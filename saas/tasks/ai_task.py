"""
tasks/ai_task.py

Background task that runs an agent automation against a Work item.
This module is imported by RQ workers — it must NOT import Flask app context.
All database access is done directly via db.get_db().

Job lifecycle:
  queued  → running  → done (status set to 'proposed' for human review)
                     → failed (status set back to 'new', error written to thread)

The function `run_work_agent` is the RQ job entry point.
"""

import json
import logging
import os
import sys
import time

# ── Path bootstrap so the worker can find sibling saas/ modules ───────────
_HERE = os.path.dirname(os.path.abspath(__file__))        # saas/tasks/
_SAAS = os.path.dirname(_HERE)                            # saas/
if _SAAS not in sys.path:
    sys.path.insert(0, _SAAS)

import db
import automation_registry
import orchestrator as _orch

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_work(conn, tenant_id: int, work_id: int):
    return conn.execute(
        "SELECT * FROM works WHERE id = ? AND tenant_id = ?",
        (work_id, tenant_id),
    ).fetchone()


def _append_event(conn, tenant_id: int, work_id: int, event_kind: str,
                  actor_kind: str, body: str, metadata: dict = None,
                  agent_key: str = None):
    conn.execute(
        """
        INSERT INTO work_events
            (tenant_id, work_id, event_kind, actor_kind, body, metadata_json, agent_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            work_id,
            event_kind,
            actor_kind,
            body,
            json.dumps(metadata) if metadata else None,
            agent_key,
        ),
    )


def _set_job_status(conn, work_id: int, rq_job_status: str):
    conn.execute(
        "UPDATE works SET rq_job_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (rq_job_status, work_id),
    )


def _find_automation(agent_key: str) -> dict | None:
    """Return the automation registry entry matching agent_key, or None."""
    for entry in automation_registry.AUTOMATION_REGISTRY:
        if entry.get("key") == agent_key:
            return entry
    return None


def _build_payload(work: dict) -> dict:
    """Build the orchestrator payload from a works row."""
    with db.get_db() as conn:
        client = None
        if work["client_entity_id"]:
            client = conn.execute(
                "SELECT * FROM client_entities WHERE id = ?",
                (work["client_entity_id"],),
            ).fetchone()
            client = dict(client) if client else None

        linked_task = None
        if work["linked_task_id"]:
            linked_task = conn.execute(
                "SELECT * FROM compliance_tasks WHERE id = ?",
                (work["linked_task_id"],),
            ).fetchone()
            linked_task = dict(linked_task) if linked_task else None

    return {
        "work": {
            "id": work["id"],
            "kind": work["kind"],
            "title": work["title"],
            "description": work["description"],
            "priority": work["priority"],
            "due_date": work["due_date"],
            "agent_key": work["agent_key"],
        },
        "client": client,
        "linked_task": linked_task,
    }


# ── Main job function (RQ entry point) ────────────────────────────────────

def run_work_agent(tenant_id: int, work_id: int) -> dict:
    """
    Execute the agent automation for the given Work item.

    Steps:
      1. Mark work as running (rq_job_status = 'running', status = 'in_progress')
      2. Find the automation from the registry (by work.agent_key)
      3. Submit to orchestrator (Paperclip) OR simulate locally if not configured
      4. Poll for completion (up to MAX_POLL_ATTEMPTS × POLL_INTERVAL_SECONDS)
      5. Write result as 'agent_run' event in work_events
      6. Set status = 'proposed' (awaits human review/release)

    On failure: set rq_job_status = 'failed', status = 'new', write error event.
    """
    MAX_POLL_ATTEMPTS = 40          # 40 × 15 s = 10 minutes max
    POLL_INTERVAL_SECONDS = 15

    try:
        # ── Step 1: mark running ──────────────────────────────────────────
        with db.get_db() as conn:
            work_row = _get_work(conn, tenant_id, work_id)
            if not work_row:
                raise ValueError(f"Work #{work_id} not found for tenant {tenant_id}")
            work = dict(work_row)

            conn.execute(
                """UPDATE works
                   SET rq_job_status = 'running',
                       status = 'in_progress',
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (work_id,),
            )
            _append_event(
                conn, tenant_id, work_id,
                event_kind="system",
                actor_kind="system",
                body="Agent job started — running in background.",
                agent_key=work.get("agent_key"),
            )

        logger.info("run_work_agent: tenant=%s work=%s agent_key=%s",
                    tenant_id, work_id, work.get("agent_key"))

        # ── Step 2: find automation ───────────────────────────────────────
        agent_key = work.get("agent_key") or ""
        automation = _find_automation(agent_key)
        if not automation:
            raise ValueError(
                f"No automation found for agent_key='{agent_key}'. "
                "Set work.agent_key to a key from automation_registry.AUTOMATION_REGISTRY."
            )

        # ── Step 3: submit to orchestrator ───────────────────────────────
        payload = _build_payload(work)
        # Resolve task_type from automation's task_types list
        task_type = (automation.get("task_types") or [agent_key])[0]

        orch = _orch.get_orchestrator()
        issue_id = None
        try:
            issue_id = orch.create_agent_task(tenant_id, work_id, task_type, payload)
            logger.info("run_work_agent: created orchestrator issue %s", issue_id)
        except ValueError as exc:
            # Orchestrator not configured for this tenant (e.g. no Paperclip company_id).
            # Fall back to a local simulation so the job still completes.
            logger.warning(
                "run_work_agent: orchestrator unavailable (%s) — using local simulation",
                exc,
            )
            issue_id = None

        # ── Step 4: poll for completion OR simulate ───────────────────────
        result_body = None
        result_meta = {}

        if issue_id:
            # Poll the external orchestrator
            for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
                time.sleep(POLL_INTERVAL_SECONDS)
                try:
                    task_info = orch.get_agent_task(issue_id)
                    state = (task_info or {}).get("state") or ""
                    logger.debug(
                        "run_work_agent: poll attempt %d, state=%s", attempt, state
                    )
                    if state in ("done", "completed", "closed", "resolved"):
                        comments = orch.get_agent_comments(issue_id) or []
                        result_body = "\n\n".join(
                            c.get("body") or "" for c in comments if c.get("body")
                        ) or f"Agent completed (no output comments). Issue: {issue_id}"
                        result_meta = {"issue_id": issue_id, "state": state}
                        break
                    if state in ("failed", "error", "cancelled"):
                        raise RuntimeError(
                            f"Orchestrator task {issue_id} ended with state={state}"
                        )
                except RuntimeError:
                    raise
                except Exception as poll_exc:  # noqa: BLE001
                    logger.warning("run_work_agent: poll error attempt %d: %s", attempt, poll_exc)
            else:
                raise TimeoutError(
                    f"Agent task {issue_id} did not complete within "
                    f"{MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS} seconds."
                )
        else:
            # Local simulation: produce a structured output stub
            time.sleep(2)  # Minimal delay so it feels like real work
            result_body = (
                f"**{automation['name']}** completed (local simulation).\n\n"
                f"Agent: {automation.get('assigned_agent', 'Unknown')}\n"
                f"Category: {automation.get('category', '—')}\n"
                f"Output type: {automation.get('output_type', '—')}\n\n"
                "_(Paperclip AI backend not configured for this tenant. "
                "Connect it via Settings → Credentials to get real AI output.)_"
            )
            result_meta = {"simulated": True, "automation_key": agent_key}

        # ── Step 5 & 6: write result + advance status ─────────────────────
        confidence = 0.75 if not result_meta.get("simulated") else 0.0

        with db.get_db() as conn:
            _append_event(
                conn, tenant_id, work_id,
                event_kind="agent_run",
                actor_kind="agent",
                body=result_body,
                metadata=result_meta,
                agent_key=agent_key,
            )
            conn.execute(
                """UPDATE works
                   SET status = 'proposed',
                       rq_job_status = 'done',
                       agent_confidence = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (confidence, work_id),
            )

        logger.info("run_work_agent: work #%d completed, status=proposed", work_id)
        return {"ok": True, "work_id": work_id, "issue_id": issue_id}

    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        logger.error("run_work_agent: FAILED work #%d — %s", work_id, error_msg, exc_info=True)
        try:
            with db.get_db() as conn:
                _set_job_status(conn, work_id, "failed")
                conn.execute(
                    "UPDATE works SET status = 'new' WHERE id = ?", (work_id,)
                )
                _append_event(
                    conn, tenant_id, work_id,
                    event_kind="system",
                    actor_kind="system",
                    body=f"Agent job failed: {error_msg}",
                )
        except Exception as db_exc:  # noqa: BLE001
            logger.error("run_work_agent: could not write failure event: %s", db_exc)
        raise

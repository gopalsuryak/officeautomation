"""
automation_service.py — AI Automation Center service layer (Wave 13)

Provides read-only aggregation and query functions for the AI Automation
Center admin page.  All queries are tenant-scoped; no Paperclip IDs are
exposed to callers — the presence of a Paperclip reference is returned as a
boolean only.
"""

import db
from orchestrator import get_orchestrator
import automation_registry


# ── status groups ─────────────────────────────────────────────────────────

_AI_STATUSES = {
    "ai_queued",
    "ai_processing",
    "ai_failed",
    "ai_draft_ready",
    "under_review",
}

# Tasks eligible for retry in the Automation Center.
RETRY_ELIGIBLE_STATUSES = {"ai_failed", "ready_for_ai", "changes_required"}

# Tasks that can have their AI result refreshed (sync from Paperclip).
SYNC_ELIGIBLE_STATUSES = {
    "ai_queued",
    "ai_processing",
    "ai_failed",
    "ai_draft_ready",
    "under_review",
    "ready_for_ai",
    "pending_documents",
}


# ── summary ───────────────────────────────────────────────────────────────

def get_ai_automation_summary(tenant_id: int) -> dict:
    """
    Return KPI counts for the AI Automation Center header cards.

    Returns a dict with:
      queued_jobs, processing_jobs, failed_jobs, draft_ready_jobs,
      under_review_jobs, tasks_with_paperclip_ref,
      latest_ai_sync_at, latest_ai_dispatch_at
    """
    result = {
        "queued_jobs": 0,
        "processing_jobs": 0,
        "failed_jobs": 0,
        "draft_ready_jobs": 0,
        "under_review_jobs": 0,
        "tasks_with_paperclip_ref": 0,
        "latest_ai_sync_at": None,
        "latest_ai_dispatch_at": None,
    }

    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND status IN ('ai_queued','ai_processing','ai_failed','ai_draft_ready','under_review')
            GROUP BY status
            """,
            (tenant_id,),
        ).fetchall()
        for row in rows:
            status = row["status"]
            if status == "ai_queued":
                result["queued_jobs"] = row["cnt"]
            elif status == "ai_processing":
                result["processing_jobs"] = row["cnt"]
            elif status == "ai_failed":
                result["failed_jobs"] = row["cnt"]
            elif status == "ai_draft_ready":
                result["draft_ready_jobs"] = row["cnt"]
            elif status == "under_review":
                result["under_review_jobs"] = row["cnt"]

        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM compliance_tasks
            WHERE tenant_id = ? AND paperclip_issue_id IS NOT NULL
            """,
            (tenant_id,),
        ).fetchone()
        result["tasks_with_paperclip_ref"] = row["cnt"] if row else 0

        row = conn.execute(
            """
            SELECT MAX(created_at) AS latest
            FROM ai_outputs
            WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
        result["latest_ai_sync_at"] = row["latest"] if row else None

        row = conn.execute(
            """
            SELECT MAX(updated_at) AS latest
            FROM compliance_tasks
            WHERE tenant_id = ? AND paperclip_issue_id IS NOT NULL
            """,
            (tenant_id,),
        ).fetchone()
        result["latest_ai_dispatch_at"] = row["latest"] if row else None

    return result


# ── job list ──────────────────────────────────────────────────────────────

def list_ai_jobs(tenant_id: int, filters: dict | None = None) -> list[dict]:
    """
    Return compliance_tasks joined with client_entities and latest ai_output.

    Paperclip issue IDs are never returned; presence is exposed as a boolean
    field ``has_paperclip_ref``.

    filters may contain:
      status, client_entity_id, task_type, confidence, has_ai_output, search
    """
    filters = filters or {}

    where = ["ct.tenant_id = ?"]
    params: list = [tenant_id]

    if filters.get("status"):
        where.append("ct.status = ?")
        params.append(filters["status"])

    if filters.get("client_entity_id"):
        try:
            ceid = int(filters["client_entity_id"])
            where.append("ct.client_entity_id = ?")
            params.append(ceid)
        except (TypeError, ValueError):
            pass  # ignore un-parseable value; skip the filter entirely

    if filters.get("task_type"):
        where.append("ct.task_type = ?")
        params.append(filters["task_type"])

    has_ai_filter = filters.get("has_ai_output")
    if has_ai_filter == "yes":
        where.append("ao.id IS NOT NULL")
    elif has_ai_filter == "no":
        where.append("ao.id IS NULL")

    # confidence is only meaningful when there IS an AI output; skip this
    # filter when has_ai_output=no to avoid an always-empty result set.
    if filters.get("confidence") and has_ai_filter != "no":
        where.append("ao.confidence = ?")
        params.append(filters["confidence"])

    if filters.get("search"):
        search_term = f"%{filters['search']}%"
        where.append(
            "(ct.title LIKE ? OR ce.name LIKE ? OR ct.task_type LIKE ?)"
        )
        params.extend([search_term, search_term, search_term])

    sql = f"""
        SELECT
            ct.id                           AS task_id,
            ce.name                         AS client_name,
            ct.title                        AS task_title,
            ct.task_type,
            ct.period,
            ct.status,
            ct.pending_from,
            ct.priority,
            ct.due_date,
            CASE WHEN ct.paperclip_issue_id IS NOT NULL THEN 1 ELSE 0 END
                                            AS has_paperclip_ref,
            ct.updated_at,
            ao.id                           AS latest_ai_output_id,
            ao.status_recommendation        AS latest_ai_status_recommendation,
            ao.confidence                   AS latest_ai_confidence,
            ao.created_at                   AS latest_ai_created_at
        FROM compliance_tasks ct
        LEFT JOIN client_entities ce
               ON ce.id = ct.client_entity_id AND ce.tenant_id = ct.tenant_id
        LEFT JOIN (
            SELECT task_id, id, status_recommendation, confidence, created_at,
                   ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY created_at DESC) AS rn
            FROM ai_outputs
            WHERE tenant_id = ?
        ) ao ON ao.task_id = ct.id AND ao.rn = 1
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE ct.status
                WHEN 'ai_failed'        THEN 1
                WHEN 'ai_queued'        THEN 2
                WHEN 'ai_processing'    THEN 3
                WHEN 'ai_draft_ready'   THEN 4
                WHEN 'under_review'     THEN 5
                ELSE 6
            END,
            ct.updated_at DESC
        LIMIT 200
    """

    # tenant_id is needed for both the sub-query and the main WHERE clause.
    all_params = [tenant_id] + params

    with db.get_db() as conn:
        rows = conn.execute(sql, all_params).fetchall()

    jobs = [dict(row) for row in rows]
    for job in jobs:
        job["assigned_agent"] = (
            automation_registry.get_default_agent_for_task_type(job.get("task_type"))
            or "Unassigned"
        )

    return jobs


# ── connection health ─────────────────────────────────────────────────────

def get_ai_connection_health() -> dict:
    """
    Return a lightweight status dict for the AI background worker connection.

    Never raises; always returns a safe dict with at least:
      { "status": "connected"|"unavailable"|"unknown", "message": str }
    """
    try:
        orchestrator = get_orchestrator()

        # Use a harmless attribute probe to confirm the orchestrator is wired.
        if hasattr(orchestrator, "get_agent_task"):
            return {
                "status": "connected",
                "message": "AI background worker is configured and ready.",
            }

        return {
            "status": "unknown",
            "message": "Connection check not available yet.",
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unavailable",
            "message": f"Could not reach AI background worker: {exc}",
        }

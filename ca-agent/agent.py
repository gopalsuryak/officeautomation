#!/usr/bin/env python3
"""
CA Firm Agent — Paperclip CLI adapter entrypoint.

Paperclip invokes this script on every heartbeat with PAPERCLIP_* env vars set.
The agent:
  1. Reads context from env vars
  2. Fetches the assigned issue from Paperclip
  3. Builds a prompt using CA domain knowledge
  4. Calls the LLM for a response
  5. Posts a comment and marks the issue done (or blocked if something is missing)

Register this as a CLI agent in Paperclip:
  Adapter type : cli
  Command      : python agent.py
  Working dir  : <path to this folder>
"""

import json
import sys
import traceback

from paperclip_client import PaperclipClient
from ca_knowledge import build_structured_system_prompt, classify_task, upcoming_due_dates
from llm_client import complete
from output_schema import (
    empty_structured_output,
    extract_json_object,
    normalize_structured_output,
    structured_output_to_markdown,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    """Paperclip captures stdout; prefix lines so they appear in the run log."""
    print(f"[ca-agent] {msg}", flush=True)


def extract_ca_assist_context(issue: dict) -> dict | None:
    description = issue.get("description", "") or ""
    try:
        data = extract_json_object(description)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("source") != "ca_assist":
        return None
    return data


def build_task_prompt(issue: dict, comments: list[dict], client: PaperclipClient) -> str:
    title = issue.get("title", "(no title)")
    description = issue.get("description", "") or ""
    status = issue.get("status", "")
    priority = issue.get("priority", "")
    identifier = issue.get("identifier", issue.get("id", ""))

    # Build comment thread
    comment_thread = ""
    if comments:
        lines = []
        for c in comments[-10:]:   # last 10 comments for context
            author = c.get("authorType", "unknown")
            body = c.get("body", "")
            lines.append(f"**{author}**: {body}")
        comment_thread = "\n\n### Previous comments\n" + "\n\n".join(lines)

    # Domain tags
    tags = classify_task(title, description)
    tag_hint = f"Domain classification: {', '.join(tags)}"

    # Wake reason
    wake_reason = client.wake_reason or "scheduled heartbeat"

    prompt = f"""## Task: {identifier} — {title}

**Status**: {status}  
**Priority**: {priority}  
**Wake reason**: {wake_reason}  
**{tag_hint}**

### Description
{description or "(no description provided)"}
{comment_thread}

### Your job
Return ONLY a valid JSON object matching the required schema in the system prompt.
Identify missing documents/data precisely.
Classify risk conservatively for filing-impacting matters.
Never claim filing is complete or final.
"""
    return prompt


def _calendar_structured_output() -> dict:
    due_dates = upcoming_due_dates(45)
    if not due_dates:
        summary = "No statutory due dates in the next 45 days."
    else:
        lines = ["## Statutory Due Dates — Next 45 Days\n"]
        lines.append("| Form | Due Date | Days Left | Description |")
        lines.append("|------|----------|-----------|-------------|")
        for d in due_dates:
            lines.append(f"| **{d['form']}** | {d['due_date']} | {d['days_left']} | {d['desc']} |")
        summary = "\n".join(lines)

    return normalize_structured_output(
        {
            "status_recommendation": "draft_ready",
            "confidence": "high",
            "missing_inputs": [],
            "risk_flags": [],
            "applicable_laws": ["Statutory due date calendars based on FY 2025-26 assumptions"],
            "document_requests": [],
            "client_message_draft": "Please review upcoming compliance due dates and confirm priority filings.",
            "internal_working_note": "Generated from built-in statutory calendar helper.",
            "final_output_markdown": summary,
        }
    )


def _recommendation_to_issue_status(status_recommendation: str) -> tuple[str, str]:
    mapping = {
        "need_info": ("blocked", "Waiting for required inputs (see structured output)."),
        "draft_ready": ("done", "Structured draft prepared."),
        "review_required": ("done", "Structured draft prepared; CA review required."),
        "high_risk_review": ("blocked", "High-risk review required before further action."),
    }
    return mapping.get(status_recommendation, ("done", "Structured draft prepared."))


def _build_comment(structured: dict) -> str:
    markdown = structured_output_to_markdown(structured)
    raw_json = json.dumps(structured, ensure_ascii=False, indent=2)
    return (
        f"{markdown}\n\n"
        "---\n"
        "#### Raw JSON (for Wave 6 sync)\n"
        "```json\n"
        f"{raw_json}\n"
        "```"
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def run() -> int:
    client = PaperclipClient()

    log(f"Heartbeat received | agent={client.agent_id} | company={client.company_id} | run={client.run_id}")

    # ── 1. Resolve which issue to work on ─────────────────────────────────
    issue_id = client.task_id
    if not issue_id:
        log("No PAPERCLIP_TASK_ID — scanning assigned issues...")
        issues = client.list_my_issues()
        if not issues:
            log("No assigned issues. Nothing to do.")
            return 0
        # Prefer in_progress → in_review → todo → blocked
        for preferred_status in ("in_progress", "in_review", "todo", "blocked"):
            match = next((i for i in issues if i.get("status") == preferred_status), None)
            if match:
                issue_id = match["id"]
                log(f"Picked issue {match.get('identifier', issue_id)} (status={preferred_status})")
                break
        if not issue_id:
            log("No actionable issues found.")
            return 0

    # ── 2. Fetch issue details ─────────────────────────────────────────────
    log(f"Fetching issue {issue_id}...")
    issue = client.get_issue(issue_id)
    identifier = issue.get("identifier", issue_id)
    log(f"Working on: {identifier} — {issue.get('title', '')}")

    ca_context = extract_ca_assist_context(issue)
    if ca_context:
        log(f"Detected CA Assist context | task_id={ca_context.get('task_id')}")

    # ── 3. Checkout issue ──────────────────────────────────────────────────
    try:
        client.checkout_issue(issue_id)
        log("Issue checked out.")
    except RuntimeError as e:
        # Already checked out or wrong status — proceed anyway
        log(f"Checkout note: {e}")

    # ── 4. Get comments ────────────────────────────────────────────────────
    comments = client.get_comments(issue_id)
    log(f"Loaded {len(comments)} comment(s).")

    # ── 5. Special handlers ────────────────────────────────────────────────
    tags = classify_task(issue.get("title", ""), issue.get("description", "") or "")
    if "compliance_calendar" in tags:
        log("Handling as compliance calendar task with structured output...")
        structured = _calendar_structured_output()
        if ca_context and ca_context.get("task_id"):
            note = structured.get("internal_working_note", "")
            structured["internal_working_note"] = (
                f"{note}\nCA Assist task_id detected: {ca_context.get('task_id')}"
            ).strip()

        client.post_comment(issue_id, _build_comment(structured))
        issue_status, status_note = _recommendation_to_issue_status(structured["status_recommendation"])
        client.update_issue(issue_id, issue_status, status_note)
        return 0

    # ── 6. Build prompt and call LLM ───────────────────────────────────────
    system = build_structured_system_prompt()
    user_prompt = build_task_prompt(issue, comments, client)

    log("Calling LLM...")
    try:
        answer = complete(system, user_prompt, json_mode=True)
    except Exception as e:
        error_msg = f"LLM call failed: {e}"
        log(error_msg)
        client.post_comment(issue_id, f"⚠️ Agent encountered an error:\n\n```\n{error_msg}\n```")
        client.update_issue(issue_id, "blocked", error_msg)
        return 1

    log(f"LLM response received ({len(answer)} chars). Parsing structured JSON...")

    parse_failed = False
    try:
        parsed = extract_json_object(answer)
        structured = normalize_structured_output(parsed)
    except Exception:
        parse_failed = True
        structured = normalize_structured_output(
            empty_structured_output(
                raw_text=answer,
                reason="LLM did not return valid JSON",
            )
        )

    if ca_context and ca_context.get("task_id"):
        note = structured.get("internal_working_note", "")
        structured["internal_working_note"] = (
            f"{note}\nCA Assist task_id detected: {ca_context.get('task_id')}"
        ).strip()
        structured = normalize_structured_output(structured)

    comment_body = _build_comment(structured)
    client.post_comment(issue_id, comment_body)

    if parse_failed:
        if structured.get("missing_inputs"):
            issue_status, status_note = ("blocked", "Invalid JSON from LLM; missing inputs need clarification.")
        else:
            issue_status, status_note = ("done", "Invalid JSON from LLM; draft posted for CA review.")
    else:
        issue_status, status_note = _recommendation_to_issue_status(structured["status_recommendation"])

    client.update_issue(issue_id, issue_status, status_note)
    log(f"Issue updated with structured status mapping: {structured['status_recommendation']} -> {issue_status}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception:
        print(f"[ca-agent] FATAL ERROR:\n{traceback.format_exc()}", flush=True)
        sys.exit(1)

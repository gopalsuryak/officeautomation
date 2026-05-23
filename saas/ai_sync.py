import json
import re
from typing import Any

import db
from orchestrator import get_orchestrator

_STATUS_VALUES = {"need_info", "draft_ready", "review_required", "high_risk_review"}
_CONFIDENCE_VALUES = {"low", "medium", "high"}
_REQUIRED_KEYS = [
    "status_recommendation",
    "confidence",
    "missing_inputs",
    "risk_flags",
    "applicable_laws",
    "document_requests",
    "client_message_draft",
    "internal_working_note",
    "final_output_markdown",
]


def _flatten_comments(comments: Any) -> list[dict]:
    if isinstance(comments, dict):
        maybe = comments.get("comments")
        if isinstance(maybe, list):
            return [c for c in maybe if isinstance(c, (dict, str))]
        # Some APIs return the comment payload object itself.
        if any(k in comments for k in ("body", "content", "text", "message")):
            return [comments]
        return []

    if isinstance(comments, list):
        return [c for c in comments if isinstance(c, (dict, str))]

    return []


def _comment_body(comment: Any) -> str:
    if isinstance(comment, str):
        return comment
    if not isinstance(comment, dict):
        return ""
    for key in ("body", "content", "text", "message"):
        value = comment.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_balanced_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for idx, ch in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:idx + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    return None

    return None


def extract_latest_structured_json_from_comments(comments):
    items = _flatten_comments(comments)
    if not items:
        raise ValueError("No AI comments found yet. Please wait and try Refresh AI Result again.")

    fence_re = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)

    # Iterate newest to oldest, then parse the last fenced JSON block in that comment.
    for comment in reversed(items):
        body = _comment_body(comment)
        if "```" not in body:
            continue

        blocks = fence_re.findall(body)
        if not blocks:
            continue

        for block in reversed(blocks):
            candidate = block.strip()
            if not candidate:
                continue

            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                parsed = _extract_balanced_object(candidate)
                if parsed is not None:
                    return parsed

    raise ValueError("Structured AI JSON was not found in comments yet. Please try again shortly.")


def _to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [value]


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def normalize_ai_output_for_db(data):
    source = dict(data or {})

    normalized = {
        "status_recommendation": source.get("status_recommendation", "review_required"),
        "confidence": source.get("confidence", "low"),
        "missing_inputs": source.get("missing_inputs", []),
        "risk_flags": source.get("risk_flags", []),
        "applicable_laws": source.get("applicable_laws", []),
        "document_requests": source.get("document_requests", []),
        "client_message_draft": source.get("client_message_draft", ""),
        "internal_working_note": source.get("internal_working_note", ""),
        "final_output_markdown": source.get("final_output_markdown", ""),
    }

    for key in _REQUIRED_KEYS:
        normalized.setdefault(key, "" if key in {"client_message_draft", "internal_working_note", "final_output_markdown"} else [])

    for key in ("missing_inputs", "risk_flags", "applicable_laws", "document_requests"):
        normalized[key] = _to_list(normalized.get(key))

    for key in ("client_message_draft", "internal_working_note", "final_output_markdown"):
        normalized[key] = _to_text(normalized.get(key))

    status = _to_text(normalized.get("status_recommendation")).strip().lower()
    normalized["status_recommendation"] = status if status in _STATUS_VALUES else "review_required"

    confidence = _to_text(normalized.get("confidence")).strip().lower()
    normalized["confidence"] = confidence if confidence in _CONFIDENCE_VALUES else "low"

    return normalized


def map_ai_status_to_task_status(status_recommendation):
    mapping = {
        "need_info": "pending_documents",
        "draft_ready": "ai_draft_ready",
        "review_required": "under_review",
        "high_risk_review": "under_review",
    }
    return mapping.get((status_recommendation or "").strip().lower(), "under_review")


def insert_ai_output(
    conn,
    tenant_id,
    task_id,
    normalized,
    provider=None,
    model=None,
    prompt_version="wave5_structured",
    paperclip_comment_id=None,
):
    raw_json = json.dumps(normalized, ensure_ascii=False)
    cur = conn.execute(
        """
        INSERT INTO ai_outputs (
            tenant_id, task_id, provider, model, prompt_version,
            output_type, status_recommendation, confidence,
            missing_inputs_json, risk_flags_json, applicable_laws_json, document_requests_json,
            client_message_draft, internal_working_note, output_markdown,
            raw_json, paperclip_comment_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            task_id,
            provider,
            model,
            prompt_version,
            "structured_ca_draft",
            normalized["status_recommendation"],
            normalized["confidence"],
            json.dumps(normalized["missing_inputs"], ensure_ascii=False),
            json.dumps(normalized["risk_flags"], ensure_ascii=False),
            json.dumps(normalized["applicable_laws"], ensure_ascii=False),
            json.dumps(normalized["document_requests"], ensure_ascii=False),
            normalized["client_message_draft"],
            normalized["internal_working_note"],
            normalized["final_output_markdown"],
            raw_json,
            str(paperclip_comment_id) if paperclip_comment_id is not None else None,
        ),
    )
    return cur.lastrowid


def create_document_requests_from_ai(
    conn,
    tenant_id,
    task_id,
    client_entity_id,
    document_requests,
    user_id=None,
):
    created = 0

    for item in _to_list(document_requests):
        document_name = ""
        description = None

        if isinstance(item, str):
            document_name = item.strip()
        elif isinstance(item, dict):
            document_name = _to_text(
                item.get("document_name") or item.get("name") or item.get("title")
            ).strip()
            description = _to_text(item.get("description") or item.get("notes") or "").strip() or None
        else:
            document_name = _to_text(item).strip()

        if not document_name:
            continue

        exists = conn.execute(
            """
            SELECT id FROM document_requests
            WHERE tenant_id = ? AND task_id = ? AND LOWER(document_name) = LOWER(?)
            LIMIT 1
            """,
            (tenant_id, task_id, document_name),
        ).fetchone()
        if exists:
            continue

        conn.execute(
            """
            INSERT INTO document_requests (
                tenant_id, client_entity_id, task_id, document_name, description,
                requested_from, status
            ) VALUES (?, ?, ?, ?, ?, 'client', 'requested')
            """,
            (tenant_id, client_entity_id, task_id, document_name, description),
        )
        created += 1

    return created


def sync_paperclip_result_for_task(tenant_id, task_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        task = conn.execute(
            """
            SELECT * FROM compliance_tasks
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, task_id),
        ).fetchone()

    if not task:
        raise ValueError("Task not found for this tenant.")

    paperclip_issue_id = task["paperclip_issue_id"]
    if not paperclip_issue_id:
        raise ValueError("This task has not been sent to AI yet.")

    try:
        comments = get_orchestrator().get_agent_comments(paperclip_issue_id)
    except Exception as exc:
        raise ValueError("Could not fetch AI comments yet. Please try again shortly.") from exc

    structured = extract_latest_structured_json_from_comments(comments)
    normalized = normalize_ai_output_for_db(structured)

    old_status = task["status"]
    new_status = map_ai_status_to_task_status(normalized["status_recommendation"])
    pending_from_map = {
        "pending_documents": "client",
        "ai_draft_ready": "staff",
        "under_review": "reviewer",
    }
    new_pending_from = pending_from_map.get(new_status, task["pending_from"] or "staff")

    with db.get_db() as conn:
        ai_output_id = insert_ai_output(
            conn=conn,
            tenant_id=tenant_id,
            task_id=task_id,
            normalized=normalized,
            provider=_to_text(structured.get("provider") if isinstance(structured, dict) else "") or None,
            model=_to_text(structured.get("model") if isinstance(structured, dict) else "") or None,
            prompt_version="wave5_structured",
            paperclip_comment_id=None,
        )

        document_requests_created = create_document_requests_from_ai(
            conn=conn,
            tenant_id=tenant_id,
            task_id=task_id,
            client_entity_id=task["client_entity_id"],
            document_requests=normalized["document_requests"],
            user_id=user_id,
        )

        conn.execute(
            """
            UPDATE compliance_tasks
            SET status = ?, pending_from = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (new_status, new_pending_from, tenant_id, task_id),
        )
        db.touch_updated_at(conn, "compliance_tasks", task_id)

        conn.execute(
            """
            INSERT INTO task_status_history
                (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, task_id, old_status, new_status, user_id, "AI result synced from background worker"),
        )

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (tenant_id, task_id, user_id, "AI result synced from background worker."),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="ai_result_synced",
            entity_type="compliance_task",
            entity_id=task_id,
            old_value={"status": old_status, "pending_from": task["pending_from"]},
            new_value={
                "status": new_status,
                "pending_from": new_pending_from,
                "ai_output_id": ai_output_id,
                "document_requests_created": document_requests_created,
            },
            metadata={"status_recommendation": normalized["status_recommendation"]},
            ip_address=ip_address,
        )

    return {
        "ai_output_id": ai_output_id,
        "new_status": new_status,
        "document_requests_created": document_requests_created,
    }

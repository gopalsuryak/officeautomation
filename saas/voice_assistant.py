import re
from datetime import date
from typing import Any
from urllib.parse import urlencode

import db
import compliance_tasks
import document_workflow

SUPPORTED_INTENTS = [
    "create_task",
    "search_tasks",
    "open_client",
    "open_portal_readiness",
    "open_ai_automation",
    "open_accounting_data",
    "create_document_request",
    "unknown",
]


def _unknown_result() -> dict[str, Any]:
    return {
        "intent": "unknown",
        "confidence": "low",
        "needs_confirmation": False,
        "action_label": "Unknown command",
        "parameters": {},
        "message": "I could not understand this command. Please try again or use the normal menu.",
    }


def _normalise_command_text(command_text: str) -> str:
    return re.sub(r"\s+", " ", (command_text or "").strip()).lower()


def _task_type_from_phrase(task_phrase: str) -> str | None:
    text = (task_phrase or "").strip().lower().replace("-", "").replace(" ", "")
    if text == "gstr3b":
        return "gstr3b"
    if text == "tds":
        return "tds_24q"
    if text == "itr":
        return "itr"
    return None


def parse_voice_command(tenant_id, command_text):
    del tenant_id
    clean = _normalise_command_text(command_text)
    if not clean:
        return _unknown_result()

    create_task_match = re.match(
        r"^create\s+(gstr[\s\-]*3b|tds|itr)\s+task\s+for\s+(.+?)(?:\s+for\s+(.+))?$",
        clean,
    )
    if create_task_match:
        task_type = _task_type_from_phrase(create_task_match.group(1))
        client_search = (create_task_match.group(2) or "").strip()
        period = (create_task_match.group(3) or "").strip()
        return {
            "intent": "create_task",
            "confidence": "medium",
            "needs_confirmation": True,
            "action_label": "Create compliance task",
            "parameters": {
                "client_search": client_search,
                "task_type": task_type,
                "period": period or None,
            },
            "message": "I found a possible task creation command.",
        }

    if "show overdue tasks" in clean:
        return {
            "intent": "search_tasks",
            "confidence": "high",
            "needs_confirmation": True,
            "action_label": "Open overdue tasks",
            "parameters": {"task_filter": "overdue"},
            "message": "I found a task search command.",
        }

    if "show pending document tasks" in clean:
        return {
            "intent": "search_tasks",
            "confidence": "high",
            "needs_confirmation": True,
            "action_label": "Open pending document tasks",
            "parameters": {"task_filter": "pending_documents"},
            "message": "I found a task search command.",
        }

    if "show tasks under review" in clean:
        return {
            "intent": "search_tasks",
            "confidence": "high",
            "needs_confirmation": True,
            "action_label": "Open tasks under review",
            "parameters": {"task_filter": "under_review"},
            "message": "I found a task search command.",
        }

    open_client_match = re.match(r"^open\s+client\s+(.+)$", clean)
    if open_client_match:
        return {
            "intent": "open_client",
            "confidence": "medium",
            "needs_confirmation": True,
            "action_label": "Open client",
            "parameters": {
                "client_search": (open_client_match.group(1) or "").strip(),
            },
            "message": "I found a client navigation command.",
        }

    if "open ai automation" in clean:
        return {
            "intent": "open_ai_automation",
            "confidence": "high",
            "needs_confirmation": True,
            "action_label": "Open AI Automation",
            "parameters": {},
            "message": "I found a navigation command.",
        }

    if "open portal readiness" in clean:
        return {
            "intent": "open_portal_readiness",
            "confidence": "high",
            "needs_confirmation": True,
            "action_label": "Open Portal Readiness",
            "parameters": {},
            "message": "I found a navigation command.",
        }

    if "open accounting data" in clean:
        return {
            "intent": "open_accounting_data",
            "confidence": "high",
            "needs_confirmation": True,
            "action_label": "Open Accounting Data",
            "parameters": {},
            "message": "I found a navigation command.",
        }

    ask_match = re.match(r"^ask\s+(.+?)\s+for\s+(.+)$", clean)
    if ask_match:
        return {
            "intent": "create_document_request",
            "confidence": "medium",
            "needs_confirmation": True,
            "action_label": "Create document request",
            "parameters": {
                "client_search": (ask_match.group(1) or "").strip(),
                "document_name": (ask_match.group(2) or "").strip(),
            },
            "message": "I found a possible document request command.",
        }

    request_match = re.match(r"^request\s+(.+?)\s+from\s+(.+)$", clean)
    if request_match:
        return {
            "intent": "create_document_request",
            "confidence": "medium",
            "needs_confirmation": True,
            "action_label": "Create document request",
            "parameters": {
                "document_name": (request_match.group(1) or "").strip(),
                "client_search": (request_match.group(2) or "").strip(),
            },
            "message": "I found a possible document request command.",
        }

    return _unknown_result()


def _find_client_candidates(tenant_id: int, client_search: str) -> list[dict[str, Any]]:
    term = (client_search or "").strip()
    if not term:
        return []

    like = f"%{term}%"
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, name, legal_name, status
            FROM client_entities
            WHERE tenant_id = ?
              AND status = 'active'
              AND (
                    name LIKE ?
                 OR legal_name LIKE ?
              )
            ORDER BY
                CASE
                    WHEN LOWER(name) = LOWER(?) THEN 0
                    WHEN LOWER(name) LIKE LOWER(?) THEN 1
                    WHEN LOWER(legal_name) = LOWER(?) THEN 2
                    ELSE 3
                END,
                name COLLATE NOCASE ASC
            LIMIT 6
            """,
            (tenant_id, like, like, term, f"{term}%", term),
        ).fetchall()

    return [dict(row) for row in rows]


def _task_search_redirect(task_filter: str | None) -> str:
    target_filter = (task_filter or "").strip().lower()
    if target_filter == "pending_documents":
        return "/tasks?" + urlencode({"status": "pending_documents"})
    if target_filter == "under_review":
        return "/tasks?" + urlencode({"status": "under_review"})
    if target_filter == "overdue":
        return "/tasks?" + urlencode({"due_before": date.today().isoformat()})
    return "/tasks"


def resolve_command_preview(tenant_id, parsed):
    preview = dict(parsed or {})
    intent = (preview.get("intent") or "unknown").strip().lower()
    parameters = dict(preview.get("parameters") or {})
    preview["parameters"] = parameters

    if intent in {"create_task", "open_client", "create_document_request"}:
        candidates = _find_client_candidates(tenant_id, parameters.get("client_search", ""))
        if len(candidates) == 1:
            chosen = candidates[0]
            parameters["client_entity_id"] = chosen["id"]
            parameters["client_name"] = chosen["name"]
            preview["client_match"] = {
                "client_entity_id": chosen["id"],
                "client_name": chosen["name"],
            }
        elif len(candidates) > 1:
            preview["client_candidates"] = [
                {
                    "client_entity_id": row["id"],
                    "client_name": row["name"],
                }
                for row in candidates
            ]
            preview["message"] = "Multiple clients matched. Please choose the correct client before confirming."

    if intent == "open_client" and parameters.get("client_entity_id"):
        preview["redirect_url"] = f"/clients/{parameters['client_entity_id']}"

    if intent == "open_ai_automation":
        preview["redirect_url"] = "/automation"

    if intent == "open_portal_readiness":
        preview["redirect_url"] = "/portal-readiness"

    if intent == "open_accounting_data":
        preview["redirect_url"] = "/accounting-data"

    if intent == "search_tasks":
        preview["redirect_url"] = _task_search_redirect(parameters.get("task_filter"))

    if intent == "create_document_request" and not parameters.get("task_id"):
        preview["warning"] = "Task context is required to create a document request in Phase 1."

    return preview


def _resolve_client_name(tenant_id: int, client_entity_id: int) -> str | None:
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT name
            FROM client_entities
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, client_entity_id),
        ).fetchone()
    return row["name"] if row else None


def execute_confirmed_command(tenant_id, parsed, user_id=None, ip_address=None):
    command = dict(parsed or {})
    intent = (command.get("intent") or "unknown").strip().lower()
    parameters = dict(command.get("parameters") or {})

    if intent not in SUPPORTED_INTENTS or intent == "unknown":
        return {
            "success": False,
            "message": "Unknown or unsupported command.",
        }

    if intent == "open_ai_automation":
        return {"success": True, "redirect_url": "/automation", "message": "Opening AI Automation."}

    if intent == "open_portal_readiness":
        return {"success": True, "redirect_url": "/portal-readiness", "message": "Opening Portal Readiness."}

    if intent == "open_accounting_data":
        return {"success": True, "redirect_url": "/accounting-data", "message": "Opening Accounting Data."}

    if intent == "search_tasks":
        return {
            "success": True,
            "redirect_url": _task_search_redirect(parameters.get("task_filter")),
            "message": "Opening filtered tasks.",
        }

    if intent == "open_client":
        client_entity_id = parameters.get("client_entity_id")
        if not client_entity_id:
            return {
                "success": False,
                "message": "Client is not resolved. Please select a single client match first.",
            }
        return {
            "success": True,
            "redirect_url": f"/clients/{int(client_entity_id)}",
            "message": "Opening client profile.",
        }

    if intent == "create_task":
        client_entity_id = parameters.get("client_entity_id")
        task_type = (parameters.get("task_type") or "").strip().lower()
        period = (parameters.get("period") or "").strip() or None

        if not client_entity_id:
            return {
                "success": False,
                "message": "Client is not resolved. Please select a single client match first.",
            }

        if task_type not in compliance_tasks.TASK_TYPES:
            return {
                "success": False,
                "message": "Unsupported task type for Phase 1 voice assistant.",
            }

        resolved_client_name = parameters.get("client_name") or _resolve_client_name(tenant_id, int(client_entity_id))
        if not resolved_client_name:
            return {
                "success": False,
                "message": "Selected client is not available for this tenant.",
            }

        task_label = compliance_tasks.TASK_TYPE_LABELS.get(task_type, task_type.upper())
        title = f"{task_label} task for {resolved_client_name}"
        if period:
            title = f"{title} - {period}"

        created = compliance_tasks.create_compliance_task(
            tenant_id=tenant_id,
            data={
                "client_entity_id": int(client_entity_id),
                "task_type": task_type,
                "title": title,
                "description": "Created from Jarvis Assistant confirmation.",
                "period": period,
                "priority": "normal",
            },
            user_id=user_id,
            ip_address=ip_address,
        )

        with db.get_db() as conn:
            db.log_audit(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="voice_command_task_created",
                entity_type="compliance_task",
                entity_id=created["id"],
                old_value=None,
                new_value={
                    "task_id": created["id"],
                    "task_type": task_type,
                    "client_entity_id": int(client_entity_id),
                    "period": period,
                },
                metadata={"intent": intent},
                ip_address=ip_address,
            )

        return {
            "success": True,
            "redirect_url": f"/tasks/{created['id']}",
            "message": "Task created.",
        }

    if intent == "create_document_request":
        task_id = parameters.get("task_id")
        document_name = (parameters.get("document_name") or "").strip()
        if not task_id:
            return {
                "success": False,
                "message": "Task context is required before creating a document request in Phase 1.",
            }
        if not document_name:
            return {
                "success": False,
                "message": "Document name is required.",
            }

        created = document_workflow.add_document_request(
            tenant_id=tenant_id,
            task_id=int(task_id),
            document_name=document_name,
            user_id=user_id,
            ip_address=ip_address,
        )
        if not created:
            return {
                "success": False,
                "message": "Task not found.",
            }

        with db.get_db() as conn:
            db.log_audit(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="voice_command_document_request_created",
                entity_type="document_request",
                entity_id=created["id"],
                old_value=None,
                new_value={
                    "task_id": int(task_id),
                    "document_name": document_name,
                },
                metadata={"intent": intent},
                ip_address=ip_address,
            )

        return {
            "success": True,
            "redirect_url": f"/tasks/{int(task_id)}",
            "message": "Document request created.",
        }

    return {
        "success": False,
        "message": "Unsupported command in Phase 1.",
    }

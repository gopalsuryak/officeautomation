from datetime import date, timedelta
from typing import Any

import db

TASK_TYPES = [
    "gstr1",
    "gstr3b",
    "gstr9",
    "tds_24q",
    "tds_26q",
    "tds_certificate",
    "itr",
    "tax_audit",
    "aoc4",
    "mgt7",
    "dir3kyc",
    "pf_esi",
    "advance_tax",
    "document_checklist",
    "general_query",
]

TASK_TYPE_LABELS = {
    "gstr1": "GSTR-1",
    "gstr3b": "GSTR-3B",
    "gstr9": "GSTR-9",
    "tds_24q": "TDS 24Q",
    "tds_26q": "TDS 26Q",
    "tds_certificate": "TDS Certificate",
    "itr": "Income Tax Return",
    "tax_audit": "Tax Audit",
    "aoc4": "AOC-4",
    "mgt7": "MGT-7",
    "dir3kyc": "DIR-3 KYC",
    "pf_esi": "PF / ESI",
    "advance_tax": "Advance Tax",
    "document_checklist": "Document Checklist",
    "general_query": "General Query",
}

STATUS_LABELS = {
    "draft": "Draft",
    "pending_documents": "Pending Documents",
    "ready_for_ai": "Ready for AI",
    "ai_queued": "AI Queued",
    "ai_processing": "AI Processing",
    "ai_draft_ready": "AI Draft Ready",
    "under_review": "Under Review",
    "changes_required": "Changes Required",
    "approved": "Approved",
    "filed": "Filed",
    "closed": "Closed",
    "cancelled": "Cancelled",
    "ai_failed": "AI Failed",
}

ALLOWED_TRANSITIONS = {
    "draft": ["pending_documents", "ready_for_ai", "cancelled"],
    "pending_documents": ["ready_for_ai", "cancelled"],
    "ready_for_ai": ["ai_queued", "under_review", "cancelled"],
    "ai_queued": ["ai_processing", "ai_failed", "cancelled"],
    "ai_processing": ["ai_draft_ready", "pending_documents", "ai_failed"],
    "ai_draft_ready": ["under_review", "changes_required", "cancelled"],
    "under_review": ["approved", "changes_required", "cancelled"],
    "changes_required": ["ready_for_ai", "under_review", "cancelled"],
    "approved": ["filed", "closed"],
    "filed": ["closed"],
    "ai_failed": ["ready_for_ai", "cancelled"],
    "cancelled": [],
    "closed": [],
}

_ALLOWED_EDIT_FIELDS = [
    "title",
    "description",
    "period",
    "financial_year",
    "due_date",
    "priority",
    "assigned_user_id",
    "reviewer_user_id",
]


def _clean_text(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return value


def _normalise_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = {k: _clean_text(v) for k, v in data.items()}
    if payload.get("task_type"):
        payload["task_type"] = str(payload["task_type"]).strip().lower()
    if payload.get("priority"):
        payload["priority"] = str(payload["priority"]).strip().lower()
    return payload


def _client_belongs_to_tenant(conn, tenant_id: int, client_entity_id: int):
    return conn.execute(
        """
        SELECT id, name FROM client_entities
        WHERE id = ? AND tenant_id = ?
        LIMIT 1
        """,
        (client_entity_id, tenant_id),
    ).fetchone()


def _task_belongs_to_tenant(conn, tenant_id: int, task_id: int):
    return conn.execute(
        """
        SELECT * FROM compliance_tasks
        WHERE id = ? AND tenant_id = ?
        LIMIT 1
        """,
        (task_id, tenant_id),
    ).fetchone()


def _resolve_pending_from(new_status: str, existing_pending_from: str | None) -> str:
    fixed = {
        "pending_documents": "client",
        "under_review": "reviewer",
        "approved": "none",
        "filed": "none",
        "closed": "none",
        "cancelled": "none",
        "ai_failed": "system",
        "ai_queued": "system",
        "ai_processing": "system",
        "draft": "staff",
        "changes_required": "staff",
    }
    if new_status in fixed:
        return fixed[new_status]
    return existing_pending_from or "staff"


def create_compliance_task(
    tenant_id: int,
    data: dict[str, Any],
    user_id: int | None = None,
    ip_address: str | None = None,
):
    payload = _normalise_payload(data)

    client_entity_id = payload.get("client_entity_id")
    task_type = payload.get("task_type")
    title = payload.get("title")

    if not client_entity_id:
        raise ValueError("Client is required.")
    if not task_type or task_type not in TASK_TYPES:
        raise ValueError("Please select a valid task type.")
    if not title:
        raise ValueError("Task title is required.")

    with db.get_db() as conn:
        client = _client_belongs_to_tenant(conn, tenant_id, int(client_entity_id))
        if not client:
            raise ValueError("Selected client does not belong to your tenant.")

        cur = conn.execute(
            """
            INSERT INTO compliance_tasks (
                tenant_id, client_entity_id, task_type, title, description,
                period, financial_year, due_date, status, priority,
                pending_from, assigned_user_id, reviewer_user_id, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, 'staff', ?, ?, ?)
            """,
            (
                tenant_id,
                int(client_entity_id),
                task_type,
                title,
                payload.get("description"),
                payload.get("period"),
                payload.get("financial_year"),
                payload.get("due_date"),
                payload.get("priority") or "normal",
                payload.get("assigned_user_id"),
                payload.get("reviewer_user_id"),
                user_id,
            ),
        )
        task_id = cur.lastrowid

        conn.execute(
            """
            INSERT INTO task_status_history
                (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, task_id, None, "draft", user_id, "Task created"),
        )

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (tenant_id, task_id, user_id, "Task created."),
        )

        row = conn.execute(
            "SELECT * FROM compliance_tasks WHERE id = ? AND tenant_id = ?",
            (task_id, tenant_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="task_created",
            entity_type="compliance_task",
            entity_id=task_id,
            old_value=None,
            new_value=dict(row) if row else payload,
            metadata={"client_name": client["name"]},
            ip_address=ip_address,
        )

    return row


def update_compliance_task(
    tenant_id: int,
    task_id: int,
    data: dict[str, Any],
    user_id: int | None = None,
    ip_address: str | None = None,
):
    payload = _normalise_payload(data)

    with db.get_db() as conn:
        existing = _task_belongs_to_tenant(conn, tenant_id, task_id)
        if not existing:
            return None

        updates = []
        values: list[Any] = []
        for field in _ALLOWED_EDIT_FIELDS:
            if field in payload:
                updates.append(f"{field} = ?")
                values.append(payload.get(field))

        if not updates:
            return existing

        conn.execute(
            f"""
            UPDATE compliance_tasks
            SET {', '.join(updates)}
            WHERE id = ? AND tenant_id = ?
            """,  # noqa: S608
            tuple(values + [task_id, tenant_id]),
        )
        db.touch_updated_at(conn, "compliance_tasks", task_id)

        updated = conn.execute(
            "SELECT * FROM compliance_tasks WHERE id = ? AND tenant_id = ?",
            (task_id, tenant_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="task_updated",
            entity_type="compliance_task",
            entity_id=task_id,
            old_value=dict(existing),
            new_value=dict(updated) if updated else payload,
            metadata=None,
            ip_address=ip_address,
        )

    return updated


def get_compliance_task(tenant_id: int, task_id: int):
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                t.*,
                c.name AS client_name,
                c.gstin AS client_gstin,
                c.pan AS client_pan,
                c.entity_type AS client_entity_type
            FROM compliance_tasks t
            JOIN client_entities c
              ON c.id = t.client_entity_id AND c.tenant_id = t.tenant_id
            WHERE t.tenant_id = ? AND t.id = ?
            LIMIT 1
            """,
            (tenant_id, task_id),
        ).fetchone()


def list_compliance_tasks(tenant_id: int, filters: dict[str, Any] | None = None):
    filters = filters or {}
    where = ["t.tenant_id = ?"]
    params: list[Any] = [tenant_id]

    if filters.get("client_entity_id"):
        where.append("t.client_entity_id = ?")
        params.append(filters["client_entity_id"])

    if filters.get("status"):
        where.append("t.status = ?")
        params.append(str(filters["status"]).strip().lower())

    if filters.get("task_type"):
        where.append("t.task_type = ?")
        params.append(str(filters["task_type"]).strip().lower())

    if filters.get("priority"):
        where.append("t.priority = ?")
        params.append(str(filters["priority"]).strip().lower())

    if filters.get("pending_from"):
        where.append("t.pending_from = ?")
        params.append(str(filters["pending_from"]).strip().lower())

    if filters.get("due_before"):
        where.append("date(t.due_date) <= date(?)")
        params.append(filters["due_before"])

    if filters.get("due_after"):
        where.append("date(t.due_date) >= date(?)")
        params.append(filters["due_after"])

    if filters.get("search"):
        like = f"%{str(filters['search']).strip()}%"
        where.append(
            """
            (
                t.title LIKE ? OR
                t.description LIKE ? OR
                t.period LIKE ? OR
                c.name LIKE ? OR
                c.gstin LIKE ? OR
                c.pan LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like, like])

    query = f"""
        SELECT
            t.*,
            c.name AS client_name,
            c.gstin AS client_gstin,
            c.pan AS client_pan,
            c.entity_type AS client_entity_type
        FROM compliance_tasks t
        JOIN client_entities c
          ON c.id = t.client_entity_id AND c.tenant_id = t.tenant_id
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE WHEN t.due_date IS NULL OR t.due_date = '' THEN 1 ELSE 0 END,
            date(t.due_date) ASC,
            t.created_at DESC
    """

    with db.get_db() as conn:
        return conn.execute(query, tuple(params)).fetchall()


def transition_task_status(
    tenant_id: int,
    task_id: int,
    new_status: str,
    user_id: int | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
):
    target_status = (new_status or "").strip().lower()
    if target_status not in STATUS_LABELS:
        raise ValueError("Invalid target status.")

    with db.get_db() as conn:
        existing = _task_belongs_to_tenant(conn, tenant_id, task_id)
        if not existing:
            return None

        current_status = existing["status"]
        allowed = ALLOWED_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            allowed_labels = ", ".join(allowed) if allowed else "no transitions allowed"
            raise ValueError(
                f"Invalid status transition: {current_status} -> {target_status}. Allowed: {allowed_labels}."
            )

        new_pending_from = _resolve_pending_from(target_status, existing["pending_from"])

        conn.execute(
            """
            UPDATE compliance_tasks
            SET status = ?, pending_from = ?
            WHERE id = ? AND tenant_id = ?
            """,
            (target_status, new_pending_from, task_id, tenant_id),
        )
        db.touch_updated_at(conn, "compliance_tasks", task_id)

        conn.execute(
            """
            INSERT INTO task_status_history
                (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tenant_id, task_id, current_status, target_status, user_id, reason),
        )

        comment_body = f"Status changed from {current_status} to {target_status}."
        if reason:
            comment_body += f" Reason: {reason.strip()}"

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (tenant_id, task_id, user_id, comment_body),
        )

        updated = conn.execute(
            "SELECT * FROM compliance_tasks WHERE id = ? AND tenant_id = ?",
            (task_id, tenant_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="task_status_changed",
            entity_type="compliance_task",
            entity_id=task_id,
            old_value={"status": current_status, "pending_from": existing["pending_from"]},
            new_value={"status": target_status, "pending_from": new_pending_from},
            metadata={"reason": reason},
            ip_address=ip_address,
        )

    return updated


def add_task_comment(
    tenant_id: int,
    task_id: int,
    body: str,
    user_id: int | None = None,
    comment_type: str = "user",
):
    clean_body = (body or "").strip()
    if not clean_body:
        raise ValueError("Comment cannot be empty.")

    with db.get_db() as conn:
        task = _task_belongs_to_tenant(conn, tenant_id, task_id)
        if not task:
            return None

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tenant_id, task_id, user_id, comment_type, clean_body),
        )

    return True


def list_task_comments(tenant_id: int, task_id: int):
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                tc.*,
                u.name AS user_name
            FROM task_comments tc
            LEFT JOIN users u ON u.id = tc.user_id
            WHERE tc.tenant_id = ? AND tc.task_id = ?
            ORDER BY tc.created_at DESC, tc.id DESC
            """,
            (tenant_id, task_id),
        ).fetchall()


def list_task_status_history(tenant_id: int, task_id: int):
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                h.*,
                u.name AS changed_by_name
            FROM task_status_history h
            LEFT JOIN users u ON u.id = h.changed_by_user_id
            WHERE h.tenant_id = ? AND h.task_id = ?
            ORDER BY h.created_at DESC, h.id DESC
            """,
            (tenant_id, task_id),
        ).fetchall()


def get_valid_next_statuses(current_status: str) -> list[str]:
    return ALLOWED_TRANSITIONS.get(current_status, [])


def mark_task_ai_queued(
    tenant_id: int,
    task_id: int,
    paperclip_issue_id: str,
    user_id: int | None = None,
    ip_address: str | None = None,
):
    if not paperclip_issue_id:
        raise ValueError("Paperclip issue id is required.")

    with db.get_db() as conn:
        existing = _task_belongs_to_tenant(conn, tenant_id, task_id)
        if not existing:
            return None

        old_status = existing["status"]
        allowed = ALLOWED_TRANSITIONS.get(old_status, [])
        if "ai_queued" not in allowed:
            raise ValueError(
                f"Task cannot be sent to AI from status '{old_status}'. Move it to Ready for AI first."
            )

        conn.execute(
            """
            UPDATE compliance_tasks
            SET status = 'ai_queued',
                pending_from = 'system',
                paperclip_issue_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND tenant_id = ?
            """,
            (str(paperclip_issue_id), task_id, tenant_id),
        )

        conn.execute(
            """
            INSERT INTO task_status_history
                (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
            VALUES (?, ?, ?, 'ai_queued', ?, ?)
            """,
            (tenant_id, task_id, old_status, user_id, "Dispatched to AI background worker"),
        )

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (tenant_id, task_id, user_id, "Task sent to AI background worker."),
        )

        updated = conn.execute(
            "SELECT * FROM compliance_tasks WHERE id = ? AND tenant_id = ?",
            (task_id, tenant_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="task_sent_to_ai",
            entity_type="compliance_task",
            entity_id=task_id,
            old_value={
                "status": old_status,
                "pending_from": existing["pending_from"],
                "paperclip_issue_id": existing["paperclip_issue_id"],
            },
            new_value={
                "status": "ai_queued",
                "pending_from": "system",
                "paperclip_issue_id": str(paperclip_issue_id),
            },
            metadata=None,
            ip_address=ip_address,
        )

    return updated


def get_task_summary_counts(tenant_id: int) -> dict[str, int]:
    today = date.today()
    week_end = today + timedelta(days=6)
    month_start = today.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)

    non_open_statuses = ("filed", "closed", "cancelled")

    with db.get_db() as conn:
        total_open = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status NOT IN (?, ?, ?)
            """,
            (tenant_id, *non_open_statuses),
        ).fetchone()["c"]

        due_this_week = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND due_date IS NOT NULL
              AND due_date != ''
              AND date(due_date) >= date(?)
              AND date(due_date) <= date(?)
              AND status NOT IN (?, ?, ?)
            """,
            (tenant_id, today.isoformat(), week_end.isoformat(), *non_open_statuses),
        ).fetchone()["c"]

        pending_documents = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status = 'pending_documents'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        under_review = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status = 'under_review'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        changes_required = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status = 'changes_required'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        approved = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status = 'approved'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        filed_this_month = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND status = 'filed'
              AND datetime(updated_at) >= datetime(?)
              AND datetime(updated_at) < datetime(?)
            """,
            (tenant_id, month_start.isoformat(), next_month_start.isoformat()),
        ).fetchone()["c"]

        overdue = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND due_date IS NOT NULL
              AND due_date != ''
              AND date(due_date) < date(?)
              AND status NOT IN (?, ?, ?)
            """,
            (tenant_id, today.isoformat(), *non_open_statuses),
        ).fetchone()["c"]

    return {
        "total_open": int(total_open or 0),
        "due_this_week": int(due_this_week or 0),
        "pending_documents": int(pending_documents or 0),
        "under_review": int(under_review or 0),
        "changes_required": int(changes_required or 0),
        "approved": int(approved or 0),
        "filed_this_month": int(filed_this_month or 0),
        "overdue": int(overdue or 0),
    }

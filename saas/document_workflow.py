from datetime import date

import db
import usage

DOCUMENT_STATUSES = ["requested", "received", "waived", "not_required"]

DOCUMENT_STATUS_LABELS = {
    "requested": "Requested",
    "received": "Received",
    "waived": "Waived",
    "not_required": "Not Required",
}


def _get_task(conn, tenant_id: int, task_id: int):
    return conn.execute(
        """
        SELECT * FROM compliance_tasks
        WHERE tenant_id = ? AND id = ?
        LIMIT 1
        """,
        (tenant_id, task_id),
    ).fetchone()


def add_document_request(
    tenant_id,
    task_id,
    document_name,
    description=None,
    requested_from="client",
    user_id=None,
    ip_address=None,
):
    clean_name = (document_name or "").strip()
    if not clean_name:
        raise ValueError("Document name is required.")

    clean_description = (description or "").strip() or None
    clean_requested_from = (requested_from or "client").strip().lower() or "client"

    with db.get_db() as conn:
        task = _get_task(conn, tenant_id, task_id)
        if not task:
            return None

        duplicate = conn.execute(
            """
            SELECT id
            FROM document_requests
            WHERE tenant_id = ?
              AND task_id = ?
              AND LOWER(document_name) = LOWER(?)
              AND status = 'requested'
            LIMIT 1
            """,
            (tenant_id, task_id, clean_name),
        ).fetchone()
        if duplicate:
            raise ValueError("An active request for this document already exists.")

        usage.increment_document_request_usage(tenant_id=tenant_id, amount=1, conn=conn)

        cur = conn.execute(
            """
            INSERT INTO document_requests (
                tenant_id, client_entity_id, task_id, document_name,
                description, requested_from, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'requested')
            """,
            (
                tenant_id,
                task["client_entity_id"],
                task_id,
                clean_name,
                clean_description,
                clean_requested_from,
            ),
        )
        request_id = cur.lastrowid

        old_status = task["status"]
        if old_status in {"draft", "ready_for_ai", "ai_draft_ready", "under_review", "changes_required"}:
            conn.execute(
                """
                UPDATE compliance_tasks
                SET status = 'pending_documents', pending_from = 'client'
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, task_id),
            )
            db.touch_updated_at(conn, "compliance_tasks", task_id)

            conn.execute(
                """
                INSERT INTO task_status_history
                    (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
                VALUES (?, ?, ?, 'pending_documents', ?, ?)
                """,
                (tenant_id, task_id, old_status, user_id, f"Document requested: {clean_name}"),
            )

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (tenant_id, task_id, user_id, f"Document requested: {clean_name}"),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="document_request_created",
            entity_type="document_request",
            entity_id=request_id,
            old_value=None,
            new_value={
                "task_id": task_id,
                "document_name": clean_name,
                "status": "requested",
                "requested_from": clean_requested_from,
            },
            metadata={"description": clean_description},
            ip_address=ip_address,
        )

        created = conn.execute(
            """
            SELECT *
            FROM document_requests
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, request_id),
        ).fetchone()

    return created


def update_document_request_status(
    tenant_id,
    request_id,
    new_status,
    user_id=None,
    note=None,
    ip_address=None,
):
    target_status = (new_status or "").strip().lower()
    if target_status not in DOCUMENT_STATUSES:
        raise ValueError("Invalid document status.")

    note_text = (note or "").strip()

    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT dr.*, t.status AS task_status, t.pending_from AS task_pending_from
            FROM document_requests dr
            JOIN compliance_tasks t ON t.id = dr.task_id AND t.tenant_id = dr.tenant_id
            WHERE dr.tenant_id = ? AND dr.id = ?
            LIMIT 1
            """,
            (tenant_id, request_id),
        ).fetchone()
        if not row:
            return None

        old_status = row["status"]
        existing_notes = (row["notes"] or "").strip()
        merged_notes = existing_notes
        if note_text:
            merged_notes = f"{existing_notes}\n{note_text}".strip() if existing_notes else note_text

        received_at_value = row["received_at"]
        if target_status == "received":
            conn.execute(
                """
                UPDATE document_requests
                SET status = ?, received_at = CURRENT_TIMESTAMP, notes = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (target_status, merged_notes or None, tenant_id, request_id),
            )
        else:
            conn.execute(
                """
                UPDATE document_requests
                SET status = ?, notes = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (target_status, merged_notes or None, tenant_id, request_id),
            )

        updated = conn.execute(
            """
            SELECT *
            FROM document_requests
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, request_id),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (
                tenant_id,
                row["task_id"],
                user_id,
                f"Document {row['document_name']} marked as {DOCUMENT_STATUS_LABELS.get(target_status, target_status)}.",
            ),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="document_request_status_changed",
            entity_type="document_request",
            entity_id=request_id,
            old_value={"status": old_status, "received_at": received_at_value, "notes": row["notes"]},
            new_value={
                "status": target_status,
                "received_at": updated["received_at"] if updated else received_at_value,
                "notes": updated["notes"] if updated else merged_notes,
            },
            metadata={"task_id": row["task_id"]},
            ip_address=ip_address,
        )

        pending_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_requests
            WHERE tenant_id = ? AND task_id = ? AND status = 'requested'
            """,
            (tenant_id, row["task_id"]),
        ).fetchone()["c"]

        if int(pending_count or 0) == 0 and row["task_status"] == "pending_documents":
            conn.execute(
                """
                UPDATE compliance_tasks
                SET status = 'ready_for_ai', pending_from = 'staff'
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, row["task_id"]),
            )
            db.touch_updated_at(conn, "compliance_tasks", row["task_id"])

            conn.execute(
                """
                INSERT INTO task_status_history
                    (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
                VALUES (?, ?, 'pending_documents', 'ready_for_ai', ?, ?)
                """,
                (tenant_id, row["task_id"], user_id, "All requested documents resolved"),
            )

            conn.execute(
                """
                INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
                VALUES (?, ?, ?, 'system', ?)
                """,
                (
                    tenant_id,
                    row["task_id"],
                    user_id,
                    "All requested documents resolved. Task moved to Ready for AI.",
                ),
            )

    return updated


def list_document_requests_for_task(tenant_id, task_id):
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT *
            FROM document_requests
            WHERE tenant_id = ? AND task_id = ?
            ORDER BY CASE WHEN status = 'requested' THEN 0 ELSE 1 END, created_at DESC, id DESC
            """,
            (tenant_id, task_id),
        ).fetchall()


def get_document_request_summary(tenant_id):
    today = date.today()
    month_start = today.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)

    with db.get_db() as conn:
        pending_documents_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_requests
            WHERE tenant_id = ? AND status = 'requested'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        received_this_month = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_requests
            WHERE tenant_id = ?
              AND status = 'received'
              AND received_at IS NOT NULL
              AND datetime(received_at) >= datetime(?)
              AND datetime(received_at) < datetime(?)
            """,
            (tenant_id, month_start.isoformat(), next_month_start.isoformat()),
        ).fetchone()["c"]

        waived_this_month = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_requests
            WHERE tenant_id = ?
              AND status = 'waived'
              AND datetime(created_at) >= datetime(?)
              AND datetime(created_at) < datetime(?)
            """,
            (tenant_id, month_start.isoformat(), next_month_start.isoformat()),
        ).fetchone()["c"]

    return {
        "pending_documents_count": int(pending_documents_count or 0),
        "received_this_month": int(received_this_month or 0),
        "waived_this_month": int(waived_this_month or 0),
    }

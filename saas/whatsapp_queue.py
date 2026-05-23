"""
WhatsApp Send Queue — Queue Management for WhatsApp Drafts
CA Assist (Wave 14: WhatsApp + Chromium Integration)

Manages the queue of reviewed WhatsApp drafts pending manual approval and sending.
Mirrors the structure and patterns of email_queue.py.

Status flow:
  queued → approved_to_send → sent
       └→ cancelled
  approved_to_send → failed → (requeue or cancel)
"""
import json
import logging
from datetime import datetime, timezone

import db
import whatsapp_sender

logger = logging.getLogger(__name__)

QUEUE_STATUSES = ["queued", "approved_to_send", "sent", "failed", "cancelled"]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _get_queue_row(conn, tenant_id: int, queue_id: int):
    return conn.execute(
        """
        SELECT q.*,
               c.name   AS client_name,
               t.title  AS task_title,
               d.draft_type,
               d.status AS draft_status
        FROM whatsapp_send_queue q
        LEFT JOIN client_entities c
               ON q.client_entity_id = c.id AND c.tenant_id = q.tenant_id
        LEFT JOIN compliance_tasks t
               ON q.task_id = t.id AND t.tenant_id = q.tenant_id
        LEFT JOIN document_communication_drafts d
               ON q.draft_id = d.id AND d.tenant_id = q.tenant_id
        WHERE q.tenant_id = ? AND q.id = ?
        """,
        (tenant_id, queue_id),
    ).fetchone()


# ---------------------------------------------------------------------------
# Queue creation
# ---------------------------------------------------------------------------

def queue_whatsapp_from_draft(
    tenant_id: int,
    draft_id: int,
    to_phone: str,
    user_id: int | None = None,
    media_url: str | None = None,
) -> int:
    """
    Create a WhatsApp queue entry from a reviewed communication draft.

    Returns the new queue item id.
    Raises ValueError if the draft is not found, is not a WhatsApp draft,
    or is not in 'reviewed' status.
    """
    to_phone = (to_phone or "").strip()
    if not to_phone:
        raise ValueError("Recipient phone number is required.")

    # Validate phone normalisation early
    normalised_phone = whatsapp_sender._normalise_phone(to_phone)

    with db.get_db() as conn:
        draft = conn.execute(
            """
            SELECT d.*, c.phone AS client_phone
            FROM document_communication_drafts d
            LEFT JOIN client_entities c
                   ON d.client_entity_id = c.id AND c.tenant_id = d.tenant_id
            WHERE d.tenant_id = ? AND d.id = ?
            """,
            (tenant_id, draft_id),
        ).fetchone()

        if not draft:
            raise ValueError(f"Draft {draft_id} not found.")
        if draft["draft_type"] != "whatsapp":
            raise ValueError("Only WhatsApp drafts can be queued here.")
        if draft["status"] != "reviewed":
            raise ValueError("Draft must be in 'reviewed' status before queueing.")

        now = _now_iso()
        cur = conn.execute(
            """
            INSERT INTO whatsapp_send_queue (
                tenant_id, client_entity_id, task_id, draft_id,
                to_phone, body, media_url, status,
                provider, queued_by, queued_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                draft["client_entity_id"],
                draft["task_id"],
                draft_id,
                normalised_phone,
                draft["body"],
                media_url,
                _resolve_provider(),
                user_id,
                now, now, now,
            ),
        )
        conn.commit()
        return cur.lastrowid


def _resolve_provider() -> str | None:
    """Return the currently configured provider name, or None if unconfigured."""
    try:
        return whatsapp_sender.get_whatsapp_provider()
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_whatsapp_queue_item(tenant_id: int, queue_id: int) -> dict:
    with db.get_db() as conn:
        row = _get_queue_row(conn, tenant_id, queue_id)
    return _row_to_dict(row)


def list_whatsapp_queue(tenant_id: int, filters: dict | None = None) -> list[dict]:
    filters = filters or {}
    params = [tenant_id]
    where = ["q.tenant_id = ?"]

    if filters.get("status"):
        where.append("q.status = ?")
        params.append(filters["status"].lower())
    if filters.get("client_entity_id"):
        where.append("q.client_entity_id = ?")
        params.append(int(filters["client_entity_id"]))
    if filters.get("task_id"):
        where.append("q.task_id = ?")
        params.append(int(filters["task_id"]))

    where_clause = " AND ".join(where)
    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT q.*,
                   c.name  AS client_name,
                   t.title AS task_title
            FROM whatsapp_send_queue q
            LEFT JOIN client_entities c
                   ON q.client_entity_id = c.id AND c.tenant_id = q.tenant_id
            LEFT JOIN compliance_tasks t
                   ON q.task_id = t.id AND t.tenant_id = q.tenant_id
            WHERE {where_clause}
            ORDER BY q.queued_at DESC
            """,
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_whatsapp_queue_summary(tenant_id: int) -> dict:
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'queued'            THEN 1 ELSE 0 END) AS queued_count,
                SUM(CASE WHEN status = 'approved_to_send'  THEN 1 ELSE 0 END) AS approved_count,
                SUM(CASE WHEN status = 'sent'              THEN 1 ELSE 0 END) AS sent_count,
                SUM(CASE WHEN status = 'failed'            THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN status = 'cancelled'         THEN 1 ELSE 0 END) AS cancelled_count,
                SUM(CASE WHEN date(queued_at) >= date('now', 'start of month') THEN 1 ELSE 0 END)
                    AS queued_this_month
            FROM whatsapp_send_queue
            WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()
    return _row_to_dict(row) or {}


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def approve_whatsapp_queue_item(
    tenant_id: int, queue_id: int, user_id: int | None = None
) -> dict:
    """
    Move item from 'queued' → 'approved_to_send'.
    Does not send — caller must call send_approved_whatsapp_queue_item() next.
    """
    with db.get_db() as conn:
        row = _get_queue_row(conn, tenant_id, queue_id)
        if not row:
            raise ValueError(f"Queue item {queue_id} not found.")
        if row["status"] != "queued":
            raise ValueError(f"Item must be in 'queued' status to approve (current: {row['status']}).")
        conn.execute(
            "UPDATE whatsapp_send_queue SET status='approved_to_send', updated_at=? WHERE tenant_id=? AND id=?",
            (_now_iso(), tenant_id, queue_id),
        )
        conn.commit()
    return get_whatsapp_queue_item(tenant_id, queue_id)


def cancel_whatsapp_queue_item(
    tenant_id: int, queue_id: int, reason: str | None = None
) -> dict:
    """Cancel a queued or approved (not yet sent) WhatsApp item."""
    with db.get_db() as conn:
        row = _get_queue_row(conn, tenant_id, queue_id)
        if not row:
            raise ValueError(f"Queue item {queue_id} not found.")
        if row["status"] in ("sent", "cancelled"):
            raise ValueError(f"Cannot cancel item in status '{row['status']}'.")
        meta = json.loads(row["metadata_json"] or "{}")
        if reason:
            meta["cancel_reason"] = reason
        conn.execute(
            "UPDATE whatsapp_send_queue SET status='cancelled', metadata_json=?, updated_at=? WHERE tenant_id=? AND id=?",
            (json.dumps(meta), _now_iso(), tenant_id, queue_id),
        )
        conn.commit()
    return get_whatsapp_queue_item(tenant_id, queue_id)


# ---------------------------------------------------------------------------
# Actual sending
# ---------------------------------------------------------------------------

def send_approved_whatsapp_queue_item(
    tenant_id: int, queue_id: int, user_id: int | None = None
) -> dict:
    """
    Send an 'approved_to_send' WhatsApp queue item.
    Updates status to 'sent' on success, 'failed' on error.
    Returns the updated queue item dict (with 'send_result' key added).
    """
    with db.get_db() as conn:
        row = _get_queue_row(conn, tenant_id, queue_id)
        if not row:
            raise ValueError(f"Queue item {queue_id} not found.")
        if row["status"] != "approved_to_send":
            raise ValueError(
                f"Item must be in 'approved_to_send' status to send (current: {row['status']})."
            )

        to_phone = row["to_phone"]
        body = row["body"]
        media_url = row["media_url"]
        provider = row["provider"]
        now = _now_iso()

        try:
            result = whatsapp_sender.send_whatsapp_message(
                to_phone=to_phone,
                body=body,
                media_url=media_url,
                provider=provider,
            )
            conn.execute(
                """
                UPDATE whatsapp_send_queue
                   SET status='sent',
                       provider_message_id=?,
                       sent_at=?,
                       updated_at=?
                 WHERE tenant_id=? AND id=?
                """,
                (result.get("message_id"), now, now, tenant_id, queue_id),
            )
            conn.commit()
            logger.info("WhatsApp sent: queue_id=%s provider_msg=%s", queue_id, result.get("message_id"))
            item = get_whatsapp_queue_item(tenant_id, queue_id)
            item["send_result"] = result
            return item

        except Exception as exc:
            error_msg = str(exc)[:500]
            conn.execute(
                """
                UPDATE whatsapp_send_queue
                   SET status='failed',
                       error_message=?,
                       failed_at=?,
                       updated_at=?
                 WHERE tenant_id=? AND id=?
                """,
                (error_msg, now, now, tenant_id, queue_id),
            )
            conn.commit()
            logger.error("WhatsApp send failed: queue_id=%s error=%s", queue_id, error_msg)
            raise RuntimeError(f"WhatsApp send failed: {error_msg}") from exc

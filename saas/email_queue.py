"""
Email Send Queue — Internal Queue for Reviewed Email Drafts
CA Assist (Phase: Reviewed Email Sending Foundation — Manual Review Only)

This module manages the queue of reviewed email drafts ready for future sending.
No actual SMTP, Gmail, or Zoho integration in this phase.
All emails are queued manually through the UI and marked as reviewed before queueing.
"""
import json
from datetime import datetime, timezone
import db

# Constants
QUEUE_STATUSES = ["queued", "ready_to_send", "approved_to_send", "sent", "failed", "cancelled"]
SEND_MODES = ["manual_review", "smtp_future", "gmail_future", "zoho_future"]


def _get_email_queue_item_row(conn, tenant_id, queue_id):
    row = conn.execute(
        """
        SELECT q.*, c.name as client_name, t.title as task_title, d.draft_type,
               eps.display_name AS provider_display_name,
               eps.provider_type AS provider_type,
               eps.from_email AS provider_from_email,
                             eps.last_check_status AS provider_last_check_status,
                             ap.id AS approval_id,
                             ap.approval_status,
                             ap.approved_at,
                     ap.approval_note,
                     fr.id AS failure_review_id,
                     fr.review_status AS failure_review_status,
                     fr.review_note AS failure_review_note,
                     fr.reopen_note AS failure_reopen_note,
                     fr.reviewed_at AS failure_reviewed_at,
                     fr.reopened_at AS failure_reopened_at
        FROM email_send_queue q
        LEFT JOIN client_entities c ON q.client_entity_id = c.id AND c.tenant_id = q.tenant_id
        LEFT JOIN compliance_tasks t ON q.task_id = t.id AND t.tenant_id = q.tenant_id
        LEFT JOIN document_communication_drafts d ON q.draft_id = d.id AND d.tenant_id = q.tenant_id
        LEFT JOIN email_provider_settings eps ON q.provider_setting_id = eps.id AND eps.tenant_id = q.tenant_id
                LEFT JOIN email_send_approvals ap
                    ON ap.tenant_id = q.tenant_id
                 AND ap.queue_id = q.id
                 AND ap.id = (
                        SELECT MAX(a2.id)
                        FROM email_send_approvals a2
                        WHERE a2.tenant_id = q.tenant_id
                            AND a2.queue_id = q.id
                )
                LEFT JOIN email_failure_reviews fr
                    ON fr.tenant_id = q.tenant_id
                 AND fr.queue_id = q.id
                 AND fr.id = (
                        SELECT MAX(f2.id)
                        FROM email_failure_reviews f2
                        WHERE f2.tenant_id = q.tenant_id
                          AND f2.queue_id = q.id
                )
        WHERE q.tenant_id = ? AND q.id = ?
        """,
        (tenant_id, queue_id)
    ).fetchone()
    return dict(row) if row else None

# 1. Queue a reviewed email draft for future sending
def queue_reviewed_email_draft(tenant_id, draft_id, to_email=None, cc_email=None, bcc_email=None, user_id=None, ip_address=None):
    """
    Queue a reviewed email draft for future sending.
    
    - Load the draft (tenant-safe).
    - Verify draft_type is "email".
    - Verify draft status is "reviewed".
    - Use provided to_email or resolve from client_entities.email.
    - Raise ValueError if no recipient email available.
    - Insert email_send_queue row with status=queued, send_mode=manual_review.
    - Add audit log action: email_draft_queued.
    - Add task_comments system note: "Reviewed email draft queued for future sending."
    - Return queue row.
    """
    with db.get_db() as conn:
        # Load and verify draft
        draft = conn.execute(
            """
            SELECT d.*, t.title as task_title, c.name as client_name, c.email as client_email, c.id as client_id
            FROM document_communication_drafts d
            JOIN compliance_tasks t ON d.task_id = t.id AND t.tenant_id = d.tenant_id
            JOIN client_entities c ON d.client_entity_id = c.id AND c.tenant_id = d.tenant_id
            WHERE d.tenant_id = ? AND d.id = ?
            """,
            (tenant_id, draft_id)
        ).fetchone()
        
        if not draft:
            raise ValueError("Draft not found")
        
        if draft["draft_type"] != "email":
            raise ValueError("Only email drafts can be queued for sending")
        
        if draft["status"] != "reviewed":
            raise ValueError("Only reviewed drafts can be queued for sending")

        draft = dict(draft)
        
        # Resolve recipient email
        recipient_email = to_email or draft.get("client_email")
        if not recipient_email:
            raise ValueError("No recipient email available for this draft")
        
        # Insert email_send_queue row
        cur = conn.execute(
            """
            INSERT INTO email_send_queue
                (tenant_id, client_entity_id, task_id, draft_id, to_email, cc_email, bcc_email,
                 subject, body, status, send_mode, provider, queued_by, queued_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'manual_review', NULL, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                tenant_id, draft["client_entity_id"], draft["task_id"], draft_id,
                recipient_email, cc_email, bcc_email,
                draft["subject"], draft["body"],
                user_id
            )
        )
        queue_id = cur.lastrowid
        
        # Add audit log
        db.log_audit(
            conn, tenant_id, user_id,
            "email_draft_queued",
            "email_send_queue", queue_id,
            None,
            {
                "draft_id": draft_id,
                "to_email": recipient_email,
                "cc_email": cc_email,
                "bcc_email": bcc_email
            },
            None,
            ip_address
        )
        
        # Add task comment
        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
            VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
            """,
            (tenant_id, draft["task_id"], "Reviewed email draft queued for future sending.")
        )

        return _get_email_queue_item_row(conn, tenant_id, queue_id)


# 2. List email queue items with filtering
def list_email_queue(tenant_id, filters=None):
    """
    List email queue items with filtering.
    
    Filters:
    - client_entity_id
    - task_id
    - status
    - search (over client name, to_email, subject, body, task title)
    
    Returns:
    - queue_id, client_name, task_id, task_title, draft_id, to_email, subject,
      status, send_mode, provider, queued_at, sent_at, failed_at, error_message
    """
    filters = filters or {}
    params = [tenant_id]
    where = ["q.tenant_id = ?"]
    
    if filters.get("client_entity_id"):
        where.append("q.client_entity_id = ?")
        params.append(filters["client_entity_id"])
    
    if filters.get("task_id"):
        where.append("q.task_id = ?")
        params.append(filters["task_id"])
    
    if filters.get("status"):
        where.append("q.status = ?")
        params.append(filters["status"])
    
    if filters.get("search"):
        search = f"%{filters['search'].lower()}%"
        where.append("(" +
            "LOWER(c.name) LIKE ? OR "
            "LOWER(q.to_email) LIKE ? OR "
            "LOWER(q.subject) LIKE ? OR "
            "LOWER(q.body) LIKE ? OR "
            "LOWER(t.title) LIKE ? "
            ")")
        params.extend([search, search, search, search, search])
    
    where_clause = " AND ".join(where)
    
    with db.get_db() as conn:
        rows = conn.execute(f'''
            SELECT q.id as queue_id, c.name as client_name, q.task_id, t.title as task_title,
                   q.draft_id, q.to_email, q.subject, q.status, q.send_mode, q.provider,
                   q.provider_setting_id,
                   eps.display_name as provider_display_name,
                   eps.provider_type as provider_type,
                   eps.from_email as provider_from_email,
                   eps.last_check_status as provider_last_check_status,
                   ap.id AS approval_id,
                   ap.approval_status,
                   ap.approved_at,
                   ap.approval_note,
                   q.queued_at, q.sent_at, q.failed_at, q.error_message
            FROM email_send_queue q
            JOIN client_entities c ON q.client_entity_id = c.id AND c.tenant_id = q.tenant_id
            JOIN compliance_tasks t ON q.task_id = t.id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps ON q.provider_setting_id = eps.id AND eps.tenant_id = q.tenant_id
            LEFT JOIN email_send_approvals ap
              ON ap.tenant_id = q.tenant_id
             AND ap.queue_id = q.id
             AND ap.id = (
                SELECT MAX(a2.id)
                FROM email_send_approvals a2
                WHERE a2.tenant_id = q.tenant_id
                  AND a2.queue_id = q.id
            )
            WHERE {where_clause}
            ORDER BY q.queued_at DESC
        ''', params).fetchall()
        return [dict(row) for row in rows]


def get_latest_send_approval_for_queue(tenant_id, queue_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM email_send_approvals
            WHERE tenant_id = ? AND queue_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (tenant_id, queue_id),
        ).fetchone()
    return dict(row) if row else None


def get_latest_failure_review_for_queue(tenant_id, queue_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM email_failure_reviews
            WHERE tenant_id = ? AND queue_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (tenant_id, queue_id),
        ).fetchone()
    return dict(row) if row else None


def add_failure_review_note(tenant_id, queue_id, review_note, user_id=None, ip_address=None):
    review_note = (review_note or "").strip()
    if not review_note:
        raise ValueError("Failure review note is required")

    with db.get_db() as conn:
        queue_item = conn.execute(
            "SELECT * FROM email_send_queue WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, queue_id),
        ).fetchone()
        if not queue_item:
            raise ValueError("Queue item not found")
        if queue_item["status"] != "failed":
            raise ValueError("Failure review notes can only be added for failed queue items")

        cur = conn.execute(
            """
            INSERT INTO email_failure_reviews
                (tenant_id, queue_id, review_status, review_note, reviewed_by, reviewed_at, created_at)
            VALUES (?, ?, 'reviewed', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (tenant_id, queue_id, review_note, user_id),
        )
        review_id = cur.lastrowid

        db.log_audit(
            conn,
            tenant_id,
            user_id,
            "email_queue_failure_reviewed",
            "email_send_queue",
            queue_id,
            None,
            {"failure_review_id": review_id, "review_status": "reviewed"},
            None,
            ip_address,
        )

        if queue_item["task_id"]:
            conn.execute(
                """
                INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
                VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
                """,
                (tenant_id, queue_item["task_id"], "SMTP failure reviewed. No email has been resent."),
            )

        row = conn.execute(
            "SELECT * FROM email_failure_reviews WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, review_id),
        ).fetchone()
    return dict(row) if row else None


def reopen_failed_email_queue_item(tenant_id, queue_id, reopen_note=None, user_id=None, ip_address=None):
    reopen_note = (reopen_note or "").strip() or None

    with db.get_db() as conn:
        queue_item = conn.execute(
            "SELECT * FROM email_send_queue WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, queue_id),
        ).fetchone()
        if not queue_item:
            raise ValueError("Queue item not found")
        if queue_item["status"] != "failed":
            raise ValueError("Only failed queue items can be reopened")
        if not queue_item["provider_setting_id"]:
            raise ValueError("Provider assignment is required before reopening")

        provider = conn.execute(
            """
            SELECT id, status
            FROM email_provider_settings
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, queue_item["provider_setting_id"]),
        ).fetchone()
        if not provider:
            raise ValueError("Assigned provider not found")
        if provider["status"] != "active":
            raise ValueError("Assigned provider must be active before reopening")

        latest_approval = conn.execute(
            """
            SELECT approval_status
            FROM email_send_approvals
            WHERE tenant_id = ? AND queue_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (tenant_id, queue_id),
        ).fetchone()
        if not latest_approval or latest_approval["approval_status"] != "approved":
            raise ValueError("An active approved send approval is required before reopening")

        dry_run = conn.execute(
            """
            SELECT id
            FROM email_dry_run_previews
            WHERE tenant_id = ? AND queue_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (tenant_id, queue_id),
        ).fetchone()
        if not dry_run:
            raise ValueError("At least one dry-run preview is required before reopening")

        conn.execute(
            """
            UPDATE email_send_queue
            SET status = 'approved_to_send',
                failed_at = NULL,
                error_message = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, queue_id),
        )

        cur = conn.execute(
            """
            INSERT INTO email_failure_reviews
                (tenant_id, queue_id, review_status, reopen_note, reviewed_by, reopened_by, reviewed_at, reopened_at, created_at)
            VALUES (?, ?, 'reopened', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (tenant_id, queue_id, reopen_note, user_id, user_id),
        )
        failure_review_id = cur.lastrowid

        db.log_audit(
            conn,
            tenant_id,
            user_id,
            "email_queue_reopened_after_failure",
            "email_send_queue",
            queue_id,
            {"status": queue_item["status"], "failed_at": queue_item["failed_at"], "error_message": queue_item["error_message"]},
            {"status": "approved_to_send", "failure_review_id": failure_review_id},
            None,
            ip_address,
        )

        if queue_item["task_id"]:
            conn.execute(
                """
                INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
                VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
                """,
                (tenant_id, queue_item["task_id"], "Failed email queue item reopened for manual sending. No email has been sent."),
            )

        return _get_email_queue_item_row(conn, tenant_id, queue_id)


def list_available_email_providers_for_queue(tenant_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, display_name, provider_type, from_email, is_default, last_check_status
            FROM email_provider_settings
            WHERE tenant_id = ? AND status = 'active'
            ORDER BY is_default DESC, display_name ASC
            """,
            (tenant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_default_email_provider(tenant_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT id, display_name, provider_type, from_email, is_default, last_check_status
            FROM email_provider_settings
            WHERE tenant_id = ? AND status = 'active' AND is_default = 1
            LIMIT 1
            """,
            (tenant_id,),
        ).fetchone()
    return dict(row) if row else None


def assign_provider_to_queue_item(tenant_id, queue_id, provider_id=None, user_id=None, ip_address=None):
    with db.get_db() as conn:
        queue_item = conn.execute(
            "SELECT * FROM email_send_queue WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, queue_id),
        ).fetchone()
        if not queue_item:
            raise ValueError("Queue item not found")

        provider_row = None
        if provider_id:
            try:
                provider_id = int(provider_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid provider selection") from exc
            provider_row = conn.execute(
                """
                SELECT id, display_name, provider_type, from_email, status
                FROM email_provider_settings
                WHERE tenant_id = ? AND id = ?
                LIMIT 1
                """,
                (tenant_id, provider_id),
            ).fetchone()
        else:
            provider_row = conn.execute(
                """
                SELECT id, display_name, provider_type, from_email, status
                FROM email_provider_settings
                WHERE tenant_id = ? AND status = 'active' AND is_default = 1
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()

        if not provider_row:
            raise ValueError("No active default provider found")

        provider = dict(provider_row)
        if provider.get("status") != "active":
            raise ValueError("Selected provider must be active")

        queue_status = queue_item["status"]
        if queue_status in {"ready_to_send", "cancelled"}:
            next_status = queue_status
        else:
            next_status = "queued"

        conn.execute(
            """
            UPDATE email_send_queue
            SET provider_setting_id = ?,
                provider = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (provider["id"], provider["provider_type"], next_status, tenant_id, queue_id),
        )

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
            VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
            """,
            (tenant_id, queue_item["task_id"], "Email provider assigned to queued email."),
        )

        db.log_audit(
            conn,
            tenant_id,
            user_id,
            "email_queue_provider_assigned",
            "email_send_queue",
            queue_id,
            {
                "provider_setting_id": queue_item["provider_setting_id"],
                "provider": queue_item["provider"],
                "status": queue_item["status"],
            },
            {
                "provider_setting_id": provider["id"],
                "provider": provider["provider_type"],
                "status": next_status,
            },
            {
                "provider_display_name": provider["display_name"],
                "provider_from_email": provider["from_email"],
            },
            ip_address,
        )

        return _get_email_queue_item_row(conn, tenant_id, queue_id)


def mark_queue_ready_with_provider(tenant_id, queue_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        queue_item = conn.execute(
            "SELECT * FROM email_send_queue WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, queue_id),
        ).fetchone()
        if not queue_item:
            raise ValueError("Queue item not found")

        if not queue_item["provider_setting_id"]:
            raise ValueError("Assign an active provider before marking ready")

        if queue_item["status"] != "queued":
            raise ValueError("Only queued items can be marked ready to send")

        conn.execute(
            """
            UPDATE email_send_queue
            SET status = 'ready_to_send',
                updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, queue_id),
        )

        db.log_audit(
            conn,
            tenant_id,
            user_id,
            "email_queue_ready_to_send",
            "email_send_queue",
            queue_id,
            {"status": queue_item["status"]},
            {"status": "ready_to_send"},
            None,
            ip_address,
        )

        return _get_email_queue_item_row(conn, tenant_id, queue_id)


# 3. Get email queue summary KPIs
def get_email_queue_summary(tenant_id, filters=None):
    """
    Return KPI counts for email queue.
    
    Returns:
    - total, queued_count, ready_to_send_count, sent_count, failed_count, cancelled_count, queued_this_month
    """
    filters = filters or {}
    params = [tenant_id]
    where = ["tenant_id = ?"]
    
    if filters.get("client_entity_id"):
        where.append("client_entity_id = ?")
        params.append(filters["client_entity_id"])
    
    if filters.get("task_id"):
        where.append("task_id = ?")
        params.append(filters["task_id"])
    
    where_clause = " AND ".join(where)
    
    with db.get_db() as conn:
        summary = conn.execute(f'''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) as queued_count,
                SUM(CASE WHEN status = 'ready_to_send' THEN 1 ELSE 0 END) as ready_to_send_count,
                SUM(CASE WHEN status = 'approved_to_send' THEN 1 ELSE 0 END) as approved_to_send_count,
                SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_count,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled_count,
                SUM(CASE WHEN strftime('%Y-%m', queued_at) = strftime('%Y-%m', 'now') THEN 1 ELSE 0 END) as queued_this_month
            FROM email_send_queue
            WHERE {where_clause}
        ''', params).fetchone()
        
        return {
            "total": summary["total"] or 0,
            "queued_count": summary["queued_count"] or 0,
            "ready_to_send_count": summary["ready_to_send_count"] or 0,
            "approved_to_send_count": summary["approved_to_send_count"] or 0,
            "sent_count": summary["sent_count"] or 0,
            "failed_count": summary["failed_count"] or 0,
            "cancelled_count": summary["cancelled_count"] or 0,
            "queued_this_month": summary["queued_this_month"] or 0
        }


# 4. Get email queue item detail
def get_email_queue_item(tenant_id, queue_id):
    """
    Get full detail of a queued email item.
    
    Joins:
    - client_entities
    - compliance_tasks
    - document_communication_drafts
    """
    with db.get_db() as conn:
        return _get_email_queue_item_row(conn, tenant_id, queue_id)


# 5. Update email queue status
def update_email_queue_status(tenant_id, queue_id, status, error_message=None, user_id=None, ip_address=None):
    """
    Update email queue status.
    
    Allowed statuses for manual review phase:
    - queued
    - ready_to_send
    - cancelled
    
    Sent/failed marked only by system in future automated phases.
    """
    if status not in QUEUE_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    
    # For manual review phase, restrict to certain statuses
    if status not in ["queued", "ready_to_send", "cancelled"]:
        raise ValueError(f"Status '{status}' cannot be set manually in this phase")
    
    with db.get_db() as conn:
        # Load current item to verify it exists
        item = conn.execute(
            "SELECT * FROM email_send_queue WHERE tenant_id = ? AND id = ?",
            (tenant_id, queue_id)
        ).fetchone()
        
        if not item:
            raise ValueError("Queue item not found")

        if status == "ready_to_send" and not item["provider_setting_id"]:
            raise ValueError("Assign an active provider before setting ready_to_send")
        
        conn.execute(
            """
            UPDATE email_send_queue
            SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (status, error_message, tenant_id, queue_id)
        )
        
        # Add audit log
        db.log_audit(
            conn, tenant_id, user_id,
            "email_queue_status_changed",
            "email_send_queue", queue_id,
            dict(item),
            {"status": status, "error_message": error_message},
            None,
            ip_address
        )

        return _get_email_queue_item_row(conn, tenant_id, queue_id)


# 6. Check if queue item can be manually sent via SMTP
def can_manually_send_queue_item(tenant_id, queue_id):
    """
    Lightweight check to determine if a queue item can be manually sent via SMTP.
    
    Returns:
        dict with "can_send" (bool) and "errors" (list of strings)
    """
    # This delegates to smtp_sender.validate_send_preconditions
    # but we can't import it here to avoid circular imports.
    # Instead, perform a minimal check here.
    
    errors = []
    queue_item = get_email_queue_item(tenant_id, queue_id)
    
    if not queue_item:
        errors.append("Queue item not found")
        return {"can_send": False, "errors": errors}
    
    # Check status
    if queue_item.get("status") != "approved_to_send":
        errors.append(f"Queue must be in 'approved_to_send' status, got '{queue_item.get('status')}'")
    
    # Check provider type
    if queue_item.get("provider_type") != "smtp":
        provider_type = queue_item.get("provider_type") or "none"
        errors.append(f"Provider must be SMTP type, got '{provider_type}'")
    
    # Check provider is active
    if queue_item.get("provider_last_check_status") != "ready":
        status = queue_item.get("provider_last_check_status") or "unknown"
        errors.append(f"Provider must be ready, current status: '{status}'")
    
    # Check approval status
    if queue_item.get("approval_status") != "approved":
        approval_status = queue_item.get("approval_status") or "none"
        errors.append(f"Approval must be 'approved', got '{approval_status}'")
    
    if errors:
        return {"can_send": False, "errors": errors}
    
    return {"can_send": True, "errors": []}


def _delivery_log_filter_clause(filters):
    filters = filters or {}
    params = []
    where = []

    default_statuses = ["approved_to_send", "sent", "failed", "ready_to_send"]
    status = (filters.get("status") or "").strip()
    if status:
        where.append("q.status = ?")
        params.append(status)
    else:
        placeholders = ",".join(["?"] * len(default_statuses))
        where.append(f"q.status IN ({placeholders})")
        params.extend(default_statuses)

    if filters.get("client_entity_id"):
        where.append("q.client_entity_id = ?")
        params.append(filters["client_entity_id"])

    if filters.get("task_id"):
        where.append("q.task_id = ?")
        params.append(filters["task_id"])

    if filters.get("provider_id"):
        where.append("q.provider_setting_id = ?")
        params.append(filters["provider_id"])

    date_from = (filters.get("date_from") or "").strip()
    if date_from:
        where.append("DATE(COALESCE(q.sent_at, q.failed_at, q.queued_at)) >= DATE(?)")
        params.append(date_from)

    date_to = (filters.get("date_to") or "").strip()
    if date_to:
        where.append("DATE(COALESCE(q.sent_at, q.failed_at, q.queued_at)) <= DATE(?)")
        params.append(date_to)

    search = (filters.get("search") or "").strip().lower()
    if search:
        like = f"%{search}%"
        where.append(
            "(" 
            "LOWER(c.name) LIKE ? OR "
            "LOWER(t.title) LIKE ? OR "
            "LOWER(COALESCE(eps.display_name, '')) LIKE ? OR "
            "LOWER(COALESCE(q.to_email, '')) LIKE ? OR "
            "LOWER(COALESCE(q.subject, '')) LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like])

    return where, params


def list_email_delivery_logs(tenant_id, filters=None):
    """
    List email delivery/send log records with tenant-safe filtering.

    Default statuses:
    - approved_to_send
    - ready_to_send
    - sent
    - failed
    """
    filters = filters or {}
    where, params = _delivery_log_filter_clause(filters)
    where = ["q.tenant_id = ?"] + where
    params = [tenant_id] + params

    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                q.id AS queue_id,
                c.name AS client_name,
                q.task_id,
                t.title AS task_title,
                q.draft_id,
                q.provider_setting_id,
                eps.display_name AS provider_display_name,
                eps.provider_type AS provider_type,
                q.to_email,
                q.subject,
                q.status,
                q.send_mode,
                q.queued_at,
                q.sent_at,
                q.failed_at,
                q.error_message
            FROM email_send_queue q
            LEFT JOIN client_entities c
                ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            LEFT JOIN compliance_tasks t
                ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN document_communication_drafts d
                ON d.id = q.draft_id AND d.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps
                ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE {' AND '.join(where)}
            ORDER BY datetime(COALESCE(q.sent_at, q.failed_at, q.queued_at)) DESC, q.id DESC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_email_delivery_log_summary(tenant_id, filters=None):
    """
    Return KPI counts for delivery log register.
    """
    filters = filters or {}
    where, params = _delivery_log_filter_clause(filters)
    where = ["q.tenant_id = ?"] + where
    params = [tenant_id] + params

    with db.get_db() as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN q.status = 'approved_to_send' THEN 1 ELSE 0 END) AS approved_to_send_count,
                SUM(CASE WHEN q.status = 'ready_to_send' THEN 1 ELSE 0 END) AS ready_to_send_count,
                SUM(CASE WHEN q.status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                SUM(CASE WHEN q.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                SUM(
                    CASE
                        WHEN q.status = 'sent'
                         AND strftime('%Y-%m', q.sent_at) = strftime('%Y-%m', 'now')
                        THEN 1 ELSE 0
                    END
                ) AS sent_this_month,
                SUM(
                    CASE
                        WHEN q.status = 'failed'
                         AND strftime('%Y-%m', q.failed_at) = strftime('%Y-%m', 'now')
                        THEN 1 ELSE 0
                    END
                ) AS failed_this_month
            FROM email_send_queue q
            LEFT JOIN client_entities c
                ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            LEFT JOIN compliance_tasks t
                ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps
                ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchone()

    return {
        "total": row["total"] or 0,
        "approved_to_send_count": row["approved_to_send_count"] or 0,
        "ready_to_send_count": row["ready_to_send_count"] or 0,
        "sent_count": row["sent_count"] or 0,
        "failed_count": row["failed_count"] or 0,
        "sent_this_month": row["sent_this_month"] or 0,
        "failed_this_month": row["failed_this_month"] or 0,
    }

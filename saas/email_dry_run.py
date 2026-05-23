import json

import db


REQUIRED_FIELDS = ["from_email", "to_email", "subject", "body"]


def _as_dict(row):
    if not row:
        return None
    return dict(row)


def _parse_validation(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _get_queue_with_provider(conn, tenant_id, queue_id):
    row = conn.execute(
        """
        SELECT q.*,
               c.name AS client_name,
               t.title AS task_title,
               eps.display_name AS provider_display_name,
               eps.provider_type AS provider_type,
               eps.status AS provider_status,
               eps.from_email AS provider_from_email,
               eps.from_name AS provider_from_name
        FROM email_send_queue q
        LEFT JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
        LEFT JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
        LEFT JOIN email_provider_settings eps ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
        WHERE q.tenant_id = ? AND q.id = ?
        LIMIT 1
        """,
        (tenant_id, queue_id),
    ).fetchone()
    return _as_dict(row)


def _get_preview_row(conn, tenant_id, preview_id):
    row = conn.execute(
        """
         SELECT p.*,
               q.status AS queue_status,
               q.sent_at,
               c.name AS client_name,
               t.title AS task_title,
               eps.display_name AS provider_display_name,
             eps.provider_type AS provider_type,
             ap.id AS approval_id,
             ap.approval_status,
             ap.approved_at,
             ap.approval_note
        FROM email_dry_run_previews p
        JOIN email_send_queue q ON q.id = p.queue_id AND q.tenant_id = p.tenant_id
        LEFT JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
        LEFT JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
        LEFT JOIN email_provider_settings eps ON eps.id = p.provider_setting_id AND eps.tenant_id = p.tenant_id
         LEFT JOIN email_send_approvals ap
           ON ap.tenant_id = p.tenant_id
          AND ap.queue_id = p.queue_id
          AND ap.id = (
             SELECT MAX(a2.id)
             FROM email_send_approvals a2
             WHERE a2.tenant_id = p.tenant_id
            AND a2.queue_id = p.queue_id
          )
        WHERE p.tenant_id = ? AND p.id = ?
        LIMIT 1
        """,
        (tenant_id, preview_id),
    ).fetchone()
    item = _as_dict(row)
    if item:
        item["validation"] = _parse_validation(item.get("validation_json"))
    return item


def _build_validation(payload):
    missing = []
    for field in REQUIRED_FIELDS:
        if not (payload.get(field) or "").strip():
            missing.append(field)
    return {
        "status": "ready" if not missing else "incomplete",
        "ready": len(missing) == 0,
        "missing_fields": missing,
        "checks": {
            "provider_assigned": True,
            "queue_ready_to_send": True,
            "provider_active": True,
            "required_fields_present": len(missing) == 0,
        },
    }


def build_email_payload_preview(tenant_id, queue_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        queue_item = _get_queue_with_provider(conn, tenant_id, queue_id)
        if not queue_item:
            raise ValueError("Queue item not found")

        if queue_item.get("status") != "ready_to_send":
            raise ValueError("Dry run preview is allowed only for ready_to_send queue items")

        provider_setting_id = queue_item.get("provider_setting_id")
        if not provider_setting_id:
            raise ValueError("Assign an active provider before generating dry run preview")

        if queue_item.get("provider_status") != "active":
            raise ValueError("Assigned provider must be active")

        payload = {
            "from_email": queue_item.get("provider_from_email") or "",
            "from_name": queue_item.get("provider_from_name") or "",
            "to_email": queue_item.get("to_email") or "",
            "cc_email": queue_item.get("cc_email") or "",
            "bcc_email": queue_item.get("bcc_email") or "",
            "subject": queue_item.get("subject") or "",
            "body": queue_item.get("body") or "",
        }
        validation = _build_validation(payload)

        cur = conn.execute(
            """
            INSERT INTO email_dry_run_previews (
                tenant_id,
                queue_id,
                provider_setting_id,
                status,
                from_email,
                from_name,
                to_email,
                cc_email,
                bcc_email,
                subject,
                body,
                validation_json,
                created_by,
                created_at
            ) VALUES (?, ?, ?, 'generated', ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                tenant_id,
                queue_id,
                provider_setting_id,
                payload["from_email"],
                payload["from_name"],
                payload["to_email"],
                payload["cc_email"],
                payload["bcc_email"],
                payload["subject"],
                payload["body"],
                json.dumps(validation, ensure_ascii=False),
                user_id,
            ),
        )
        preview_id = cur.lastrowid

        db.log_audit(
            conn,
            tenant_id,
            user_id,
            "email_dry_run_preview_generated",
            "email_dry_run_previews",
            preview_id,
            None,
            {
                "queue_id": queue_id,
                "provider_setting_id": provider_setting_id,
                "status": "generated",
                "validation_status": validation.get("status"),
                "missing_fields": validation.get("missing_fields", []),
            },
            {
                "dry_run_only": True,
                "provider_type": queue_item.get("provider_type"),
            },
            ip_address,
        )

        return _get_preview_row(conn, tenant_id, preview_id)


def get_email_dry_run_preview(tenant_id, preview_id):
    with db.get_db() as conn:
        return _get_preview_row(conn, tenant_id, preview_id)


def list_email_dry_runs_for_queue(tenant_id, queue_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.id,
                   p.queue_id,
                   p.provider_setting_id,
                   p.status,
                   p.from_email,
                   p.to_email,
                   p.subject,
                   p.validation_json,
                   p.created_at,
                   eps.display_name AS provider_display_name,
                   eps.provider_type AS provider_type
            FROM email_dry_run_previews p
            LEFT JOIN email_provider_settings eps ON eps.id = p.provider_setting_id AND eps.tenant_id = p.tenant_id
            WHERE p.tenant_id = ? AND p.queue_id = ?
            ORDER BY datetime(p.created_at) DESC, p.id DESC
            """,
            (tenant_id, queue_id),
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["validation"] = _parse_validation(item.get("validation_json"))
        items.append(item)
    return items


def approve_dry_run_for_sending(tenant_id, preview_id, approval_note=None, user_id=None, ip_address=None):
    note = (approval_note or "").strip() or None
    with db.get_db() as conn:
        preview = _get_preview_row(conn, tenant_id, preview_id)
        if not preview:
            raise ValueError("Dry-run preview not found")

        queue_item = _get_queue_with_provider(conn, tenant_id, preview["queue_id"])
        if not queue_item:
            raise ValueError("Queue item not found")

        if queue_item.get("status") != "ready_to_send":
            raise ValueError("Only ready_to_send queue items can be approved")

        validation = preview.get("validation") or {}
        if validation.get("status") != "ready":
            raise ValueError("Dry-run preview must be ready before approval")

        provider_setting_id = queue_item.get("provider_setting_id")
        if not provider_setting_id:
            raise ValueError("Assigned active provider is required before approval")

        if queue_item.get("provider_status") != "active":
            raise ValueError("Assigned provider must be active")

        cur = conn.execute(
            """
            INSERT INTO email_send_approvals (
                tenant_id,
                queue_id,
                dry_run_preview_id,
                provider_setting_id,
                approval_status,
                approved_by,
                approved_at,
                approval_note,
                created_at
            ) VALUES (?, ?, ?, ?, 'approved', ?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
            """,
            (
                tenant_id,
                queue_item["id"],
                preview_id,
                provider_setting_id,
                user_id,
                note,
            ),
        )
        approval_id = cur.lastrowid

        conn.execute(
            """
            UPDATE email_send_queue
            SET status = 'approved_to_send',
                updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, queue_item["id"]),
        )

        db.log_audit(
            conn,
            tenant_id,
            user_id,
            "email_send_approved",
            "email_send_approvals",
            approval_id,
            None,
            {
                "queue_id": queue_item["id"],
                "dry_run_preview_id": preview_id,
                "approval_status": "approved",
                "approval_note": note,
            },
            {"dry_run_only": True},
            ip_address,
        )

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
            VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
            """,
            (
                tenant_id,
                queue_item.get("task_id"),
                "Email dry-run preview approved for future sending. No email has been sent.",
            ),
        )

        row = conn.execute(
            """
            SELECT *
            FROM email_send_approvals
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, approval_id),
        ).fetchone()
        return _as_dict(row)


def revoke_send_approval(tenant_id, approval_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        approval = conn.execute(
            """
            SELECT *
            FROM email_send_approvals
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, approval_id),
        ).fetchone()
        if not approval:
            raise ValueError("Approval not found")
        approval = dict(approval)

        conn.execute(
            """
            UPDATE email_send_approvals
            SET approval_status = 'revoked'
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, approval_id),
        )

        queue_item = conn.execute(
            """
            SELECT *
            FROM email_send_queue
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, approval["queue_id"]),
        ).fetchone()

        if queue_item and queue_item["status"] == "approved_to_send":
            conn.execute(
                """
                UPDATE email_send_queue
                SET status = 'ready_to_send',
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, approval["queue_id"]),
            )

        db.log_audit(
            conn,
            tenant_id,
            user_id,
            "email_send_approval_revoked",
            "email_send_approvals",
            approval_id,
            approval,
            {"approval_status": "revoked"},
            {"dry_run_only": True},
            ip_address,
        )

        if queue_item:
            conn.execute(
                """
                INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
                VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
                """,
                (
                    tenant_id,
                    queue_item["task_id"],
                    "Email send approval revoked. No email has been sent.",
                ),
            )

        row = conn.execute(
            """
            SELECT *
            FROM email_send_approvals
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, approval_id),
        ).fetchone()
        return _as_dict(row)

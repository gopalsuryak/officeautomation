import os

import db


def _truthy_env(name):
    value = (os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_email_qa_summary(tenant_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*)
                 FROM document_communication_drafts d
                 WHERE d.tenant_id = ?
                   AND d.draft_type = 'email') AS total_drafts,
                (SELECT COUNT(*)
                 FROM document_communication_drafts d
                 WHERE d.tenant_id = ?
                   AND d.draft_type = 'email'
                   AND d.status = 'draft') AS drafts_awaiting_review,
                (SELECT COUNT(*)
                 FROM document_communication_drafts d
                 LEFT JOIN email_send_queue q
                   ON q.tenant_id = d.tenant_id
                  AND q.draft_id = d.id
                 WHERE d.tenant_id = ?
                   AND d.draft_type = 'email'
                   AND d.status = 'reviewed'
                   AND q.id IS NULL) AS reviewed_not_queued,
                (SELECT COUNT(*)
                 FROM email_send_queue q
                 WHERE q.tenant_id = ?
                   AND q.status IN ('queued', 'ready_to_send', 'approved_to_send')
                   AND q.provider_setting_id IS NULL) AS queued_without_provider,
                (SELECT COUNT(*)
                 FROM email_send_queue q
                 WHERE q.tenant_id = ?
                   AND q.status = 'ready_to_send') AS ready_to_send_count,
                (SELECT COUNT(*)
                 FROM email_send_queue q
                 WHERE q.tenant_id = ?
                   AND q.status = 'approved_to_send') AS approved_to_send_count,
                (SELECT COUNT(*)
                 FROM email_send_queue q
                 WHERE q.tenant_id = ?
                   AND q.status = 'failed') AS failed_count,
                (SELECT COUNT(*)
                 FROM email_send_queue q
                 LEFT JOIN email_failure_reviews fr
                   ON fr.tenant_id = q.tenant_id
                  AND fr.queue_id = q.id
                  AND fr.id = (
                        SELECT MAX(fr2.id)
                        FROM email_failure_reviews fr2
                        WHERE fr2.tenant_id = q.tenant_id
                          AND fr2.queue_id = q.id
                  )
                 WHERE q.tenant_id = ?
                   AND q.status = 'failed'
                   AND (fr.id IS NULL OR COALESCE(fr.review_status, '') != 'reopened')) AS failed_unreviewed_count,
                (SELECT COUNT(*)
                 FROM email_send_queue q
                 WHERE q.tenant_id = ?
                   AND q.status = 'sent'
                   AND strftime('%Y-%m', q.sent_at) = strftime('%Y-%m', 'now')) AS sent_this_month,
                (SELECT COUNT(*)
                 FROM email_provider_settings p
                 WHERE p.tenant_id = ?) AS providers_total,
                (SELECT COUNT(*)
                 FROM email_provider_settings p
                 WHERE p.tenant_id = ?
                   AND p.status = 'active') AS active_providers,
                (SELECT COUNT(*)
                 FROM email_provider_settings p
                 WHERE p.tenant_id = ?
                   AND p.status = 'active'
                   AND COALESCE(p.last_check_status, '') = 'incomplete') AS providers_incomplete,
                (SELECT COUNT(*)
                 FROM email_provider_settings p
                 WHERE p.tenant_id = ?
                   AND p.status = 'error') AS providers_error,
                (SELECT COUNT(*)
                 FROM email_provider_settings p
                 WHERE p.tenant_id = ?
                   AND COALESCE(p.last_check_status, '') = '') AS providers_without_readiness_check,
                (SELECT COUNT(*)
                 FROM email_dry_run_previews p
                 WHERE p.tenant_id = ?) AS dry_runs_generated,
                (SELECT COUNT(*)
                 FROM email_send_approvals a
                 WHERE a.tenant_id = ?
                   AND a.approval_status = 'approved') AS approvals_active,
                (SELECT COUNT(*)
                 FROM email_send_approvals a
                 WHERE a.tenant_id = ?
                   AND a.approval_status = 'revoked') AS approvals_revoked,
                (SELECT COUNT(*)
                 FROM email_provider_settings p
                 WHERE p.tenant_id = ?
                   AND (
                        COALESCE(p.status, '') != 'active'
                        OR COALESCE(p.last_check_status, '') = ''
                        OR COALESCE(p.last_check_status, '') != 'ready'
                        OR COALESCE(TRIM(p.from_email), '') = ''
                        OR (
                            p.provider_type = 'smtp'
                            AND (
                                COALESCE(TRIM(p.smtp_host), '') = ''
                                OR p.smtp_port IS NULL
                                OR COALESCE(TRIM(p.smtp_username), '') = ''
                                OR COALESCE(TRIM(p.smtp_password_secret), '') = ''
                            )
                        )
                   )) AS providers_needing_attention
            """,
            (
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
                tenant_id,
            ),
        ).fetchone()

    row_dict = dict(row or {})
    return {
        "total_drafts": int(row_dict.get("total_drafts") or 0),
        "drafts_awaiting_review": int(row_dict.get("drafts_awaiting_review") or 0),
        "reviewed_not_queued": int(row_dict.get("reviewed_not_queued") or 0),
        "queued_without_provider": int(row_dict.get("queued_without_provider") or 0),
        "ready_to_send_count": int(row_dict.get("ready_to_send_count") or 0),
        "approved_to_send_count": int(row_dict.get("approved_to_send_count") or 0),
        "failed_count": int(row_dict.get("failed_count") or 0),
        "failed_unreviewed_count": int(row_dict.get("failed_unreviewed_count") or 0),
        "sent_this_month": int(row_dict.get("sent_this_month") or 0),
        "providers_total": int(row_dict.get("providers_total") or 0),
        "active_providers": int(row_dict.get("active_providers") or 0),
        "providers_incomplete": int(row_dict.get("providers_incomplete") or 0),
        "providers_error": int(row_dict.get("providers_error") or 0),
        "providers_without_readiness_check": int(row_dict.get("providers_without_readiness_check") or 0),
        "dry_runs_generated": int(row_dict.get("dry_runs_generated") or 0),
        "approvals_active": int(row_dict.get("approvals_active") or 0),
        "approvals_revoked": int(row_dict.get("approvals_revoked") or 0),
        "providers_needing_attention": int(row_dict.get("providers_needing_attention") or 0),
    }


def get_providers_needing_attention(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))

    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id AS provider_id,
                p.display_name,
                p.provider_type,
                p.from_email,
                p.status,
                p.last_check_status,
                p.last_error,
                p.smtp_host,
                p.smtp_port,
                p.smtp_username,
                CASE
                    WHEN p.provider_type = 'smtp' AND COALESCE(TRIM(p.smtp_password_secret), '') = '' THEN 1
                    ELSE 0
                END AS smtp_password_secret_missing
            FROM email_provider_settings p
            WHERE p.tenant_id = ?
              AND (
                    COALESCE(p.status, '') != 'active'
                    OR COALESCE(p.last_check_status, '') = ''
                    OR COALESCE(p.last_check_status, '') != 'ready'
                    OR COALESCE(TRIM(p.from_email), '') = ''
                    OR (
                        p.provider_type = 'smtp'
                        AND (
                            COALESCE(TRIM(p.smtp_host), '') = ''
                            OR p.smtp_port IS NULL
                            OR COALESCE(TRIM(p.smtp_username), '') = ''
                            OR COALESCE(TRIM(p.smtp_password_secret), '') = ''
                        )
                    )
              )
            ORDER BY
                CASE WHEN COALESCE(p.status, '') = 'error' THEN 0 ELSE 1 END,
                COALESCE(p.updated_at, p.created_at) DESC,
                p.id DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()

    providers = []
    for row in rows:
        item = dict(row)
        issues = []

        if item.get("status") != "active":
            issues.append("Provider status is not active")
        if not (item.get("last_check_status") or "").strip():
            issues.append("Readiness check not run")
        elif item.get("last_check_status") != "ready":
            issues.append(f"Readiness is {item.get('last_check_status')}")
        if not (item.get("from_email") or "").strip():
            issues.append("From email is missing")
        if item.get("provider_type") == "smtp":
            if not (item.get("smtp_host") or "").strip():
                issues.append("SMTP host missing")
            if item.get("smtp_port") is None:
                issues.append("SMTP port missing")
            if not (item.get("smtp_username") or "").strip():
                issues.append("SMTP username missing")
                if item.get("smtp_password_secret_missing"):
                    issues.append("SMTP password secret missing")

        providers.append(
            {
                "provider_id": item.get("provider_id"),
                "display_name": item.get("display_name"),
                "provider_type": item.get("provider_type"),
                "from_email": item.get("from_email"),
                "status": item.get("status"),
                "last_check_status": item.get("last_check_status"),
                "last_error": item.get("last_error"),
                "issue_summary": "; ".join(issues) if issues else "Needs review",
            }
        )

    return providers


def get_failed_items_needing_review(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))

    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                q.id AS queue_id,
                c.name AS client_name,
                t.title AS task_title,
                eps.display_name AS provider_display_name,
                q.to_email,
                q.subject,
                q.failed_at,
                q.error_message
            FROM email_send_queue q
            LEFT JOIN client_entities c
              ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            LEFT JOIN compliance_tasks t
              ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps
              ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            LEFT JOIN email_failure_reviews fr
              ON fr.tenant_id = q.tenant_id
             AND fr.queue_id = q.id
             AND fr.id = (
                SELECT MAX(fr2.id)
                FROM email_failure_reviews fr2
                WHERE fr2.tenant_id = q.tenant_id
                  AND fr2.queue_id = q.id
             )
            WHERE q.tenant_id = ?
              AND q.status = 'failed'
              AND (fr.id IS NULL OR COALESCE(fr.review_status, '') != 'reopened')
            ORDER BY datetime(COALESCE(q.failed_at, q.updated_at, q.queued_at)) DESC, q.id DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_approved_items_pending_send(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))

    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                q.id AS queue_id,
                c.name AS client_name,
                t.title AS task_title,
                eps.display_name AS provider_display_name,
                q.to_email,
                q.subject,
                ap.approved_at,
                q.queued_at
            FROM email_send_queue q
            LEFT JOIN client_entities c
              ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            LEFT JOIN compliance_tasks t
              ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps
              ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            LEFT JOIN email_send_approvals ap
              ON ap.tenant_id = q.tenant_id
             AND ap.queue_id = q.id
             AND ap.id = (
                SELECT MAX(ap2.id)
                FROM email_send_approvals ap2
                WHERE ap2.tenant_id = q.tenant_id
                  AND ap2.queue_id = q.id
             )
            WHERE q.tenant_id = ?
              AND q.status = 'approved_to_send'
            ORDER BY datetime(COALESCE(ap.approved_at, q.updated_at, q.queued_at)) DESC, q.id DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_failure_rate_by_provider(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))

    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                eps.id AS provider_id,
                eps.display_name,
                eps.provider_type,
                SUM(CASE WHEN q.status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                SUM(CASE WHEN q.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                COUNT(*) AS total_attempts,
                ROUND(
                    (SUM(CASE WHEN q.status = 'failed' THEN 1 ELSE 0 END) * 100.0)
                    / NULLIF(COUNT(*), 0),
                    2
                ) AS failure_rate_percent
            FROM email_send_queue q
            JOIN email_provider_settings eps
              ON eps.id = q.provider_setting_id
             AND eps.tenant_id = q.tenant_id
            WHERE q.tenant_id = ?
              AND q.status IN ('sent', 'failed')
            GROUP BY eps.id, eps.display_name, eps.provider_type
            ORDER BY
                COALESCE(failure_rate_percent, 0) DESC,
                total_attempts DESC,
                eps.display_name ASC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        result.append(
            {
                "provider_id": item.get("provider_id"),
                "display_name": item.get("display_name"),
                "provider_type": item.get("provider_type"),
                "sent_count": int(item.get("sent_count") or 0),
                "failed_count": int(item.get("failed_count") or 0),
                "total_attempts": int(item.get("total_attempts") or 0),
                "failure_rate_percent": float(item.get("failure_rate_percent") or 0.0),
            }
        )
    return result


def get_safety_checklist(tenant_id):
    summary = get_email_qa_summary(tenant_id)

    with db.get_db() as conn:
        default_provider_row = conn.execute(
            """
            SELECT id
            FROM email_provider_settings
            WHERE tenant_id = ?
              AND is_default = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (tenant_id,),
        ).fetchone()

        ready_provider_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_provider_settings
            WHERE tenant_id = ?
              AND status = 'active'
              AND COALESCE(last_check_status, '') = 'ready'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        delivery_log_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_send_queue
            WHERE tenant_id = ?
              AND status IN ('approved_to_send', 'ready_to_send', 'sent', 'failed')
            """,
            (tenant_id,),
        ).fetchone()["c"]

    has_background_worker_enabled = any(
        _truthy_env(name)
        for name in [
            "EMAIL_BACKGROUND_WORKER",
            "EMAIL_BACKGROUND_WORKER_ENABLED",
            "SMTP_BACKGROUND_WORKER",
        ]
    )

    checks = []

    checks.append(
        {
            "name": "has_active_provider",
            "status": "pass" if summary["active_providers"] > 0 else "fail",
            "detail": (
                f"{summary['active_providers']} active provider(s) configured."
                if summary["active_providers"] > 0
                else "No active provider found."
            ),
        }
    )

    checks.append(
        {
            "name": "has_default_provider",
            "status": "pass" if default_provider_row else "fail",
            "detail": "Default provider is configured." if default_provider_row else "No default provider configured.",
        }
    )

    checks.append(
        {
            "name": "has_ready_provider",
            "status": "pass" if int(ready_provider_count or 0) > 0 else "warning",
            "detail": (
                f"{int(ready_provider_count or 0)} provider(s) report ready readiness status."
                if int(ready_provider_count or 0) > 0
                else "No provider with readiness status 'ready'."
            ),
        }
    )

    checks.append(
        {
            "name": "has_approved_pending_items",
            "status": "pass" if summary["approved_to_send_count"] > 0 else "warning",
            "detail": (
                f"{summary['approved_to_send_count']} item(s) pending manual send confirmation."
                if summary["approved_to_send_count"] > 0
                else "No approved_to_send items currently pending."
            ),
        }
    )

    checks.append(
        {
            "name": "has_failed_unreviewed_items",
            "status": "warning" if summary["failed_unreviewed_count"] > 0 else "pass",
            "detail": (
                f"{summary['failed_unreviewed_count']} failed item(s) need review/reopen decision."
                if summary["failed_unreviewed_count"] > 0
                else "No failed items currently pending review."
            ),
        }
    )

    checks.append(
        {
            "name": "has_delivery_logs",
            "status": "pass" if int(delivery_log_count or 0) > 0 else "warning",
            "detail": (
                f"{int(delivery_log_count or 0)} delivery-log eligible record(s) found."
                if int(delivery_log_count or 0) > 0
                else "No delivery-log records yet (approved/ready/sent/failed)."
            ),
        }
    )

    checks.append(
        {
            "name": "has_no_bulk_send_route",
            "status": "pass",
            "detail": "Bulk send route is not implemented; sending remains one queue item at a time.",
        }
    )

    checks.append(
        {
            "name": "has_no_background_worker_flag",
            "status": "fail" if has_background_worker_enabled else "pass",
            "detail": (
                "Background worker flag appears enabled in environment; verify this phase is manual-only."
                if has_background_worker_enabled
                else "No background worker flag enabled for email dispatch."
            ),
        }
    )

    return checks

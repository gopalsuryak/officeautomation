import db

READINESS_STATUSES = {"pending", "completed", "blocked", "not_applicable"}

DEFAULT_READINESS_CHECKS = [
    {
        "check_key": "internal_smtp_provider_created",
        "title": "Internal SMTP Provider Created",
        "description": "Create and verify an internal SMTP provider profile for controlled testing.",
        "required_before_client_use": True,
    },
    {
        "check_key": "internal_test_email_sent",
        "title": "Internal Test Email Sent",
        "description": "Send at least one internal-only SMTP test email outside client communication.",
        "required_before_client_use": True,
    },
    {
        "check_key": "delivery_log_verified",
        "title": "Delivery Log Verified",
        "description": "Confirm sent or failed outcomes appear correctly in delivery logs.",
        "required_before_client_use": True,
    },
    {
        "check_key": "qa_dashboard_reviewed",
        "title": "QA Dashboard Reviewed",
        "description": "Review Email QA Dashboard KPIs and safety checklist before client usage.",
        "required_before_client_use": True,
    },
    {
        "check_key": "failed_send_flow_tested",
        "title": "Failed Send Flow Tested",
        "description": "Confirm failure review and manual reopen flow behaves as expected.",
        "required_before_client_use": True,
    },
    {
        "check_key": "password_not_visible_verified",
        "title": "Password Visibility Verified",
        "description": "Verify SMTP password/secret values are never shown in UI or logs.",
        "required_before_client_use": True,
    },
    {
        "check_key": "no_bulk_send_verified",
        "title": "No Bulk Send Verified",
        "description": "Confirm there is no bulk-send action in current email module UI/routes.",
        "required_before_client_use": True,
    },
    {
        "check_key": "no_background_worker_verified",
        "title": "No Background Worker Verified",
        "description": "Confirm email sending is manual-only with no background worker enabled.",
        "required_before_client_use": True,
    },
    {
        "check_key": "client_email_testing_approved",
        "title": "Client Email Testing Approved",
        "description": "Internal owner/manager approval recorded before controlled client email use.",
        "required_before_client_use": True,
    },
]


def _default_map():
    return {item["check_key"]: item for item in DEFAULT_READINESS_CHECKS}


def ensure_readiness_checks(tenant_id):
    default_by_key = _default_map()

    with db.get_db() as conn:
        existing_rows = conn.execute(
            """
            SELECT check_key
            FROM email_readiness_checks
            WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchall()
        existing_keys = {row["check_key"] for row in existing_rows}

        for check in DEFAULT_READINESS_CHECKS:
            if check["check_key"] in existing_keys:
                continue
            conn.execute(
                """
                INSERT INTO email_readiness_checks (
                    tenant_id, check_key, status, notes, completed_by, completed_at, created_at, updated_at
                ) VALUES (?, ?, 'pending', NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (tenant_id, check["check_key"]),
            )


def get_email_readiness_status(tenant_id):
    defaults = _default_map()

    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM email_readiness_checks
            WHERE tenant_id = ?
            ORDER BY id ASC
            """,
            (tenant_id,),
        ).fetchall()

    by_key = {}
    for row in rows:
        item = dict(row)
        by_key[item["check_key"]] = item

    checks = []
    for default in DEFAULT_READINESS_CHECKS:
        item = by_key.get(default["check_key"])
        if not item:
            item = {
                "check_key": default["check_key"],
                "status": "pending",
                "notes": None,
                "completed_at": None,
            }
        checks.append(
            {
                "check_key": default["check_key"],
                "title": default["title"],
                "description": default["description"],
                "required_before_client_use": bool(default["required_before_client_use"]),
                "status": item.get("status") or "pending",
                "notes": item.get("notes"),
                "completed_at": item.get("completed_at"),
            }
        )

    total_checks = len(checks)
    completed_count = sum(1 for c in checks if c["status"] == "completed")
    blocked_count = sum(1 for c in checks if c["status"] == "blocked")
    pending_count = sum(1 for c in checks if c["status"] == "pending")

    required_checks = [c for c in checks if c["required_before_client_use"]]
    required_total_count = len(required_checks)
    required_completed_count = sum(1 for c in required_checks if c["status"] == "completed")
    is_ready_for_client_use = required_completed_count == required_total_count

    return {
        "checks": checks,
        "total_checks": total_checks,
        "completed_count": completed_count,
        "blocked_count": blocked_count,
        "pending_count": pending_count,
        "required_completed_count": required_completed_count,
        "required_total_count": required_total_count,
        "is_ready_for_client_use": is_ready_for_client_use,
    }


def update_readiness_check(tenant_id, check_key, status, notes=None, user_id=None, ip_address=None):
    status = (status or "").strip().lower()
    if status not in READINESS_STATUSES:
        raise ValueError("Invalid readiness status")

    default = _default_map().get(check_key)
    if not default:
        raise ValueError("Unknown readiness check")

    notes_clean = (notes or "").strip() or None
    completed_at_value = "CURRENT_TIMESTAMP" if status == "completed" else "NULL"
    completed_by_value = user_id if status == "completed" else None

    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM email_readiness_checks
            WHERE tenant_id = ? AND check_key = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (tenant_id, check_key),
        ).fetchone()

        if row:
            old_value = dict(row)
            conn.execute(
                f"""
                UPDATE email_readiness_checks
                SET status = ?,
                    notes = ?,
                    completed_by = ?,
                    completed_at = {completed_at_value},
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = ? AND check_key = ?
                """,
                (status, notes_clean, completed_by_value, tenant_id, check_key),
            )
        else:
            old_value = None
            conn.execute(
                f"""
                INSERT INTO email_readiness_checks (
                    tenant_id, check_key, status, notes, completed_by, completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, {completed_at_value}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (tenant_id, check_key, status, notes_clean, completed_by_value),
            )

        updated = conn.execute(
            """
            SELECT *
            FROM email_readiness_checks
            WHERE tenant_id = ? AND check_key = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (tenant_id, check_key),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="email_readiness_check_updated",
            entity_type="email_readiness_checks",
            entity_id=check_key,
            old_value=old_value,
            new_value=dict(updated) if updated else None,
            metadata={
                "check_key": check_key,
                "title": default["title"],
                "required_before_client_use": bool(default["required_before_client_use"]),
                "status": status,
            },
            ip_address=ip_address,
        )

    return dict(updated) if updated else None

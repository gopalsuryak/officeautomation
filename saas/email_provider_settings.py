import json

import db
from credential_vault import encrypt_secret, mask_secret

PROVIDER_TYPES = ["smtp", "gmail", "zoho"]
PROVIDER_DISPLAY_NAMES = {
    "smtp": "SMTP",
    "gmail": "Gmail",
    "zoho": "Zoho Mail",
}
PROVIDER_STATUSES = ["draft", "active", "inactive", "error"]
OAUTH_STATUSES = ["not_configured", "pending", "connected", "expired", "revoked"]


def _clean(value):
    return (value or "").strip()


def _as_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(row)


def _sanitize_provider_row(row):
    item = _as_dict(row) or {}
    secret_raw = item.get("smtp_password_secret")
    item["secret_masked"] = mask_secret(secret_raw)
    item.pop("smtp_password_secret", None)
    return item


def mask_secret(value):
    if not value:
        return "Not stored"
    return "Stored / hidden"


def create_provider_setting(tenant_id, payload, user_id=None, ip_address=None):
    provider_type = _clean((payload or {}).get("provider_type")).lower()
    if provider_type not in PROVIDER_TYPES:
        raise ValueError("Invalid provider type")

    display_name = _clean(payload.get("display_name"))
    from_email = _clean(payload.get("from_email"))
    from_name = _clean(payload.get("from_name")) or None
    if not display_name:
        raise ValueError("Display name is required")
    if not from_email:
        raise ValueError("From email is required")

    smtp_host = None
    smtp_port = None
    smtp_username = None
    smtp_password_secret = None
    oauth_client_id = None
    oauth_status = "not_configured"

    if provider_type == "smtp":
        smtp_host = _clean(payload.get("smtp_host")) or None
        raw_port = _clean(payload.get("smtp_port"))
        if raw_port:
            try:
                smtp_port = int(raw_port)
            except ValueError as exc:
                raise ValueError("SMTP port must be a valid integer") from exc
        smtp_username = _clean(payload.get("smtp_username")) or None
        smtp_password_secret = encrypt_secret(_clean(payload.get("smtp_password_secret")) or None)
    else:
        oauth_client_id = _clean(payload.get("oauth_client_id")) or None
        requested_oauth_status = _clean(payload.get("oauth_status")).lower() or "not_configured"
        oauth_status = requested_oauth_status if requested_oauth_status in OAUTH_STATUSES else "not_configured"

    metadata_value = payload.get("metadata_json")
    metadata_json = None
    if isinstance(metadata_value, (dict, list)):
        metadata_json = json.dumps(metadata_value, ensure_ascii=False)
    elif isinstance(metadata_value, str):
        metadata_json = metadata_value.strip() or None

    with db.get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO email_provider_settings (
                tenant_id,
                provider_type,
                display_name,
                from_name,
                from_email,
                smtp_host,
                smtp_port,
                smtp_username,
                smtp_password_secret,
                oauth_client_id,
                oauth_status,
                status,
                is_default,
                metadata_json,
                created_by,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 0, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                tenant_id,
                provider_type,
                display_name,
                from_name,
                from_email,
                smtp_host,
                smtp_port,
                smtp_username,
                smtp_password_secret,
                oauth_client_id,
                oauth_status,
                metadata_json,
                user_id,
            ),
        )
        provider_id = cur.lastrowid

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="email_provider_setting_created",
            entity_type="email_provider_settings",
            entity_id=provider_id,
            old_value=None,
            new_value={
                "provider_type": provider_type,
                "display_name": display_name,
                "from_email": from_email,
                "status": "draft",
            },
            metadata={
                "from_name": from_name,
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "smtp_username": smtp_username,
                "smtp_password_secret": mask_secret(smtp_password_secret),
                "oauth_client_id": oauth_client_id,
                "oauth_status": oauth_status,
            },
            ip_address=ip_address,
        )

        row = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ?",
            (tenant_id, provider_id),
        ).fetchone()
        return _sanitize_provider_row(row)


def list_provider_settings(tenant_id, filters=None):
    filters = filters or {}
    where = ["tenant_id = ?"]
    params = [tenant_id]

    provider_type = _clean(filters.get("provider_type")).lower()
    if provider_type:
        where.append("provider_type = ?")
        params.append(provider_type)

    status = _clean(filters.get("status")).lower()
    if status:
        where.append("status = ?")
        params.append(status)

    search = _clean(filters.get("search")).lower()
    if search:
        where.append(
            "(" +
            "LOWER(display_name) LIKE ? OR " +
            "LOWER(COALESCE(from_email, '')) LIKE ? OR " +
            "LOWER(COALESCE(smtp_host, '')) LIKE ? OR " +
            "LOWER(COALESCE(smtp_username, '')) LIKE ?" +
            ")"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])

    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM email_provider_settings
            WHERE {' AND '.join(where)}
            ORDER BY is_default DESC, updated_at DESC, id DESC
            """,
            params,
        ).fetchall()
    return [_sanitize_provider_row(row) for row in rows]


def get_provider_setting(tenant_id, provider_id):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, provider_id),
        ).fetchone()
    return _sanitize_provider_row(row)


def update_provider_status(tenant_id, provider_id, status, user_id=None, ip_address=None):
    target_status = _clean(status).lower()
    if target_status not in PROVIDER_STATUSES:
        raise ValueError("Invalid provider status")

    with db.get_db() as conn:
        current = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, provider_id),
        ).fetchone()
        if not current:
            raise ValueError("Provider setting not found")

        conn.execute(
            """
            UPDATE email_provider_settings
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (target_status, tenant_id, provider_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="email_provider_setting_status_changed",
            entity_type="email_provider_settings",
            entity_id=provider_id,
            old_value={"status": _as_dict(current).get("status")},
            new_value={"status": target_status},
            metadata=None,
            ip_address=ip_address,
        )

        updated = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, provider_id),
        ).fetchone()
        return _sanitize_provider_row(updated)


def set_default_provider(tenant_id, provider_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        current = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, provider_id),
        ).fetchone()
        if not current:
            raise ValueError("Provider setting not found")

        conn.execute(
            "UPDATE email_provider_settings SET is_default = 0, updated_at = CURRENT_TIMESTAMP WHERE tenant_id = ?",
            (tenant_id,),
        )
        conn.execute(
            "UPDATE email_provider_settings SET is_default = 1, updated_at = CURRENT_TIMESTAMP WHERE tenant_id = ? AND id = ?",
            (tenant_id, provider_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="email_provider_setting_default_changed",
            entity_type="email_provider_settings",
            entity_id=provider_id,
            old_value={"is_default": _as_dict(current).get("is_default")},
            new_value={"is_default": 1},
            metadata=None,
            ip_address=ip_address,
        )

        updated = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, provider_id),
        ).fetchone()
        return _sanitize_provider_row(updated)


def simulate_provider_check(tenant_id, provider_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, provider_id),
        ).fetchone()
        if not row:
            raise ValueError("Provider setting not found")

        provider = _as_dict(row)
        provider_type = (provider.get("provider_type") or "").lower()
        errors = []

        if provider_type == "smtp":
            if not _clean(provider.get("smtp_host")):
                errors.append("smtp_host is required")
            if not provider.get("smtp_port"):
                errors.append("smtp_port is required")
            if not _clean(provider.get("smtp_username")):
                errors.append("smtp_username is required")
            if not _clean(provider.get("smtp_password_secret")):
                errors.append("smtp_password_secret is required")
        elif provider_type in {"gmail", "zoho"}:
            if not _clean(provider.get("from_email")):
                errors.append("from_email is required")
            if (provider.get("oauth_status") or "not_configured") != "connected":
                errors.append("oauth_status must be connected")
        else:
            errors.append("provider_type is invalid")

        if errors:
            check_status = "incomplete"
            last_error = "; ".join(errors)
        else:
            check_status = "ready"
            last_error = None

        conn.execute(
            """
            UPDATE email_provider_settings
            SET last_checked_at = CURRENT_TIMESTAMP,
                last_check_status = ?,
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (check_status, last_error, tenant_id, provider_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="email_provider_setting_checked",
            entity_type="email_provider_settings",
            entity_id=provider_id,
            old_value=None,
            new_value={
                "last_check_status": check_status,
                "last_error": last_error,
                "note": "Local readiness check only.",
            },
            metadata={"local_only": True},
            ip_address=ip_address,
        )

        updated = conn.execute(
            "SELECT * FROM email_provider_settings WHERE tenant_id = ? AND id = ? LIMIT 1",
            (tenant_id, provider_id),
        ).fetchone()

    sanitized = _sanitize_provider_row(updated)
    return {
        "provider": sanitized,
        "ready": check_status == "ready",
        "last_check_status": check_status,
        "last_error": last_error,
        "message": "Local readiness check only.",
    }

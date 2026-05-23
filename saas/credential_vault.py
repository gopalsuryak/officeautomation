import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import db

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - handled at runtime when encryption is used
    Fernet = None
    InvalidToken = Exception

PORTAL_TYPES = {
    "gst": "GST Portal",
    "income_tax": "Income Tax Portal",
    "mca": "MCA Portal",
    "traces": "TRACES Portal",
    "pf": "PF Portal",
    "esi": "ESI Portal",
    "professional_tax": "Professional Tax Portal",
    "bank": "Bank Portal",
    "zoho_books": "Zoho Books",
    "tally_bridge": "Tally Bridge",
    "other": "Other",
}

PORTAL_URLS = {
    "gst": "https://services.gst.gov.in/services/login",
    "income_tax": "https://eportal.incometax.gov.in/iec/foservices/#/login",
    "mca": "https://www.mca.gov.in/content/mca/global/en/mca/login.html",
    "traces": "https://www.tdscpc.gov.in/app/login.xhtml",
    "pf": "https://unifiedportal-emp.epfindia.gov.in/epfo/",
    "esi": "https://www.esic.gov.in/",
    "professional_tax": "",
    "bank": "",
    "zoho_books": "https://books.zoho.in/",
    "tally_bridge": "http://localhost:8799/",
    "other": "",
}

ALLOWED_STATUSES = {
    "draft",
    "available",
    "missing",
    "expired",
    "locked",
    "disabled",
    "error",
}

ENCRYPTION_PLACEHOLDER = "[ENCRYPTION_NOT_CONFIGURED]"
_ENCRYPTION_KEY_ENV = "CA_ASSIST_ENCRYPTION_KEY"


def _is_production() -> bool:
    env = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
    return env in {"prod", "production"}


def _load_fernet() -> Fernet:
    if Fernet is None:
        raise RuntimeError("Secret encryption requires the cryptography package.")

    key = (os.environ.get(_ENCRYPTION_KEY_ENV) or "").strip()
    if not key:
        message = f"{_ENCRYPTION_KEY_ENV} is required to store or open secrets."
        if _is_production():
            raise RuntimeError(message)
        raise ValueError(message)

    return Fernet(key.encode("utf-8"))


def encrypt_secret(secret_value: str | None) -> str | None:
    cleaned = _clean_text(secret_value)
    if not cleaned:
        return None
    return _load_fernet().encrypt(cleaned.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_value: str | None) -> str | None:
    cleaned = _clean_text(encrypted_value)
    if not cleaned:
        return None
    if cleaned == ENCRYPTION_PLACEHOLDER:
        raise ValueError("Stored secret is not encrypted yet. Re-enter it after configuring encryption.")

    try:
        return _load_fernet().decrypt(cleaned.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logging.exception("Failed to decrypt stored secret")
        raise ValueError("Stored secret cannot be decrypted. Re-enter it after configuring encryption.") from exc


def mask_secret(value: str | None) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return "Not stored"
    if cleaned == ENCRYPTION_PLACEHOLDER:
        return "Needs re-entry"
    return "Stored / hidden"


def _secret_is_available(value: str | None) -> bool:
    cleaned = _clean_text(value)
    return bool(cleaned) and cleaned != ENCRYPTION_PLACEHOLDER


def is_secret_available(value: str | None) -> bool:
    """Public wrapper for secret availability check."""
    return _secret_is_available(value)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _to_bool_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None:
        return 0
    return 1 if str(value).strip().lower() in {"1", "true", "yes", "on"} else 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_portal_url(portal_type):
    portal_type = _clean_text(portal_type)
    if not portal_type:
        return ""
    return PORTAL_URLS.get(portal_type, "")


def get_credential_readiness(credential):
    credential = credential or {}

    username_available = bool(_clean_text(credential.get("username")))
    secret_available = _secret_is_available(credential.get("secret_value_encrypted"))
    otp_required = bool(credential.get("otp_required"))
    status = (_clean_text(credential.get("status")) or "draft").lower()
    can_open_portal = bool(get_portal_url(credential.get("portal_type")))
    can_auto_login = False

    ready_statuses = {"available", "draft", "verified"}

    if username_available and secret_available and status in ready_statuses:
        readiness_status = "ready"
        readiness_message = "Credential is ready for guided portal use. Auto login is not enabled in this phase."
    elif username_available and not secret_available:
        readiness_status = "partial"
        readiness_message = "Username is present, but secret storage is missing or not configured."
    elif not username_available and not secret_available:
        readiness_status = "not_ready"
        readiness_message = "Username and secret are both missing. Add credential details to proceed."
    else:
        readiness_status = "partial"
        readiness_message = "Credential is partially configured. Complete missing fields before use."

    return {
        "username_available": username_available,
        "secret_available": secret_available,
        "otp_required": otp_required,
        "status": status,
        "can_open_portal": can_open_portal,
        "can_auto_login": can_auto_login,
        "readiness_status": readiness_status,
        "readiness_message": readiness_message,
    }


def _row_to_safe_dict(row):
    if not row:
        return None
    data = dict(row)
    raw_secret = data.get("secret_value_encrypted")
    data["username_available"] = bool(_clean_text(data.get("username")))
    data["secret_available"] = _secret_is_available(raw_secret)
    data["secret_masked"] = mask_secret(raw_secret)
    data["otp_required"] = bool(data.get("otp_required"))
    data["portal_url"] = get_portal_url(data.get("portal_type"))
    data["readiness"] = get_credential_readiness({**data, "secret_value_encrypted": raw_secret})
    data.pop("secret_value_encrypted", None)
    return data


def create_credential_record(
    tenant_id,
    client_entity_id,
    portal_type,
    display_name,
    username=None,
    secret_value=None,
    secret_hint=None,
    otp_required=False,
    user_id=None,
    ip_address=None,
):
    portal_type = _clean_text(portal_type)
    display_name = _clean_text(display_name)
    username = _clean_text(username)
    secret_hint = _clean_text(secret_hint)

    if portal_type not in PORTAL_TYPES:
        raise ValueError("Invalid portal type.")
    if not display_name:
        raise ValueError("Display name is required.")

    with db.get_db() as conn:
        client = conn.execute(
            """
            SELECT id, name
            FROM client_entities
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, client_entity_id),
        ).fetchone()
        if not client:
            raise ValueError("Client not found for tenant.")

        secret_value_encrypted = None
        if _clean_text(secret_value):
            secret_value_encrypted = encrypt_secret(secret_value)

        status = "available" if username else "draft"
        metadata_json = json.dumps({"portal_type": portal_type}, ensure_ascii=False)
        now_iso = _now_iso()

        cur = conn.execute(
            """
            INSERT INTO client_credentials (
                tenant_id,
                client_entity_id,
                portal_type,
                display_name,
                username,
                secret_value_encrypted,
                secret_hint,
                otp_required,
                status,
                metadata_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                client_entity_id,
                portal_type,
                display_name,
                username,
                secret_value_encrypted,
                secret_hint,
                _to_bool_int(otp_required),
                status,
                metadata_json,
                now_iso,
                now_iso,
            ),
        )

        credential_id = cur.lastrowid
        row = conn.execute(
            """
            SELECT cc.*, ce.name AS client_name
            FROM client_credentials cc
            JOIN client_entities ce ON ce.id = cc.client_entity_id
            WHERE cc.tenant_id = ? AND cc.id = ?
            LIMIT 1
            """,
            (tenant_id, credential_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="credential_record_created",
            entity_type="client_credential",
            entity_id=credential_id,
            old_value=None,
            new_value={
                "client_entity_id": client_entity_id,
                "portal_type": portal_type,
                "display_name": display_name,
                "username_available": bool(username),
                "secret_available": bool(secret_value_encrypted),
                "otp_required": bool(_to_bool_int(otp_required)),
                "status": status,
            },
            metadata={"client_name": client["name"]},
            ip_address=ip_address,
        )

        return _row_to_safe_dict(row)


def list_credentials(tenant_id, filters=None):
    filters = filters or {}

    where = ["cc.tenant_id = ?"]
    params: list[Any] = [tenant_id]

    client_entity_id = _clean_text(filters.get("client_entity_id"))
    portal_type = _clean_text(filters.get("portal_type"))
    status = _clean_text(filters.get("status"))
    search = _clean_text(filters.get("search"))

    if client_entity_id:
        where.append("CAST(cc.client_entity_id AS TEXT) = ?")
        params.append(client_entity_id)

    if portal_type and portal_type in PORTAL_TYPES:
        where.append("cc.portal_type = ?")
        params.append(portal_type)

    if status and status in ALLOWED_STATUSES:
        where.append("cc.status = ?")
        params.append(status)

    if search:
        like = f"%{search}%"
        where.append(
            """
            (
                cc.display_name LIKE ? OR
                cc.username LIKE ? OR
                cc.portal_type LIKE ? OR
                ce.name LIKE ? OR
                cc.secret_hint LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like])

    query = f"""
        SELECT
            cc.*,
            ce.name AS client_name
        FROM client_credentials cc
        JOIN client_entities ce ON ce.id = cc.client_entity_id
        WHERE {' AND '.join(where)}
        ORDER BY datetime(cc.updated_at) DESC, cc.id DESC
    """

    with db.get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return [_row_to_safe_dict(row) for row in rows]


def get_credential(tenant_id, credential_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT
                cc.*,
                ce.name AS client_name
            FROM client_credentials cc
            JOIN client_entities ce ON ce.id = cc.client_entity_id
            WHERE cc.tenant_id = ? AND cc.id = ?
            LIMIT 1
            """,
            (tenant_id, credential_id),
        ).fetchone()
    return _row_to_safe_dict(row)


def update_credential_status(
    tenant_id,
    credential_id,
    status,
    last_error=None,
    user_id=None,
    ip_address=None,
):
    status = _clean_text(status)
    if status not in ALLOWED_STATUSES:
        raise ValueError("Invalid credential status.")

    with db.get_db() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM client_credentials
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, credential_id),
        ).fetchone()
        if not existing:
            return None

        conn.execute(
            """
            UPDATE client_credentials
            SET status = ?,
                last_error = ?,
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (status, _clean_text(last_error), _now_iso(), tenant_id, credential_id),
        )

        updated = conn.execute(
            """
            SELECT cc.*, ce.name AS client_name
            FROM client_credentials cc
            JOIN client_entities ce ON ce.id = cc.client_entity_id
            WHERE cc.tenant_id = ? AND cc.id = ?
            LIMIT 1
            """,
            (tenant_id, credential_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="credential_status_changed",
            entity_type="client_credential",
            entity_id=credential_id,
            old_value={
                "status": existing["status"],
                "last_error": existing["last_error"],
            },
            new_value={
                "status": status,
                "last_error": _clean_text(last_error),
            },
            metadata={"portal_type": existing["portal_type"]},
            ip_address=ip_address,
        )

        return _row_to_safe_dict(updated)


def mark_credential_verified(
    tenant_id,
    credential_id,
    login_status="success",
    user_id=None,
    ip_address=None,
):
    login_status = _clean_text(login_status) or "success"
    next_status = "available" if login_status.lower() == "success" else "error"

    with db.get_db() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM client_credentials
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, credential_id),
        ).fetchone()
        if not existing:
            return None

        now_iso = _now_iso()
        conn.execute(
            """
            UPDATE client_credentials
            SET last_verified_at = ?,
                last_login_status = ?,
                status = ?,
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (now_iso, login_status, next_status, now_iso, tenant_id, credential_id),
        )

        updated = conn.execute(
            """
            SELECT cc.*, ce.name AS client_name
            FROM client_credentials cc
            JOIN client_entities ce ON ce.id = cc.client_entity_id
            WHERE cc.tenant_id = ? AND cc.id = ?
            LIMIT 1
            """,
            (tenant_id, credential_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="credential_verified",
            entity_type="client_credential",
            entity_id=credential_id,
            old_value={
                "status": existing["status"],
                "last_verified_at": existing["last_verified_at"],
                "last_login_status": existing["last_login_status"],
            },
            new_value={
                "status": next_status,
                "last_verified_at": now_iso,
                "last_login_status": login_status,
            },
            metadata={"portal_type": existing["portal_type"]},
            ip_address=ip_address,
        )

        return _row_to_safe_dict(updated)


def get_credential_summary(tenant_id):
    with db.get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(1) FROM client_credentials WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()[0]

        status_counts = {
            "available": 0,
            "missing": 0,
            "expired": 0,
            "locked": 0,
            "error": 0,
        }

        rows = conn.execute(
            """
            SELECT status, COUNT(1) AS count
            FROM client_credentials
            WHERE tenant_id = ?
            GROUP BY status
            """,
            (tenant_id,),
        ).fetchall()

        for row in rows:
            status_key = _clean_text(row["status"]) or ""
            if status_key in status_counts:
                status_counts[status_key] = int(row["count"])

        otp_required_count = conn.execute(
            """
            SELECT COUNT(1)
            FROM client_credentials
            WHERE tenant_id = ? AND otp_required = 1
            """,
            (tenant_id,),
        ).fetchone()[0]

    return {
        "total_credentials": int(total),
        "available_count": status_counts["available"],
        "missing_count": status_counts["missing"],
        "expired_count": status_counts["expired"],
        "locked_count": status_counts["locked"],
        "error_count": status_counts["error"],
        "otp_required_count": int(otp_required_count),
    }


def get_portal_readiness_for_client(tenant_id, client_entity_id):
    with db.get_db() as conn:
        client = conn.execute(
            """
            SELECT id
            FROM client_entities
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, client_entity_id),
        ).fetchone()
        if not client:
            raise ValueError("Client not found for tenant.")

        rows = conn.execute(
            """
            SELECT
                portal_type,
                display_name,
                username,
                secret_value_encrypted,
                otp_required,
                status,
                updated_at,
                id
            FROM client_credentials
            WHERE tenant_id = ? AND client_entity_id = ?
            ORDER BY datetime(updated_at) DESC, id DESC
            """,
            (tenant_id, client_entity_id),
        ).fetchall()

    latest_by_portal = {}
    for row in rows:
        portal_type = row["portal_type"]
        if portal_type not in latest_by_portal:
            latest_by_portal[portal_type] = row

    readiness = []
    for portal_type, label in PORTAL_TYPES.items():
        row = latest_by_portal.get(portal_type)
        readiness.append(
            {
                "portal_type": portal_type,
                "display_name": row["display_name"] if row else label,
                "status": row["status"] if row else "missing",
                "username_available": bool(_clean_text(row["username"])) if row else False,
                "secret_available": _secret_is_available(row["secret_value_encrypted"]) if row else False,
                "otp_required": bool(row["otp_required"]) if row else False,
            }
        )

    return readiness

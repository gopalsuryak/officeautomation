import json
from datetime import datetime, timezone
from typing import Any

import db

PROVIDERS = {
    "tally": "Tally / TallyPrime",
    "zoho_books": "Zoho Books",
    "manual_upload": "Manual Upload",
}

AUTH_TYPES = {
    "tally": "local_bridge",
    "zoho_books": "oauth2",
    "manual_upload": "file_upload",
}

STATUSES = {
    "draft",
    "connected",
    "disconnected",
    "error",
    "disabled",
}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row):
    return dict(row) if row else None


def create_connection(
    tenant_id,
    client_entity_id,
    provider,
    connection_name,
    user_id=None,
    ip_address=None,
):
    provider = _clean_text(provider)
    connection_name = _clean_text(connection_name)

    if provider not in PROVIDERS:
        raise ValueError("Invalid provider.")
    if not connection_name:
        raise ValueError("Connection name is required.")

    auth_type = AUTH_TYPES[provider]
    status = "connected" if provider == "manual_upload" else "draft"

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

        metadata_json = json.dumps({"foundation_only": True}, ensure_ascii=False)
        now_iso = _now_iso()

        cur = conn.execute(
            """
            INSERT INTO accounting_connections (
                tenant_id,
                client_entity_id,
                provider,
                connection_name,
                status,
                auth_type,
                metadata_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                client_entity_id,
                provider,
                connection_name,
                status,
                auth_type,
                metadata_json,
                now_iso,
                now_iso,
            ),
        )

        connection_id = cur.lastrowid
        created = conn.execute(
            """
            SELECT ac.*, ce.name AS client_name
            FROM accounting_connections ac
            JOIN client_entities ce ON ce.id = ac.client_entity_id
            WHERE ac.tenant_id = ? AND ac.id = ?
            LIMIT 1
            """,
            (tenant_id, connection_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_connection_created",
            entity_type="accounting_connection",
            entity_id=connection_id,
            old_value=None,
            new_value={
                "client_entity_id": client_entity_id,
                "provider": provider,
                "connection_name": connection_name,
                "status": status,
                "auth_type": auth_type,
            },
            metadata={"client_name": client["name"]},
            ip_address=ip_address,
        )

    return _row_to_dict(created)


def list_connections(tenant_id, filters=None):
    filters = filters or {}

    provider = _clean_text(filters.get("provider"))
    status = _clean_text(filters.get("status"))
    client_entity_id = _clean_text(filters.get("client_entity_id"))
    search = _clean_text(filters.get("search"))

    where = ["ac.tenant_id = ?"]
    params: list[Any] = [tenant_id]

    if provider and provider in PROVIDERS:
        where.append("ac.provider = ?")
        params.append(provider)

    if status and status in STATUSES:
        where.append("ac.status = ?")
        params.append(status)

    if client_entity_id:
        where.append("CAST(ac.client_entity_id AS TEXT) = ?")
        params.append(client_entity_id)

    if search:
        like = f"%{search}%"
        where.append(
            """
            (
                ac.connection_name LIKE ? OR
                ac.provider LIKE ? OR
                ce.name LIKE ? OR
                ac.last_error LIKE ?
            )
            """
        )
        params.extend([like, like, like, like])

    query = f"""
        SELECT ac.*, ce.name AS client_name
        FROM accounting_connections ac
        JOIN client_entities ce ON ce.id = ac.client_entity_id
        WHERE {' AND '.join(where)}
        ORDER BY datetime(ac.updated_at) DESC, ac.id DESC
    """

    with db.get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(row) for row in rows]


def get_connection(tenant_id, connection_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT ac.*, ce.name AS client_name
            FROM accounting_connections ac
            JOIN client_entities ce ON ce.id = ac.client_entity_id
            WHERE ac.tenant_id = ? AND ac.id = ?
            LIMIT 1
            """,
            (tenant_id, connection_id),
        ).fetchone()
    return _row_to_dict(row)


def update_connection_status(
    tenant_id,
    connection_id,
    status,
    error=None,
    user_id=None,
    ip_address=None,
):
    status = _clean_text(status)
    if status not in STATUSES:
        raise ValueError("Invalid connection status.")

    with db.get_db() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM accounting_connections
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, connection_id),
        ).fetchone()
        if not existing:
            return None

        error_text = _clean_text(error)
        now_iso = _now_iso()

        conn.execute(
            """
            UPDATE accounting_connections
            SET status = ?,
                last_error = ?,
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (status, error_text, now_iso, tenant_id, connection_id),
        )

        updated = conn.execute(
            """
            SELECT ac.*, ce.name AS client_name
            FROM accounting_connections ac
            JOIN client_entities ce ON ce.id = ac.client_entity_id
            WHERE ac.tenant_id = ? AND ac.id = ?
            LIMIT 1
            """,
            (tenant_id, connection_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_connection_status_changed",
            entity_type="accounting_connection",
            entity_id=connection_id,
            old_value={
                "status": existing["status"],
                "last_error": existing["last_error"],
            },
            new_value={
                "status": status,
                "last_error": error_text,
            },
            metadata={"provider": existing["provider"]},
            ip_address=ip_address,
        )

    return _row_to_dict(updated)


def list_sync_runs(tenant_id, connection_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM accounting_sync_runs
            WHERE tenant_id = ? AND connection_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 100
            """,
            (tenant_id, connection_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_connector_summary(tenant_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT provider, status, COUNT(1) AS count
            FROM accounting_connections
            WHERE tenant_id = ?
            GROUP BY provider, status
            """,
            (tenant_id,),
        ).fetchall()

    total_connections = sum(int(row["count"]) for row in rows)
    tally_connections = sum(int(row["count"]) for row in rows if row["provider"] == "tally")
    zoho_books_connections = sum(int(row["count"]) for row in rows if row["provider"] == "zoho_books")
    manual_upload_connections = sum(int(row["count"]) for row in rows if row["provider"] == "manual_upload")
    connected_count = sum(int(row["count"]) for row in rows if row["status"] == "connected")
    error_count = sum(int(row["count"]) for row in rows if row["status"] == "error")
    draft_count = sum(int(row["count"]) for row in rows if row["status"] == "draft")
    disabled_count = sum(int(row["count"]) for row in rows if row["status"] == "disabled")

    return {
        "total_connections": total_connections,
        "tally_connections": tally_connections,
        "zoho_books_connections": zoho_books_connections,
        "manual_upload_connections": manual_upload_connections,
        "connected_count": connected_count,
        "error_count": error_count,
        "draft_count": draft_count,
        "disabled_count": disabled_count,
    }


def get_provider_guidance(provider):
    provider = _clean_text(provider)

    if provider == "tally":
        return (
            "Tally / TallyPrime usually runs on local desktop or LAN environments. "
            "CA Assist will support a local bridge in a later phase for secure data transfer."
        )
    if provider == "zoho_books":
        return (
            "Zoho Books OAuth connection will be added in a future phase. "
            "This foundation currently tracks connection readiness and status only."
        )
    if provider == "manual_upload":
        return (
            "Excel/CSV/XML manual upload flows will be added in a future phase and are "
            "the recommended first connector path for many firms."
        )

    return "Provider guidance is not available."

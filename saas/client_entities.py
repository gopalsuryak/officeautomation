import re
import sqlite3
from typing import Any

import db
import usage

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

_ALLOWED_FIELDS = [
    "name",
    "legal_name",
    "entity_type",
    "pan",
    "gstin",
    "cin",
    "email",
    "phone",
    "address",
    "state_code",
    "assigned_user_id",
]


def _clean_text(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return value


def _normalise_data(data: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for field in _ALLOWED_FIELDS:
        if field in data:
            cleaned[field] = _clean_text(data.get(field))

    # Required in create flow. Route can still catch this cleanly.
    if "name" in cleaned and not cleaned["name"]:
        raise ValueError("Client name is required.")

    if cleaned.get("pan"):
        cleaned["pan"] = str(cleaned["pan"]).upper()
    if cleaned.get("gstin"):
        cleaned["gstin"] = str(cleaned["gstin"]).upper()
    if cleaned.get("state_code"):
        cleaned["state_code"] = str(cleaned["state_code"]).upper()

    return cleaned


def validate_pan(pan: str | None) -> bool:
    if not pan:
        return True
    return bool(PAN_RE.match(str(pan).strip().upper()))


def validate_gstin(gstin: str | None) -> bool:
    if not gstin:
        return True
    return bool(GSTIN_RE.match(str(gstin).strip().upper()))


def get_client_entity(tenant_id: int, client_entity_id: int):
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT * FROM client_entities
            WHERE id = ? AND tenant_id = ?
            LIMIT 1
            """,
            (client_entity_id, tenant_id),
        ).fetchone()


def create_client_entity(
    tenant_id: int,
    data: dict[str, Any],
    user_id: int | None = None,
    ip_address: str | None = None,
):
    payload = _normalise_data(data)

    if not payload.get("name"):
        raise ValueError("Client name is required.")
    if not validate_pan(payload.get("pan")):
        raise ValueError("Invalid PAN format.")
    if not validate_gstin(payload.get("gstin")):
        raise ValueError("Invalid GSTIN format.")

    usage.check_client_limit(tenant_id)

    columns = ["tenant_id"]
    values = [tenant_id]
    placeholders = ["?"]

    for field in _ALLOWED_FIELDS:
        if field in payload:
            columns.append(field)
            values.append(payload.get(field))
            placeholders.append("?")

    with db.get_db() as conn:
        cur = conn.execute(
            f"INSERT INTO client_entities ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",  # noqa: S608
            tuple(values),
        )
        client_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM client_entities WHERE id = ? AND tenant_id = ?",
            (client_id, tenant_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="client_created",
            entity_type="client_entity",
            entity_id=client_id,
            old_value=None,
            new_value=dict(row) if row else payload,
            metadata=None,
            ip_address=ip_address,
        )
        return row


def update_client_entity(
    tenant_id: int,
    client_entity_id: int,
    data: dict[str, Any],
    user_id: int | None = None,
    ip_address: str | None = None,
):
    existing = get_client_entity(tenant_id, client_entity_id)
    if not existing:
        return None

    payload = _normalise_data(data)

    if "name" in payload and not payload.get("name"):
        raise ValueError("Client name is required.")
    if not validate_pan(payload.get("pan")):
        raise ValueError("Invalid PAN format.")
    if not validate_gstin(payload.get("gstin")):
        raise ValueError("Invalid GSTIN format.")

    updates = []
    values = []
    for field in _ALLOWED_FIELDS:
        if field in payload:
            updates.append(f"{field} = ?")
            values.append(payload[field])

    if not updates:
        return existing

    with db.get_db() as conn:
        conn.execute(
            f"""
            UPDATE client_entities
            SET {', '.join(updates)}
            WHERE id = ? AND tenant_id = ?
            """,  # noqa: S608
            tuple(values + [client_entity_id, tenant_id]),
        )
        db.touch_updated_at(conn, "client_entities", client_entity_id)

        updated = conn.execute(
            "SELECT * FROM client_entities WHERE id = ? AND tenant_id = ?",
            (client_entity_id, tenant_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="client_updated",
            entity_type="client_entity",
            entity_id=client_entity_id,
            old_value=dict(existing),
            new_value=dict(updated) if updated else payload,
            metadata=None,
            ip_address=ip_address,
        )
        return updated


def list_client_entities(
    tenant_id: int,
    search: str | None = None,
    status: str | None = None,
    entity_type: str | None = None,
):
    filters = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]

    # Default list view should show active clients.
    status_filter = (status or "active").strip().lower()
    if status_filter != "all":
        filters.append("status = ?")
        params.append(status_filter)

    if entity_type:
        filters.append("entity_type = ?")
        params.append(entity_type.strip())

    if search:
        like = f"%{search.strip()}%"
        filters.append(
            """
            (
                name LIKE ? OR legal_name LIKE ? OR pan LIKE ? OR gstin LIKE ? OR phone LIKE ? OR email LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like, like])

    query = f"""
        SELECT * FROM client_entities
        WHERE {' AND '.join(filters)}
        ORDER BY name COLLATE NOCASE ASC, id DESC
    """

    with db.get_db() as conn:
        return conn.execute(query, tuple(params)).fetchall()


def deactivate_client_entity(
    tenant_id: int,
    client_entity_id: int,
    user_id: int | None = None,
    ip_address: str | None = None,
):
    existing = get_client_entity(tenant_id, client_entity_id)
    if not existing:
        return None

    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE client_entities
            SET status = 'inactive'
            WHERE id = ? AND tenant_id = ?
            """,
            (client_entity_id, tenant_id),
        )
        db.touch_updated_at(conn, "client_entities", client_entity_id)
        updated = conn.execute(
            "SELECT * FROM client_entities WHERE id = ? AND tenant_id = ?",
            (client_entity_id, tenant_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="client_deactivated",
            entity_type="client_entity",
            entity_id=client_entity_id,
            old_value=dict(existing),
            new_value=dict(updated) if updated else {"status": "inactive"},
            metadata=None,
            ip_address=ip_address,
        )

    return updated


def get_client_summary(tenant_id: int, client_entity_id: int) -> dict[str, int]:
    summary = {
        "total_tasks": 0,
        "open_tasks": 0,
        "pending_documents": 0,
        "under_review": 0,
        "approved": 0,
        "filed": 0,
    }

    with db.get_db() as conn:
        try:
            task_counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_tasks,
                    SUM(CASE WHEN status NOT IN ('filed', 'closed', 'cancelled') THEN 1 ELSE 0 END) AS open_tasks,
                    SUM(CASE WHEN status = 'under_review' THEN 1 ELSE 0 END) AS under_review,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                    SUM(CASE WHEN status = 'filed' THEN 1 ELSE 0 END) AS filed
                FROM compliance_tasks
                WHERE tenant_id = ? AND client_entity_id = ?
                """,
                (tenant_id, client_entity_id),
            ).fetchone()
            if task_counts:
                summary["total_tasks"] = int(task_counts["total_tasks"] or 0)
                summary["open_tasks"] = int(task_counts["open_tasks"] or 0)
                summary["under_review"] = int(task_counts["under_review"] or 0)
                summary["approved"] = int(task_counts["approved"] or 0)
                summary["filed"] = int(task_counts["filed"] or 0)
        except sqlite3.OperationalError:
            # Older DBs may not have compliance_tasks yet.
            pass

        try:
            doc_counts = conn.execute(
                """
                SELECT COUNT(*) AS pending_documents
                FROM document_requests
                WHERE tenant_id = ? AND client_entity_id = ? AND status = 'requested'
                """,
                (tenant_id, client_entity_id),
            ).fetchone()
            if doc_counts:
                summary["pending_documents"] = int(doc_counts["pending_documents"] or 0)
        except sqlite3.OperationalError:
            # Older DBs may not have document_requests yet.
            pass

    return summary

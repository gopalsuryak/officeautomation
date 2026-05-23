from collections import Counter
from typing import Any

import db
import credential_vault

PORTAL_COLUMNS = {
    "gst": "GST",
    "income_tax": "Income Tax",
    "mca": "MCA",
    "traces": "TRACES",
    "pf": "PF",
    "esi": "ESI",
    "professional_tax": "Professional Tax",
    "bank": "Bank",
    "zoho_books": "Zoho Books",
    "tally_bridge": "Tally Bridge",
}

CRITICAL_PORTALS = {"gst", "income_tax", "mca", "traces"}
ATTENTION_STATUSES = {"expired", "locked", "error", "disabled"}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _build_portal_state(portal_type: str, credential: dict | None) -> dict:
    display_name = PORTAL_COLUMNS[portal_type]
    if not credential:
        return {
            "display_name": display_name,
            "status": "missing",
            "credential_id": None,
            "username_available": False,
            "secret_available": False,
            "otp_required": False,
            "portal_url": credential_vault.get_portal_url(portal_type),
        }

    status = _clean_text(credential.get("status")).lower() or "missing"

    if status in ATTENTION_STATUSES:
        resolved_status = status
    elif status == "missing":
        resolved_status = "missing"
    else:
        readiness = credential_vault.get_credential_readiness(credential)
        resolved_status = readiness["readiness_status"]

    return {
        "display_name": display_name,
        "status": resolved_status,
        "credential_id": credential.get("id"),
        "username_available": bool(credential.get("username")),
        "secret_available": credential_vault._secret_is_available(credential.get("secret_value_encrypted")),
        "otp_required": bool(credential.get("otp_required")),
        "portal_url": credential_vault.get_portal_url(portal_type),
    }


def _compute_overall_status(portals: dict) -> str:
    statuses = [p["status"] for p in portals.values()]
    ready_count = sum(1 for s in statuses if s == "ready")

    if any(s in {"expired", "locked", "error"} for s in statuses):
        return "attention"

    critical_statuses = [portals[key]["status"] for key in CRITICAL_PORTALS]
    if all(s == "ready" for s in critical_statuses):
        return "ready"

    if ready_count == 0:
        return "not_ready"

    if any(s in {"partial", "missing", "not_ready", "disabled"} for s in statuses):
        return "partial"

    return "partial"


def get_client_portal_readiness_matrix(tenant_id, filters=None):
    filters = filters or {}
    search = _clean_text(filters.get("search")).lower()
    portal_filter = _clean_text(filters.get("portal_type"))
    status_filter = _clean_text(filters.get("status")).lower()

    with db.get_db() as conn:
        clients = conn.execute(
            """
            SELECT id, name, pan, gstin
            FROM client_entities
            WHERE tenant_id = ? AND status = 'active'
            ORDER BY name COLLATE NOCASE ASC
            """,
            (tenant_id,),
        ).fetchall()

        credentials = conn.execute(
            """
            SELECT
                id,
                tenant_id,
                client_entity_id,
                portal_type,
                display_name,
                username,
                secret_value_encrypted,
                otp_required,
                status,
                updated_at
            FROM client_credentials
            WHERE tenant_id = ?
            ORDER BY datetime(updated_at) DESC, id DESC
            """,
            (tenant_id,),
        ).fetchall()

    latest_by_client_portal: dict[tuple[int, str], dict] = {}
    for row in credentials:
        data = dict(row)
        key = (data["client_entity_id"], data["portal_type"])
        if key not in latest_by_client_portal:
            latest_by_client_portal[key] = data

    rows = []
    for client in clients:
        client_data = dict(client)

        if search:
            haystack = " ".join(
                [
                    _clean_text(client_data.get("name")),
                    _clean_text(client_data.get("pan")),
                    _clean_text(client_data.get("gstin")),
                ]
            ).lower()
            if search not in haystack:
                continue

        portals: dict[str, dict] = {}
        for portal_type in PORTAL_COLUMNS:
            credential = latest_by_client_portal.get((client_data["id"], portal_type))
            portals[portal_type] = _build_portal_state(portal_type, credential)

        statuses = [info["status"] for info in portals.values()]
        ready_count = sum(1 for status in statuses if status == "ready")
        partial_count = sum(1 for status in statuses if status == "partial")
        not_ready_count = sum(1 for status in statuses if status in {"not_ready", "missing"})
        attention_count = sum(1 for status in statuses if status in ATTENTION_STATUSES)
        overall_status = _compute_overall_status(portals)

        row = {
            "client_entity_id": client_data["id"],
            "client_name": client_data["name"],
            "pan": client_data.get("pan"),
            "gstin": client_data.get("gstin"),
            "overall_status": overall_status,
            "ready_count": ready_count,
            "partial_count": partial_count,
            "not_ready_count": not_ready_count,
            "attention_count": attention_count,
            "portals": portals,
        }

        if portal_filter and portal_filter in PORTAL_COLUMNS:
            portal_status = portals[portal_filter]["status"]
            if status_filter and portal_status != status_filter:
                continue
        elif status_filter:
            if row["overall_status"] != status_filter and status_filter not in statuses:
                continue

        rows.append(row)

    return rows


def get_portal_readiness_summary(tenant_id):
    rows = get_client_portal_readiness_matrix(tenant_id)

    overall_counts = Counter(row["overall_status"] for row in rows)

    with db.get_db() as conn:
        credential_rows = conn.execute(
            """
            SELECT status, otp_required
            FROM client_credentials
            WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchall()

    status_counter = Counter(_clean_text(row["status"]).lower() for row in credential_rows)
    otp_required_count = sum(1 for row in credential_rows if bool(row["otp_required"]))

    return {
        "total_clients": len(rows),
        "ready_clients": overall_counts.get("ready", 0),
        "partial_clients": overall_counts.get("partial", 0),
        "attention_clients": overall_counts.get("attention", 0),
        "not_ready_clients": overall_counts.get("not_ready", 0),
        "total_credentials": len(credential_rows),
        "expired_credentials": status_counter.get("expired", 0),
        "locked_credentials": status_counter.get("locked", 0),
        "error_credentials": status_counter.get("error", 0),
        "otp_required_count": otp_required_count,
    }


def get_clients_needing_attention(tenant_id, limit=10):
    rows = get_client_portal_readiness_matrix(tenant_id)
    items = []

    for row in rows:
        for portal_type in CRITICAL_PORTALS:
            portal = row["portals"][portal_type]
            status = portal["status"]
            if status in ATTENTION_STATUSES or status == "missing":
                issue = "Critical portal needs attention"
                if status == "missing":
                    issue = "Critical portal credential missing"

                items.append(
                    {
                        "client_entity_id": row["client_entity_id"],
                        "client_name": row["client_name"],
                        "issue": issue,
                        "portal_type": portal_type,
                        "portal_name": portal["display_name"],
                        "status": status,
                        "credential_id": portal["credential_id"],
                    }
                )

    priority_order = {
        "error": 0,
        "locked": 1,
        "expired": 2,
        "disabled": 3,
        "missing": 4,
    }

    items.sort(key=lambda x: (priority_order.get(x["status"], 99), x["client_name"].lower()))
    return items[:limit]

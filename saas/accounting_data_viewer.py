import sqlite3

import db


def _clean_text(value):
    return str(value or "").strip()


def _to_float_or_none(value):
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _build_where_clause(tenant_id, filters=None):
    filters = filters or {}
    where_parts = ["l.tenant_id = ?"]
    params = [tenant_id]

    client_entity_id = _clean_text(filters.get("client_entity_id"))
    if client_entity_id:
        where_parts.append("l.client_entity_id = ?")
        params.append(client_entity_id)

    connection_id = _clean_text(filters.get("connection_id"))
    if connection_id:
        where_parts.append("l.connection_id = ?")
        params.append(connection_id)

    provider = _clean_text(filters.get("provider"))
    if provider:
        where_parts.append("COALESCE(l.provider, '') = ?")
        params.append(provider)

    group_name = _clean_text(filters.get("group_name"))
    if group_name:
        where_parts.append("COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped') = ?")
        params.append(group_name)

    search = _clean_text(filters.get("search"))
    if search:
        wildcard = f"%{search}%"
        where_parts.append(
            "(" 
            "COALESCE(l.ledger_name, '') LIKE ? "
            "OR COALESCE(l.group_name, '') LIKE ? "
            "OR COALESCE(c.name, '') LIKE ?"
            ")"
        )
        params.extend([wildcard, wildcard, wildcard])

    min_closing_balance = _to_float_or_none(filters.get("min_closing_balance"))
    if min_closing_balance is not None:
        where_parts.append("COALESCE(l.closing_balance, 0) >= ?")
        params.append(min_closing_balance)

    max_closing_balance = _to_float_or_none(filters.get("max_closing_balance"))
    if max_closing_balance is not None:
        where_parts.append("COALESCE(l.closing_balance, 0) <= ?")
        params.append(max_closing_balance)

    return " AND ".join(where_parts), params


def list_ledgers(tenant_id, filters=None):
    where_sql, params = _build_where_clause(tenant_id, filters)

    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                l.id,
                c.name AS client_name,
                ac.connection_name,
                COALESCE(l.provider, ac.provider, '') AS provider,
                l.ledger_name,
                COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped') AS group_name,
                COALESCE(l.opening_balance, 0) AS opening_balance,
                COALESCE(l.closing_balance, 0) AS closing_balance,
                l.created_at
            FROM accounting_ledgers l
            JOIN client_entities c
              ON c.id = l.client_entity_id
             AND c.tenant_id = l.tenant_id
            LEFT JOIN accounting_connections ac
              ON ac.id = l.connection_id
             AND ac.tenant_id = l.tenant_id
            WHERE {where_sql}
            ORDER BY
                LOWER(COALESCE(c.name, '')) ASC,
                LOWER(COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped')) ASC,
                LOWER(COALESCE(l.ledger_name, '')) ASC,
                l.id ASC
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def get_ledger(tenant_id, ledger_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT
                l.*,
                c.name AS client_name,
                ac.connection_name,
                COALESCE(l.provider, ac.provider, '') AS provider,
                COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped') AS group_name_display
            FROM accounting_ledgers l
            JOIN client_entities c
              ON c.id = l.client_entity_id
             AND c.tenant_id = l.tenant_id
            LEFT JOIN accounting_connections ac
              ON ac.id = l.connection_id
             AND ac.tenant_id = l.tenant_id
            WHERE l.tenant_id = ? AND l.id = ?
            LIMIT 1
            """,
            (tenant_id, ledger_id),
        ).fetchone()

    return dict(row) if row else None


def get_ledger_summary(tenant_id, filters=None):
    where_sql, params = _build_where_clause(tenant_id, filters)

    with db.get_db() as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_ledgers,
                COALESCE(SUM(COALESCE(l.opening_balance, 0)), 0) AS total_opening_balance,
                COALESCE(SUM(COALESCE(l.closing_balance, 0)), 0) AS total_closing_balance,
                COALESCE(SUM(CASE WHEN COALESCE(l.closing_balance, 0) > 0 THEN COALESCE(l.closing_balance, 0) ELSE 0 END), 0) AS debit_total,
                COALESCE(SUM(CASE WHEN COALESCE(l.closing_balance, 0) < 0 THEN COALESCE(l.closing_balance, 0) ELSE 0 END), 0) AS credit_total,
                COUNT(DISTINCT COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped')) AS group_count,
                COUNT(DISTINCT l.client_entity_id) AS client_count
            FROM accounting_ledgers l
            JOIN client_entities c
              ON c.id = l.client_entity_id
             AND c.tenant_id = l.tenant_id
            LEFT JOIN accounting_connections ac
              ON ac.id = l.connection_id
             AND ac.tenant_id = l.tenant_id
            WHERE {where_sql}
            """,
            params,
        ).fetchone()

    return {
        "total_ledgers": int(row["total_ledgers"] or 0),
        "total_opening_balance": float(row["total_opening_balance"] or 0),
        "total_closing_balance": float(row["total_closing_balance"] or 0),
        "debit_total": float(row["debit_total"] or 0),
        "credit_total": float(row["credit_total"] or 0),
        "group_count": int(row["group_count"] or 0),
        "client_count": int(row["client_count"] or 0),
    }


def get_group_summary(tenant_id, filters=None):
    where_sql, params = _build_where_clause(tenant_id, filters)

    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped') AS group_name,
                COUNT(*) AS ledger_count,
                COALESCE(SUM(COALESCE(l.closing_balance, 0)), 0) AS total_closing_balance
            FROM accounting_ledgers l
            JOIN client_entities c
              ON c.id = l.client_entity_id
             AND c.tenant_id = l.tenant_id
            LEFT JOIN accounting_connections ac
              ON ac.id = l.connection_id
             AND ac.tenant_id = l.tenant_id
            WHERE {where_sql}
            GROUP BY COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped')
            ORDER BY LOWER(COALESCE(NULLIF(TRIM(l.group_name), ''), 'Ungrouped')) ASC
            """,
            params,
        ).fetchall()

    return [
        {
            "group_name": row["group_name"],
            "ledger_count": int(row["ledger_count"] or 0),
            "total_closing_balance": float(row["total_closing_balance"] or 0),
        }
        for row in rows
    ]


def get_client_accounting_summary(tenant_id, filters=None):
    where_sql, params = _build_where_clause(tenant_id, filters)

    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                l.client_entity_id,
                c.name AS client_name,
                COUNT(*) AS ledger_count,
                COALESCE(SUM(COALESCE(l.closing_balance, 0)), 0) AS total_closing_balance,
                MAX(l.created_at) AS latest_import_at
            FROM accounting_ledgers l
            JOIN client_entities c
              ON c.id = l.client_entity_id
             AND c.tenant_id = l.tenant_id
            LEFT JOIN accounting_connections ac
              ON ac.id = l.connection_id
             AND ac.tenant_id = l.tenant_id
            WHERE {where_sql}
            GROUP BY l.client_entity_id, c.name
            ORDER BY LOWER(COALESCE(c.name, '')) ASC
            """,
            params,
        ).fetchall()

    return [
        {
            "client_entity_id": row["client_entity_id"],
            "client_name": row["client_name"],
            "ledger_count": int(row["ledger_count"] or 0),
            "total_closing_balance": float(row["total_closing_balance"] or 0),
            "latest_import_at": row["latest_import_at"],
        }
        for row in rows
    ]

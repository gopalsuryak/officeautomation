import db


def _rows_to_dicts(rows):
    return [dict(row) for row in (rows or [])]


def _safe_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value):
    text = (value or "").strip()
    return text or None


def build_filter_clause(filters, table_aliases=None):
    """Build a safe parameterized SQL filter suffix for supported fields."""
    filters = filters or {}
    table_aliases = table_aliases or {}

    clauses = []
    params = []

    client_col = table_aliases.get("client_entity_id")
    client_id = _safe_int(filters.get("client_entity_id"))
    if client_col and client_id:
        clauses.append(f"{client_col} = ?")
        params.append(client_id)

    task_col = table_aliases.get("task_id")
    task_id = _safe_int(filters.get("task_id"))
    if task_col and task_id:
        clauses.append(f"{task_col} = ?")
        params.append(task_id)

    provider_col = table_aliases.get("provider_id")
    provider_id = _safe_int(filters.get("provider_id"))
    if provider_col and provider_id:
        clauses.append(f"{provider_col} = ?")
        params.append(provider_id)

    queue_status_col = table_aliases.get("queue_status")
    queue_status = _safe_text(filters.get("queue_status"))
    if queue_status_col and queue_status:
        clauses.append(f"{queue_status_col} = ?")
        params.append(queue_status.lower())

    draft_status_col = table_aliases.get("draft_status")
    draft_status = _safe_text(filters.get("draft_status"))
    if draft_status_col and draft_status:
        clauses.append(f"{draft_status_col} = ?")
        params.append(draft_status.lower())

    date_col = table_aliases.get("date_column")
    date_from = _safe_text(filters.get("date_from"))
    date_to = _safe_text(filters.get("date_to"))
    if date_col and date_from:
        clauses.append(f"date({date_col}) >= date(?)")
        params.append(date_from)
    if date_col and date_to:
        clauses.append(f"date({date_col}) <= date(?)")
        params.append(date_to)

    search = _safe_text(filters.get("search"))
    search_columns = table_aliases.get("search_columns") or []
    if search and search_columns:
        like = f"%{search.lower()}%"
        search_parts = []
        for col in search_columns:
            search_parts.append(f"LOWER(COALESCE({col}, '')) LIKE ?")
            params.append(like)
        clauses.append("(" + " OR ".join(search_parts) + ")")

    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def get_email_operations_summary(tenant_id, filters=None):
    draft_aliases = {
        "client_entity_id": "d.client_entity_id",
        "task_id": "d.task_id",
        "draft_status": "d.status",
        "date_column": "d.created_at",
        "search_columns": ["c.name", "t.title", "d.subject", "d.body"],
    }
    reviewed_aliases = dict(draft_aliases)
    reviewed_aliases["date_column"] = "d.reviewed_at"
    queue_aliases = {
        "client_entity_id": "q.client_entity_id",
        "task_id": "q.task_id",
        "provider_id": "q.provider_setting_id",
        "queue_status": "q.status",
        "date_column": "q.queued_at",
        "search_columns": ["c.name", "t.title", "q.subject", "q.to_email", "eps.display_name", "eps.from_email"],
    }
    provider_aliases = {
        "provider_id": "eps.id",
        "date_column": "eps.updated_at",
        "search_columns": ["eps.display_name", "eps.from_email"],
    }

    draft_filter_sql, draft_filter_params = build_filter_clause(filters, draft_aliases)
    reviewed_filter_sql, reviewed_filter_params = build_filter_clause(filters, reviewed_aliases)
    queue_filter_sql, queue_filter_params = build_filter_clause(filters, queue_aliases)
    provider_filter_sql, provider_filter_params = build_filter_clause(filters, provider_aliases)

    with db.get_db() as conn:
        drafts_awaiting_review = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_communication_drafts d
            JOIN client_entities c ON c.id = d.client_entity_id AND c.tenant_id = d.tenant_id
            JOIN compliance_tasks t ON t.id = d.task_id AND t.tenant_id = d.tenant_id
            WHERE d.tenant_id = ?
              AND d.draft_type = 'email'
              AND d.status = 'draft'
            """
            + draft_filter_sql,
            (tenant_id, *draft_filter_params),
        ).fetchone()["c"]

        reviewed_not_queued = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_communication_drafts d
            JOIN client_entities c ON c.id = d.client_entity_id AND c.tenant_id = d.tenant_id
            JOIN compliance_tasks t ON t.id = d.task_id AND t.tenant_id = d.tenant_id
            LEFT JOIN email_send_queue q
              ON q.tenant_id = d.tenant_id
             AND q.draft_id = d.id
            WHERE d.tenant_id = ?
              AND d.draft_type = 'email'
              AND d.status = 'reviewed'
              AND q.id IS NULL
            """
            + reviewed_filter_sql,
            (tenant_id, *reviewed_filter_params),
        ).fetchone()["c"]

        queued_without_provider = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_send_queue q
            JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE q.tenant_id = ?
              AND q.status = 'queued'
              AND q.provider_setting_id IS NULL
            """
            + queue_filter_sql,
            (tenant_id, *queue_filter_params),
        ).fetchone()["c"]

        queued_with_provider = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_send_queue q
            JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE q.tenant_id = ?
              AND q.status = 'queued'
              AND q.provider_setting_id IS NOT NULL
            """
            + queue_filter_sql,
            (tenant_id, *queue_filter_params),
        ).fetchone()["c"]

        ready_to_send = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_send_queue q
            JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE q.tenant_id = ?
              AND q.status = 'ready_to_send'
            """
            + queue_filter_sql,
            (tenant_id, *queue_filter_params),
        ).fetchone()["c"]

        cancelled_failed = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_send_queue q
            JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE q.tenant_id = ?
              AND q.status IN ('cancelled', 'failed')
            """
            + queue_filter_sql,
            (tenant_id, *queue_filter_params),
        ).fetchone()["c"]

        active_providers = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_provider_settings eps
            WHERE eps.tenant_id = ?
              AND eps.status = 'active'
            """
            + provider_filter_sql,
            (tenant_id, *provider_filter_params),
        ).fetchone()["c"]

        failing_readiness = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM email_provider_settings eps
            WHERE eps.tenant_id = ?
              AND eps.status = 'active'
              AND COALESCE(eps.last_check_status, '') = 'incomplete'
            """
            + provider_filter_sql,
            (tenant_id, *provider_filter_params),
        ).fetchone()["c"]

    return {
        "drafts_awaiting_review": int(drafts_awaiting_review or 0),
        "reviewed_not_queued": int(reviewed_not_queued or 0),
        "queued_without_provider": int(queued_without_provider or 0),
        "queued_with_provider": int(queued_with_provider or 0),
        "ready_to_send": int(ready_to_send or 0),
        "cancelled_failed": int(cancelled_failed or 0),
        "active_providers": int(active_providers or 0),
        "providers_failing_readiness": int(failing_readiness or 0),
    }


def get_drafts_awaiting_review(tenant_id, limit=10, filters=None):
    safe_limit = max(1, min(int(limit or 10), 100))
    filter_sql, filter_params = build_filter_clause(
        filters,
        {
            "client_entity_id": "d.client_entity_id",
            "task_id": "d.task_id",
            "draft_status": "d.status",
            "date_column": "d.created_at",
            "search_columns": ["c.name", "t.title", "d.subject", "d.body"],
        },
    )
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT d.id AS draft_id,
                   d.created_at,
                   d.subject,
                   c.name AS client_name,
                   t.title AS task_title
            FROM document_communication_drafts d
            JOIN client_entities c ON c.id = d.client_entity_id AND c.tenant_id = d.tenant_id
            JOIN compliance_tasks t ON t.id = d.task_id AND t.tenant_id = d.tenant_id
            WHERE d.tenant_id = ?
              AND d.draft_type = 'email'
              AND d.status = 'draft'
            """
            + filter_sql
            + """
            ORDER BY datetime(d.created_at) DESC, d.id DESC
            LIMIT ?
            """,
            (tenant_id, *filter_params, safe_limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_reviewed_drafts_not_queued(tenant_id, limit=10, filters=None):
    safe_limit = max(1, min(int(limit or 10), 100))
    filter_sql, filter_params = build_filter_clause(
        filters,
        {
            "client_entity_id": "d.client_entity_id",
            "task_id": "d.task_id",
            "draft_status": "d.status",
            "date_column": "d.reviewed_at",
            "search_columns": ["c.name", "t.title", "d.subject", "d.body"],
        },
    )
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT d.id AS draft_id,
                   d.reviewed_at,
                   d.subject,
                   c.name AS client_name,
                   t.title AS task_title
            FROM document_communication_drafts d
            JOIN client_entities c ON c.id = d.client_entity_id AND c.tenant_id = d.tenant_id
            JOIN compliance_tasks t ON t.id = d.task_id AND t.tenant_id = d.tenant_id
            LEFT JOIN email_send_queue q
              ON q.tenant_id = d.tenant_id
             AND q.draft_id = d.id
            WHERE d.tenant_id = ?
              AND d.draft_type = 'email'
              AND d.status = 'reviewed'
              AND q.id IS NULL
            """
            + filter_sql
            + """
            ORDER BY datetime(d.reviewed_at) DESC, d.id DESC
            LIMIT ?
            """,
            (tenant_id, *filter_params, safe_limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_queue_items_without_provider(tenant_id, limit=10, filters=None):
    safe_limit = max(1, min(int(limit or 10), 100))
    filter_sql, filter_params = build_filter_clause(
        filters,
        {
            "client_entity_id": "q.client_entity_id",
            "task_id": "q.task_id",
            "provider_id": "q.provider_setting_id",
            "queue_status": "q.status",
            "date_column": "q.queued_at",
            "search_columns": ["c.name", "t.title", "q.subject", "q.to_email", "eps.display_name", "eps.from_email"],
        },
    )
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT q.id AS queue_id,
                   q.queued_at,
                   q.to_email,
                   q.subject,
                   c.name AS client_name,
                   t.title AS task_title,
                   eps.display_name AS provider_display_name
            FROM email_send_queue q
            JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE q.tenant_id = ?
              AND q.status = 'queued'
              AND q.provider_setting_id IS NULL
            """
            + filter_sql
            + """
            ORDER BY datetime(q.queued_at) DESC, q.id DESC
            LIMIT ?
            """,
            (tenant_id, *filter_params, safe_limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_ready_to_send_items(tenant_id, limit=10, filters=None):
    safe_limit = max(1, min(int(limit or 10), 100))
    filter_sql, filter_params = build_filter_clause(
        filters,
        {
            "client_entity_id": "q.client_entity_id",
            "task_id": "q.task_id",
            "provider_id": "q.provider_setting_id",
            "queue_status": "q.status",
            "date_column": "q.updated_at",
            "search_columns": ["c.name", "t.title", "q.subject", "q.to_email", "eps.display_name", "eps.from_email"],
        },
    )
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT q.id AS queue_id,
                   q.queued_at,
                   q.to_email,
                   q.subject,
                   c.name AS client_name,
                   t.title AS task_title,
                   eps.display_name AS provider_display_name,
                   eps.provider_type AS provider_type,
                   eps.last_check_status AS provider_last_check_status
            FROM email_send_queue q
            JOIN client_entities c ON c.id = q.client_entity_id AND c.tenant_id = q.tenant_id
            JOIN compliance_tasks t ON t.id = q.task_id AND t.tenant_id = q.tenant_id
            LEFT JOIN email_provider_settings eps ON eps.id = q.provider_setting_id AND eps.tenant_id = q.tenant_id
            WHERE q.tenant_id = ?
              AND q.status = 'ready_to_send'
            """
            + filter_sql
            + """
            ORDER BY datetime(q.updated_at) DESC, q.id DESC
            LIMIT ?
            """,
            (tenant_id, *filter_params, safe_limit),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_provider_readiness_summary(tenant_id, filters=None):
    filter_sql, filter_params = build_filter_clause(
        filters,
        {
            "provider_id": "eps.id",
            "date_column": "eps.updated_at",
            "search_columns": ["eps.display_name", "eps.from_email"],
        },
    )
    with db.get_db() as conn:
        stats = conn.execute(
            """
            SELECT
                SUM(CASE WHEN eps.status = 'active' THEN 1 ELSE 0 END) AS active_providers,
                SUM(CASE WHEN eps.status = 'active' AND COALESCE(eps.last_check_status, '') = 'incomplete' THEN 1 ELSE 0 END) AS providers_failing_readiness
            FROM email_provider_settings eps
            WHERE eps.tenant_id = ?
            """
            + filter_sql,
            (tenant_id, *filter_params),
        ).fetchone()

        rows = conn.execute(
            """
            SELECT eps.id AS provider_id,
                   eps.display_name,
                   eps.provider_type,
                   eps.from_email,
                   eps.status,
                   eps.is_default,
                   eps.last_check_status,
                   eps.last_error,
                   eps.last_checked_at
            FROM email_provider_settings eps
            WHERE eps.tenant_id = ?
            """
            + filter_sql
            + """
            ORDER BY CASE WHEN eps.status = 'active' THEN 0 ELSE 1 END,
                     eps.is_default DESC,
                     eps.display_name ASC
            LIMIT 20
            """,
            (tenant_id, *filter_params),
        ).fetchall()

    return {
        "active_providers": int((stats["active_providers"] if stats else 0) or 0),
        "providers_failing_readiness": int((stats["providers_failing_readiness"] if stats else 0) or 0),
        "providers": _rows_to_dicts(rows),
    }

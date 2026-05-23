from datetime import datetime

import db
import plans


def current_period_month():
    return datetime.now().strftime("%Y-%m")


def _get_tenant_plan(conn, tenant_id):
    row = conn.execute(
        """
        SELECT plan
        FROM tenants
        WHERE id = ?
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone()
    if not row:
        return "starter"
    return (row["plan"] or "starter").strip().lower() or "starter"


def _pct(used, limit):
    if not limit:
        return 0
    value = int(round((float(used) / float(limit)) * 100))
    if value < 0:
        return 0
    return value


def get_or_create_usage_meter(conn, tenant_id, period_month=None):
    month = period_month or current_period_month()
    row = conn.execute(
        """
        SELECT *
        FROM usage_meters
        WHERE tenant_id = ? AND period_month = ?
        LIMIT 1
        """,
        (tenant_id, month),
    ).fetchone()
    if row:
        return row

    conn.execute(
        """
        INSERT INTO usage_meters (
            tenant_id, period_month, ai_tasks_used, llm_tokens_used,
            llm_cost_usd, documents_uploaded, document_requests_created
        ) VALUES (?, ?, 0, 0, 0, 0, 0)
        """,
        (tenant_id, month),
    )

    return conn.execute(
        """
        SELECT *
        FROM usage_meters
        WHERE tenant_id = ? AND period_month = ?
        LIMIT 1
        """,
        (tenant_id, month),
    ).fetchone()


def get_usage_summary(tenant_id, period_month=None):
    month = period_month or current_period_month()
    with db.get_db() as conn:
        plan = _get_tenant_plan(conn, tenant_id)
        limits = plans.get_plan_limits(plan)
        usage_row = get_or_create_usage_meter(conn, tenant_id, month)

        clients_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM client_entities
            WHERE tenant_id = ? AND status = 'active'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        users_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM firm_users
            WHERE tenant_id = ? AND is_active = 1
            """,
            (tenant_id,),
        ).fetchone()["c"]

        ai_tasks_used = int(usage_row["ai_tasks_used"] or 0)
        document_requests_created = int(usage_row["document_requests_created"] or 0)

    usage_data = {
        "period_month": month,
        "ai_tasks_used": ai_tasks_used,
        "document_requests_created": document_requests_created,
        "llm_tokens_used": int(usage_row["llm_tokens_used"] or 0),
        "llm_cost_usd": float(usage_row["llm_cost_usd"] or 0),
        "documents_uploaded": int(usage_row["documents_uploaded"] or 0),
    }

    counts = {
        "clients_count": int(clients_count or 0),
        "users_count": int(users_count or 0),
        "ai_tasks_used": ai_tasks_used,
        "document_requests_created": document_requests_created,
    }

    percentages = {
        "clients_pct": _pct(counts["clients_count"], limits["max_clients"]),
        "ai_tasks_pct": _pct(ai_tasks_used, limits["max_ai_tasks_per_month"]),
        "document_requests_pct": _pct(document_requests_created, limits["max_document_requests_per_month"]),
        "users_pct": _pct(counts["users_count"], limits["max_users"]),
    }

    return {
        "plan": plan,
        "plan_display_name": plans.get_plan_display_name(plan),
        "limits": limits,
        "usage": usage_data,
        "counts": counts,
        "percentages": percentages,
    }


def check_client_limit(tenant_id):
    with db.get_db() as conn:
        plan = _get_tenant_plan(conn, tenant_id)
        limits = plans.get_plan_limits(plan)

        active_clients = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM client_entities
            WHERE tenant_id = ? AND status = 'active'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        if int(active_clients or 0) >= int(limits["max_clients"]):
            raise ValueError(
                "Client limit reached for your current plan. Please upgrade to add more clients."
            )


def _increment_usage_counter(conn, tenant_id, column_name, amount, limit_value, error_message):
    meter = get_or_create_usage_meter(conn, tenant_id)
    current_value = int(meter[column_name] or 0)
    increment = max(0, int(amount or 0))
    new_value = current_value + increment

    if new_value > int(limit_value):
        raise ValueError(error_message)

    conn.execute(
        f"""
        UPDATE usage_meters
        SET {column_name} = ?, updated_at = CURRENT_TIMESTAMP
        WHERE tenant_id = ? AND period_month = ?
        """,  # noqa: S608
        (new_value, tenant_id, meter["period_month"]),
    )


def increment_ai_task_usage(tenant_id, amount=1):
    with db.get_db() as conn:
        plan = _get_tenant_plan(conn, tenant_id)
        limits = plans.get_plan_limits(plan)
        _increment_usage_counter(
            conn=conn,
            tenant_id=tenant_id,
            column_name="ai_tasks_used",
            amount=amount,
            limit_value=limits["max_ai_tasks_per_month"],
            error_message="Monthly AI task limit reached for your current plan. Please upgrade or wait until next month.",
        )


def increment_document_request_usage(tenant_id, amount=1, conn=None):
    if conn is not None:
        plan = _get_tenant_plan(conn, tenant_id)
        limits = plans.get_plan_limits(plan)
        _increment_usage_counter(
            conn=conn,
            tenant_id=tenant_id,
            column_name="document_requests_created",
            amount=amount,
            limit_value=limits["max_document_requests_per_month"],
            error_message="Monthly document request limit reached for your current plan. Please upgrade or wait until next month.",
        )
        return

    with db.get_db() as owned_conn:
        plan = _get_tenant_plan(owned_conn, tenant_id)
        limits = plans.get_plan_limits(plan)
        _increment_usage_counter(
            conn=owned_conn,
            tenant_id=tenant_id,
            column_name="document_requests_created",
            amount=amount,
            limit_value=limits["max_document_requests_per_month"],
            error_message="Monthly document request limit reached for your current plan. Please upgrade or wait until next month.",
        )


def check_user_limit(tenant_id):
    with db.get_db() as conn:
        plan = _get_tenant_plan(conn, tenant_id)
        limits = plans.get_plan_limits(plan)

        active_users = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM firm_users
            WHERE tenant_id = ? AND is_active = 1
            """,
            (tenant_id,),
        ).fetchone()["c"]

        if int(active_users or 0) >= int(limits["max_users"]):
            raise ValueError(
                "User limit reached for your current plan. Please upgrade to add more users."
            )


def _get_rate_window_start(window_minutes):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=window_minutes)
    return window_start.isoformat()


def check_hourly_ai_rate_limit(tenant_id):
    """
    Check if tenant is within hourly AI task rate limit.
    Raises ValueError if limit is exceeded.
    """
    with db.get_db() as conn:
        plan = _get_tenant_plan(conn, tenant_id)
        limits = plans.get_plan_limits(plan)
        max_per_hour = limits.get("max_ai_tasks_per_hour", 10)
        
        if not max_per_hour:
            return  # No rate limit configured
        
        # Check usage in last hour from task_status_history
        window_start = _get_rate_window_start(60)
        
        recent_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM task_status_history
            WHERE tenant_id = ?
              AND new_status IN ('ai_queued', 'ai_processing')
              AND created_at >= ?
            """,
            (tenant_id, window_start),
        ).fetchone()["c"]
        
        if int(recent_count or 0) >= int(max_per_hour):
            raise ValueError(
                f"Hourly AI task rate limit exceeded ({max_per_hour} tasks/hour). "
                "Please wait before submitting more AI tasks."
            )


def check_daily_connector_rate_limit(tenant_id):
    """
    Check if tenant is within daily connector run rate limit.
    Raises ValueError if limit is exceeded.
    """
    with db.get_db() as conn:
        plan = _get_tenant_plan(conn, tenant_id)
        limits = plans.get_plan_limits(plan)
        max_per_day = limits.get("max_connector_runs_per_day", 5)
        
        if not max_per_day:
            return  # No rate limit configured
        
        # Check usage today from audit_logs
        today_start = datetime.now().strftime("%Y-%m-%d")
        
        recent_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM audit_logs
            WHERE tenant_id = ?
              AND action LIKE 'connector_%'
              AND created_at >= ?
            """,
            (tenant_id, today_start),
        ).fetchone()["c"]
        
        if int(recent_count or 0) >= int(max_per_day):
            raise ValueError(
                f"Daily connector rate limit exceeded ({max_per_day} runs/day). "
                "Please try again tomorrow."
            )

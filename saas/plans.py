PLAN_LIMITS = {
    "starter": {
        "display_name": "Starter",
        "monthly_price": 2999,
        "max_clients": 10,
        "max_ai_tasks_per_month": 100,
        "max_document_requests_per_month": 300,
        "max_users": 2,
        "max_ai_tasks_per_hour": 10,
        "max_connector_runs_per_day": 5,
    },
    "pro": {
        "display_name": "Pro",
        "monthly_price": 7999,
        "max_clients": 50,
        "max_ai_tasks_per_month": 500,
        "max_document_requests_per_month": 1500,
        "max_users": 10,
        "max_ai_tasks_per_hour": 25,
        "max_connector_runs_per_day": 20,
    },
    "agency": {
        "display_name": "Agency",
        "monthly_price": 19999,
        "max_clients": 9999,
        "max_ai_tasks_per_month": 5000,
        "max_document_requests_per_month": 10000,
        "max_users": 999,
        "max_ai_tasks_per_hour": 100,
        "max_connector_runs_per_day": 100,
    },
}


def get_plan_limits(plan):
    key = (plan or "starter").strip().lower()
    return PLAN_LIMITS.get(key, PLAN_LIMITS["starter"])


def get_plan_display_name(plan):
    return get_plan_limits(plan)["display_name"]


def format_limit(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)

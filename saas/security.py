from functools import wraps

from flask import flash, redirect, request, session, url_for

import db


def get_current_user_id():
    return session.get("user_id")


def get_current_tenant():
    user_id = get_current_user_id()
    if not user_id:
        return None
    return db.get_current_tenant_for_user(user_id)


def get_current_tenant_id():
    tenant = get_current_tenant()
    return tenant["id"] if tenant else None


def get_current_role():
    user_id = get_current_user_id()
    tenant = get_current_tenant()
    if not user_id or not tenant:
        return "viewer"

    tenant_id = tenant["id"]
    role = db.get_user_role(user_id, tenant_id)
    if role:
        return str(role).strip().lower()

    # If owner row is missing for the tenant owner, heal it automatically.
    if tenant["user_id"] == user_id:
        db.ensure_owner_firm_user(user_id, tenant_id)
        return "owner"

    return "viewer"


def has_role(required_roles):
    current_role = get_current_role()
    if current_role == "owner":
        return True

    if isinstance(required_roles, str):
        allowed_roles = {required_roles.strip().lower()}
    else:
        allowed_roles = {str(role).strip().lower() for role in (required_roles or [])}

    return current_role in allowed_roles


def require_roles(required_roles):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            if not get_current_user_id():
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))

            if not has_role(required_roles):
                flash("You are not allowed to access this page.", "warning")
                return redirect(url_for("dashboard"))

            return func(*args, **kwargs)

        return wrapped

    return decorator


def get_request_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr


def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response

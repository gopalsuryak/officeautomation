from functools import wraps
import os

from flask import flash, redirect, request, session, url_for

import db

# Role hierarchy — higher index = higher privilege.
# owner is handled separately (always True).
_ROLE_RANK = {
    "viewer":    0,
    "assistant": 1,
    "senior":    2,
    "manager":   3,
    "partner":   4,
}


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

    # Also grant access if the user's rank is >= any allowed role's rank.
    current_rank = _ROLE_RANK.get(current_role, -1)
    for role in allowed_roles:
        if current_rank >= _ROLE_RANK.get(role, 999):
            return True

    return False


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


TRUSTED_PROXY_IPS = set(
    ip.strip()
    for ip in os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if ip.strip()
)


def get_request_ip():
    """
    Get the client IP address from the request.
    Only trusts X-Forwarded-For from trusted proxy IPs.
    """
    remote_addr = request.remote_addr or ""
    if remote_addr in TRUSTED_PROXY_IPS:
        forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
    return remote_addr


def _generate_csp_nonce() -> str:
    """Generate a CSP nonce for this request."""
    import secrets
    return secrets.token_hex(16)


def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # Content-Security-Policy for XSS protection
    # B-01 fix: Allow Bootstrap CDN assets from jsdelivr.net
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    return response


def enforce_production_security(app_config: dict) -> None:
    """
    Apply production-grade security settings to Flask app config.
    Call this once during startup when APP_ENV=production.
    """
    app_config.setdefault("SESSION_COOKIE_SECURE", True)
    app_config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app_config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app_config.setdefault("REMEMBER_COOKIE_SECURE", True)
    app_config.setdefault("REMEMBER_COOKIE_HTTPONLY", True)


def check_required_env_vars() -> list[str]:
    """
    Validate that required environment variables are set in production.
    Returns list of missing variable names (empty if all present).
    """
    required = ["SECRET_KEY"]
    missing = []
    for var in required:
        if not os.environ.get(var):
            missing.append(var)
    return missing


def validate_production_credentials() -> tuple[list[str], list[str]]:
    """
    Validate that API credentials are properly configured in production.
    Returns (missing_secrets, weak_secrets) - empty lists if all credentials are valid.
    """
    missing = []
    weak = []

    # Check PAPERCLIP_ADMIN_KEY - critical for tenant provisioning
    paperclip_key = os.environ.get("PAPERCLIP_ADMIN_API_KEY", "")
    if not paperclip_key:
        missing.append("PAPERCLIP_ADMIN_API_KEY")
    elif len(paperclip_key) < 32:
        weak.append("PAPERCLIP_ADMIN_API_KEY (too short)")

    # Check RAZORPAY_KEY_SECRET - critical for payment verification
    razorpay_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not razorpay_secret:
        missing.append("RAZORPAY_KEY_SECRET")
    elif len(razorpay_secret) < 20:
        weak.append("RAZORPAY_KEY_SECRET (too short)")

    return missing, weak

"""
Production WSGI entry point for CA Assist SaaS portal.

Usage:
    gunicorn --workers 2 --bind 0.0.0.0:5000 wsgi:app

Or via systemd/systemd service, see DOCUMENTATION.md section 8.

Environment variables:
    SECRET_KEY           - Flask session signing key (required in production)
    FLASK_ENV           - Set to 'production' for production mode
    APP_ENV            - Set to 'production' to enforce strict security
    CA_ASSIST_ENCRYPTION_KEY - Fernet key for credential encryption
"""
import os

from app import app as application

# Production hardening
if os.environ.get("FLASK_ENV") == "production":
    application.config["SESSION_COOKIE_SECURE"] = True
    application.config["SESSION_COOKIE_HTTPONLY"] = True
    application.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    application.config["REMEMBER_COOKIE_SECURE"] = True
    application.config["REMEMBER_COOKIE_HTTPONLY"] = True
    application.config["PREFERRED_URL_SCHEME"] = "https"
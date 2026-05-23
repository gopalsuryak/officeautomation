"""
WhatsApp Sender — Core Sending Layer
CA Assist (Wave 14: WhatsApp + Chromium Integration)

Supports two providers selectable via env var WHATSAPP_PROVIDER:
  - "twilio"  : Twilio WhatsApp API (default if TWILIO_ACCOUNT_SID is set)
  - "meta"    : Meta Cloud API / WhatsApp Business Platform

Environment variables:
  WHATSAPP_PROVIDER       — "twilio" or "meta" (auto-detected if omitted)
  TWILIO_ACCOUNT_SID      — Twilio account SID
  TWILIO_AUTH_TOKEN       — Twilio auth token
  TWILIO_WHATSAPP_FROM    — sender number in format "whatsapp:+1XXXXXXXXXX"
  WHATSAPP_PHONE_ID       — Meta Cloud API phone number ID
  WHATSAPP_API_TOKEN      — Meta Cloud API permanent access token

No messages are sent automatically. Every outbound message requires
an explicit call after a manual approval step in the UI.
"""
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

WHATSAPP_PROVIDER_TWILIO = "twilio"
WHATSAPP_PROVIDER_META = "meta"

_VALID_PROVIDERS = {WHATSAPP_PROVIDER_TWILIO, WHATSAPP_PROVIDER_META}


def get_whatsapp_provider() -> str:
    """
    Return the active WhatsApp provider name.
    Reads WHATSAPP_PROVIDER env var first; falls back to auto-detection.
    Raises RuntimeError if no provider can be determined.
    """
    configured = os.environ.get("WHATSAPP_PROVIDER", "").strip().lower()
    if configured in _VALID_PROVIDERS:
        return configured

    # Auto-detect: whichever provider has its primary credential set
    if os.environ.get("TWILIO_ACCOUNT_SID"):
        return WHATSAPP_PROVIDER_TWILIO
    if os.environ.get("WHATSAPP_PHONE_ID"):
        return WHATSAPP_PROVIDER_META

    raise RuntimeError(
        "WhatsApp provider is not configured. "
        "Set WHATSAPP_PROVIDER=twilio or WHATSAPP_PROVIDER=meta and the required credentials."
    )


def is_whatsapp_configured() -> bool:
    """Return True if at least one WhatsApp provider is configured."""
    try:
        get_whatsapp_provider()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Phone number normalisation
# ---------------------------------------------------------------------------

def _normalise_phone(phone: str) -> str:
    """
    Strip non-digit characters, ensure E.164 with leading '+'.
    Raises ValueError on clearly invalid numbers.
    """
    digits = re.sub(r"[^\d+]", "", (phone or "").strip())
    # Strip leading zeros if no country code detected
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = "+" + digits.lstrip("+")
    # Must have at least 7 digits (shortest valid phone number)
    digit_only = digits.replace("+", "")
    if len(digit_only) < 7 or len(digit_only) > 15:
        raise ValueError(f"Invalid phone number: {phone!r}")
    return digits


# ---------------------------------------------------------------------------
# Twilio sending
# ---------------------------------------------------------------------------

def _send_via_twilio(to_phone: str, body: str, media_url: str | None = None) -> dict:
    """Send a WhatsApp message via Twilio. Returns provider response dict."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.environ.get("TWILIO_WHATSAPP_FROM", "").strip()

    if not account_sid or not auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set.")
    if not from_number:
        raise RuntimeError("TWILIO_WHATSAPP_FROM must be set (e.g. whatsapp:+14155238886).")

    to_wa = f"whatsapp:{to_phone}" if not to_phone.startswith("whatsapp:") else to_phone

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    payload: dict = {
        "From": from_number,
        "To": to_wa,
        "Body": body,
    }
    if media_url:
        payload["MediaUrl"] = media_url

    resp = requests.post(
        url,
        data=payload,
        auth=(account_sid, auth_token),
        timeout=15,
    )

    if resp.status_code not in (200, 201):
        error_data = {}
        try:
            error_data = resp.json()
        except Exception:
            pass
        error_msg = error_data.get("message") or resp.text[:200]
        raise RuntimeError(f"Twilio API error {resp.status_code}: {error_msg}")

    data = resp.json()
    return {
        "provider": WHATSAPP_PROVIDER_TWILIO,
        "message_id": data.get("sid"),
        "status": data.get("status", "queued"),
        "to": to_phone,
        "from": data.get("from"),
    }


# ---------------------------------------------------------------------------
# Meta Cloud API sending
# ---------------------------------------------------------------------------

def _send_via_meta(to_phone: str, body: str, media_url: str | None = None) -> dict:
    """Send a WhatsApp message via Meta Cloud API. Returns provider response dict."""
    phone_id = os.environ.get("WHATSAPP_PHONE_ID", "").strip()
    api_token = os.environ.get("WHATSAPP_API_TOKEN", "").strip()

    if not phone_id or not api_token:
        raise RuntimeError("WHATSAPP_PHONE_ID and WHATSAPP_API_TOKEN must be set.")

    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    if media_url:
        # Send as document or image message
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone.lstrip("+"),
            "type": "image",
            "image": {"link": media_url, "caption": body},
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone.lstrip("+"),
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }

    resp = requests.post(url, json=payload, headers=headers, timeout=15)

    if resp.status_code not in (200, 201):
        error_data = {}
        try:
            error_data = resp.json()
        except Exception:
            pass
        error_msg = (
            error_data.get("error", {}).get("message")
            or resp.text[:200]
        )
        raise RuntimeError(f"Meta WhatsApp API error {resp.status_code}: {error_msg}")

    data = resp.json()
    messages = data.get("messages", [{}])
    return {
        "provider": WHATSAPP_PROVIDER_META,
        "message_id": messages[0].get("id") if messages else None,
        "status": "sent",
        "to": to_phone,
        "from": phone_id,
    }


# ---------------------------------------------------------------------------
# Public send function
# ---------------------------------------------------------------------------

def send_whatsapp_message(
    to_phone: str,
    body: str,
    media_url: str | None = None,
    provider: str | None = None,
) -> dict:
    """
    Send a WhatsApp message to the given phone number.

    Args:
        to_phone:  Recipient phone in E.164 format (e.g. "+919876543210")
        body:      Message text
        media_url: Optional URL for image/document attachment
        provider:  Override provider ("twilio" or "meta"). Auto-detected if None.

    Returns:
        dict with keys: provider, message_id, status, to, from

    Raises:
        ValueError:   Invalid phone number
        RuntimeError: Provider not configured or API error
    """
    normalised = _normalise_phone(to_phone)
    active_provider = provider or get_whatsapp_provider()

    if active_provider not in _VALID_PROVIDERS:
        raise ValueError(f"Unknown WhatsApp provider: {active_provider!r}")

    logger.info("Sending WhatsApp via %s to %s (body length %d)", active_provider, normalised, len(body))

    if active_provider == WHATSAPP_PROVIDER_TWILIO:
        return _send_via_twilio(normalised, body, media_url)
    else:
        return _send_via_meta(normalised, body, media_url)

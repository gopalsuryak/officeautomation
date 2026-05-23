"""
Provisions a new CA firm as a Paperclip company and hires the CA agent.
Called automatically when a customer completes payment.
"""
import os
import json
import urllib.request
import urllib.error

PAPERCLIP_URL       = os.environ.get("PAPERCLIP_API_URL", "http://localhost:3100")
PAPERCLIP_ADMIN_KEY = os.environ.get("PAPERCLIP_ADMIN_API_KEY", "")
AGENT_COMMAND       = os.environ.get("AGENT_COMMAND", "python agent.py")
AGENT_WORKING_DIR   = os.environ.get("AGENT_WORKING_DIR")
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")


def _validate_config():
    """Validate required configuration at startup."""
    if not AGENT_WORKING_DIR:
        raise RuntimeError("AGENT_WORKING_DIR environment variable is required.")


def _api(method: str, path: str, body: dict = None) -> dict:
    url  = f"{PAPERCLIP_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {PAPERCLIP_ADMIN_KEY}",
            "Content-Type":  "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── tenant lifecycle ──────────────────────────────────────────────────────

def provision_tenant(firm_name: str, user_email: str, plan: str = "starter") -> dict:
    """Creates a Paperclip company + hires the CA agent. Returns company_id + agent_id."""
    company    = _api("POST", "/api/companies", {"name": firm_name, "email": user_email})
    company_id = company["id"]

    agent = _api("POST", f"/api/companies/{company_id}/agents", {
        "name":              "CA Compliance Agent",
        "adapter":           "cli",
        "command":           AGENT_COMMAND,
        "workingDirectory":  AGENT_WORKING_DIR,
        "secrets": {
            "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
            "TENANT_PLAN":       plan,
        },
        "heartbeatSchedule": "*/30 * * * *",   # every 30 min
    })

    return {"company_id": company_id, "agent_id": agent["id"]}


# ── issue (task) helpers ──────────────────────────────────────────────────

def create_task(company_id: str, title: str, description: str) -> dict:
    return _api("POST", f"/api/companies/{company_id}/issues", {
        "title":       title,
        "description": description,
    })


def get_task(issue_id: str) -> dict:
    return _api("GET", f"/api/issues/{issue_id}")


def list_tasks(company_id: str) -> list:
    result = _api("GET", f"/api/companies/{company_id}/issues")
    return result if isinstance(result, list) else result.get("issues", [])


def get_task_comments(issue_id: str) -> list:
    result = _api("GET", f"/api/issues/{issue_id}/comments")
    return result if isinstance(result, list) else result.get("comments", [])


def init_provisioner() -> None:
    """
    Validate provisioner configuration at startup.
    Call this once when the application starts in production.
    """
    _validate_config()

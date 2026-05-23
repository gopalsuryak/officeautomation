"""
Paperclip REST API client.
Reads PAPERCLIP_* env vars injected by Paperclip at heartbeat time
and exposes typed helpers for the operations a CLI agent needs.
"""

import os
import json
import urllib.request
import urllib.error
from typing import Any


class PaperclipClient:
    def __init__(self):
        self.api_url = os.environ.get("PAPERCLIP_API_URL", "http://localhost:3100")
        self.api_key = os.environ.get("PAPERCLIP_API_KEY", "")
        self.agent_id = os.environ.get("PAPERCLIP_AGENT_ID", "")
        self.company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")
        self.run_id = os.environ.get("PAPERCLIP_RUN_ID", "")
        self.task_id = os.environ.get("PAPERCLIP_TASK_ID", "")
        self.wake_reason = os.environ.get("PAPERCLIP_WAKE_REASON", "")

    def _headers(self, mutating: bool = False) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if mutating and self.run_id:
            h["X-Paperclip-Run-Id"] = self.run_id
        return h

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        url = f"{self.api_url}/api{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers=self._headers(mutating=(method in ("POST", "PATCH", "PUT", "DELETE"))),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            raise RuntimeError(f"Paperclip API {method} {path} failed [{e.code}]: {body_text}") from e

    # ── Agent ──────────────────────────────────────────────────────────────
    def get_me(self) -> dict:
        return self._request("GET", "/agents/me")

    # ── Issues ─────────────────────────────────────────────────────────────
    def get_issue(self, issue_id: str) -> dict:
        return self._request("GET", f"/issues/{issue_id}")

    def checkout_issue(self, issue_id: str) -> dict:
        return self._request(
            "POST",
            f"/issues/{issue_id}/checkout",
            {
                "agentId": self.agent_id,
                "expectedStatuses": ["todo", "backlog", "blocked", "in_review"],
            },
        )

    def update_issue(self, issue_id: str, status: str, comment: str = "") -> dict:
        payload: dict = {"status": status}
        if comment:
            payload["comment"] = comment
        return self._request("PATCH", f"/issues/{issue_id}", payload)

    def post_comment(self, issue_id: str, body: str) -> dict:
        return self._request("POST", f"/issues/{issue_id}/comments", {"body": body})

    def get_comments(self, issue_id: str) -> list:
        result = self._request("GET", f"/issues/{issue_id}/comments")
        if isinstance(result, list):
            return result
        return result.get("comments", [])

    def create_child_issue(self, parent_id: str, title: str, description: str = "", assignee_agent_id: str = "") -> dict:
        payload: dict = {
            "title": title,
            "parentId": parent_id,
            "companyId": self.company_id,
        }
        if description:
            payload["description"] = description
        if assignee_agent_id:
            payload["assigneeAgentId"] = assignee_agent_id
        return self._request("POST", f"/companies/{self.company_id}/issues", payload)

    def list_my_issues(self) -> list:
        result = self._request(
            "GET",
            f"/companies/{self.company_id}/issues"
            f"?assigneeAgentId={self.agent_id}"
            f"&status=todo,in_progress,in_review,blocked",
        )
        if isinstance(result, list):
            return result
        return result.get("issues", [])

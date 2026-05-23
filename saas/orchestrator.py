import json
from typing import Any

import db
import provisioner


class AgentOrchestrator:
    def provision_tenant(self, firm_name, user_email, plan):
        raise NotImplementedError

    def create_agent_task(self, tenant_id, task_id, task_type, payload):
        raise NotImplementedError

    def get_agent_task(self, external_task_id):
        raise NotImplementedError

    def get_agent_comments(self, external_task_id):
        raise NotImplementedError

    def cancel_agent_task(self, external_task_id):
        raise NotImplementedError


class PaperclipOrchestrator(AgentOrchestrator):
    def provision_tenant(self, firm_name, user_email, plan):
        # Keep provisioning behavior centralized in existing code for compatibility.
        return provisioner.provision_tenant(firm_name, user_email, plan)

    def _get_tenant(self, tenant_id: int):
        with db.get_db() as conn:
            return conn.execute(
                "SELECT * FROM tenants WHERE id = ? LIMIT 1",
                (tenant_id,),
            ).fetchone()

    def create_agent_task(self, tenant_id, task_id, task_type, payload):
        tenant = self._get_tenant(int(tenant_id))
        if not tenant:
            raise ValueError("Tenant not found.")

        company_id = tenant["paperclip_company_id"]
        if not company_id:
            raise ValueError("AI background worker is not configured for this tenant yet.")

        task_title = (payload or {}).get("task", {}).get("title", f"Task #{task_id}")
        issue_title = f"AI Draft: {task_title}"

        context = {
            "source": "ca_assist",
            "tenant_id": int(tenant_id),
            "task_id": int(task_id),
            "task_type": task_type,
            "payload": payload or {},
        }

        issue_description = (
            "Background AI job for CA Assist.\n"
            "This job is internal and must be processed using the structured context below.\n"
            "Use context.task_id to map results back to CA Assist in future sync waves.\n\n"
            "```json\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n"
            "```"
        )

        issue = provisioner.create_task(company_id, issue_title, issue_description)
        issue_id = issue.get("id")
        if not issue_id:
            raise RuntimeError("Could not create AI background task.")
        return str(issue_id)

    def get_agent_task(self, external_task_id):
        return provisioner.get_task(str(external_task_id))

    def get_agent_comments(self, external_task_id):
        return provisioner.get_task_comments(str(external_task_id))

    def cancel_agent_task(self, external_task_id):
        # Safe fallback: explicit unsupported result until provider contract is finalised.
        return {
            "ok": False,
            "supported": False,
            "message": "Paperclip task cancellation is not supported in this wave.",
            "external_task_id": str(external_task_id),
        }


def get_orchestrator():
    return PaperclipOrchestrator()

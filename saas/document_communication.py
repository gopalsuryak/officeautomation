# 9. List all communication drafts for register
def list_communication_drafts(tenant_id, filters=None):
    filters = filters or {}
    params = [tenant_id]
    where = ["d.tenant_id = ?"]
    if filters.get("client_entity_id"):
        where.append("d.client_entity_id = ?")
        params.append(filters["client_entity_id"])
    if filters.get("task_id"):
        where.append("d.task_id = ?")
        params.append(filters["task_id"])
    if filters.get("draft_type"):
        where.append("d.draft_type = ?")
        params.append(filters["draft_type"])
    if filters.get("status"):
        where.append("d.status = ?")
        params.append(filters["status"])
    if filters.get("search"):
        search = f"%{filters['search'].lower()}%"
        where.append("(" +
            "LOWER(c.name) LIKE ? OR "
            "LOWER(t.title) LIKE ? OR "
            "LOWER(d.subject) LIKE ? OR "
            "LOWER(d.body) LIKE ?"
            ")")
        params.extend([search, search, search, search])
    where_clause = " AND ".join(where)
    with db.get_db() as conn:
        rows = conn.execute(f'''
            SELECT d.id as draft_id, d.client_entity_id, c.name as client_name, d.task_id, t.title as task_title,
                   d.draft_type, d.status, d.subject,
                   substr(d.body, 1, 120) as body_preview,
                   d.created_at, d.reviewed_at
            FROM document_communication_drafts d
            JOIN client_entities c ON d.client_entity_id = c.id AND c.tenant_id = d.tenant_id
            JOIN compliance_tasks t ON d.task_id = t.id AND t.tenant_id = d.tenant_id
            WHERE {where_clause}
            ORDER BY d.created_at DESC
        ''', params).fetchall()
        return [dict(row) for row in rows]

# 10. Get communication register summary KPIs
def get_communication_register_summary(tenant_id, filters=None):
    filters = filters or {}
    params = [tenant_id]
    where = ["tenant_id = ?"]
    if filters.get("client_entity_id"):
        where.append("client_entity_id = ?")
        params.append(filters["client_entity_id"])
    if filters.get("task_id"):
        where.append("task_id = ?")
        params.append(filters["task_id"])
    if filters.get("draft_type"):
        where.append("draft_type = ?")
        params.append(filters["draft_type"])
    if filters.get("status"):
        where.append("status = ?")
        params.append(filters["status"])
    if filters.get("search"):
        search = f"%{filters['search'].lower()}%"
        where.append("(" +
            "LOWER(subject) LIKE ? OR "
            "LOWER(body) LIKE ?"
            ")")
        params.extend([search, search])
    where_clause = " AND ".join(where)
    with db.get_db() as conn:
        row = conn.execute(f'''
            SELECT
                COUNT(*) as total_drafts,
                SUM(CASE WHEN draft_type = 'email' THEN 1 ELSE 0 END) as email_count,
                SUM(CASE WHEN draft_type = 'whatsapp' THEN 1 ELSE 0 END) as whatsapp_count,
                SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) as draft_count,
                SUM(CASE WHEN status = 'reviewed' THEN 1 ELSE 0 END) as reviewed_count,
                SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) as archived_count,
                SUM(CASE WHEN date(created_at) >= date('now', 'start of month') THEN 1 ELSE 0 END) as drafts_this_month
            FROM document_communication_drafts
            WHERE {where_clause}
        ''', params).fetchone()
        return dict(row) if row else {}
"""
Document Communication Drafts — Internal Draft Generation for Document Requests
CA Assist (Phase: Document Request Communication Drafts Only)
"""
import json
from datetime import datetime, timezone
import db

DRAFT_TYPES = ["email", "whatsapp"]
DRAFT_STATUSES = ["draft", "reviewed", "archived"]


def _as_dict(row):
    if isinstance(row, dict):
        return row
    return dict(row) if row is not None else {}


def _get_draft_row(conn, tenant_id, draft_id):
    row = conn.execute(
        """
        SELECT d.*, t.title as task_title, c.name as client_name, c.email as client_email
        FROM document_communication_drafts d
        JOIN compliance_tasks t ON d.task_id = t.id AND t.tenant_id = d.tenant_id
        JOIN client_entities c ON d.client_entity_id = c.id AND c.tenant_id = d.tenant_id
        WHERE d.tenant_id = ? AND d.id = ?
        """,
        (tenant_id, draft_id)
    ).fetchone()
    return dict(row) if row else None

# 1. Get pending document requests for a task
def get_pending_document_requests_for_task(tenant_id, task_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT dr.*, t.title as task_title, t.period, t.task_type, c.name as client_name, c.email as client_email
            FROM document_requests dr
            JOIN compliance_tasks t ON dr.task_id = t.id AND t.tenant_id = ?
            JOIN client_entities c ON t.client_entity_id = c.id AND c.tenant_id = ?
            WHERE dr.tenant_id = ? AND dr.task_id = ? AND dr.status = 'requested'
            ORDER BY dr.created_at
            """,
            (tenant_id, tenant_id, tenant_id, task_id)
        ).fetchall()
        return [dict(row) for row in rows]

# 2. Build email draft
def build_email_draft(task, client, document_requests):
    task = _as_dict(task)
    client = _as_dict(client)
    subject = f"Documents required for {task['title']} - {client['name']}"
    body_lines = [
        f"Dear {client['name']},",
        "",
        f"We are writing to request the following documents for your {task['task_type'].upper()} compliance ({task['title']}, period: {task.get('period', 'N/A')}).",
        "",
        "Required documents:",
    ]
    for dr in document_requests:
        body_lines.append(f"- {dr['document_name']}")
    body_lines += [
        "",
        "Please provide these documents at your earliest convenience. If you have any questions or need clarification, feel free to contact us.",
        "",
        "Thank you,",
        "Team, Mehra & Jain Chartered Accountants"
    ]
    return {"subject": subject, "body": "\n".join(body_lines)}

# 3. Build WhatsApp draft
def build_whatsapp_draft(task, client, document_requests):
    task = _as_dict(task)
    client = _as_dict(client)
    lines = [
        f"Hi {client['name']},",
        f"Please share the following docs for {task['title']} ({task['task_type'].upper()}, period: {task.get('period', 'N/A')}):"
    ]
    for dr in document_requests:
        lines.append(f"- {dr['document_name']}")
    lines += [
        "Thanks!",
        "Mehra & Jain CA"
    ]
    return {"subject": "", "body": "\n".join(lines)}

# 4. Create document communication draft
def create_document_communication_draft(tenant_id, task_id, draft_type, user_id=None, ip_address=None):
    if draft_type not in DRAFT_TYPES:
        raise ValueError("Invalid draft_type")
    with db.get_db() as conn:
        # Load task and client
        task = conn.execute(
            "SELECT * FROM compliance_tasks WHERE tenant_id = ? AND id = ?",
            (tenant_id, task_id)
        ).fetchone()
        if not task:
            raise ValueError("Task not found")
        client = conn.execute(
            "SELECT * FROM client_entities WHERE tenant_id = ? AND id = ?",
            (tenant_id, task["client_entity_id"])
        ).fetchone()
        if not client:
            raise ValueError("Client not found")
        # Load pending document requests
        doc_requests = conn.execute(
            "SELECT * FROM document_requests WHERE tenant_id = ? AND task_id = ? AND status = 'requested'",
            (tenant_id, task_id)
        ).fetchall()
        if not doc_requests:
            raise ValueError("No pending document requests for this task")
        doc_requests_list = [dict(row) for row in doc_requests]
        # Build draft
        if draft_type == "email":
            draft = build_email_draft(task, client, doc_requests_list)
        else:
            draft = build_whatsapp_draft(task, client, doc_requests_list)
        # Insert draft
        cur = conn.execute(
            """
            INSERT INTO document_communication_drafts
                (tenant_id, task_id, client_entity_id, draft_type, status, subject, body, document_request_ids_json, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                tenant_id, task_id, client["id"], draft_type, draft.get("subject"), draft["body"],
                json.dumps([dr["id"] for dr in doc_requests_list]), user_id
            )
        )
        draft_id = cur.lastrowid
        db.log_audit(conn, tenant_id, user_id, "document_communication_draft_created", "document_communication_drafts", draft_id, None, draft, None, ip_address)
        # Add system note to task_comments
        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
            VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
            """,
            (tenant_id, task_id, "Document communication draft created.")
        )
        return _get_draft_row(conn, tenant_id, draft_id)

# 5. List drafts for task
def list_drafts_for_task(tenant_id, task_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM document_communication_drafts
            WHERE tenant_id = ? AND task_id = ?
            ORDER BY created_at DESC
            """,
            (tenant_id, task_id)
        ).fetchall()
        return [dict(row) for row in rows]

# 6. Get draft by id
def get_draft(tenant_id, draft_id):
    with db.get_db() as conn:
        return _get_draft_row(conn, tenant_id, draft_id)

# 7. Mark draft reviewed
def mark_draft_reviewed(tenant_id, draft_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        # Update status
        conn.execute(
            """
            UPDATE document_communication_drafts
            SET status = 'reviewed', reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (user_id, tenant_id, draft_id)
        )
        db.log_audit(conn, tenant_id, user_id, "document_communication_draft_reviewed", "document_communication_drafts", draft_id, None, {"status": "reviewed"}, None, ip_address)
        # Add system note
        draft = _get_draft_row(conn, tenant_id, draft_id)
        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body, created_at)
            VALUES (?, ?, NULL, 'system', ?, CURRENT_TIMESTAMP)
            """,
            (tenant_id, draft["task_id"], "Document communication draft marked as reviewed.")
        )
        return _get_draft_row(conn, tenant_id, draft_id)

# 8. Archive draft
def archive_draft(tenant_id, draft_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        conn.execute(
            """
            UPDATE document_communication_drafts
            SET status = 'archived', updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, draft_id)
        )
        db.log_audit(conn, tenant_id, user_id, "document_communication_draft_archived", "document_communication_drafts", draft_id, None, {"status": "archived"}, None, ip_address)
        return _get_draft_row(conn, tenant_id, draft_id)

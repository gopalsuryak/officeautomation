from __future__ import annotations

import json

import db


def get_dashboard_summary(tenant_id):
    with db.get_db() as conn:
        active_clients = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM client_entities
            WHERE tenant_id = ? AND status = 'active'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        open_tasks = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND status NOT IN ('filed', 'closed', 'cancelled')
            """,
            (tenant_id,),
        ).fetchone()["c"]

        overdue_tasks = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND due_date IS NOT NULL
              AND date(due_date) < date('now', 'localtime')
              AND status NOT IN ('filed', 'closed', 'cancelled')
            """,
            (tenant_id,),
        ).fetchone()["c"]

        due_this_week = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND due_date IS NOT NULL
              AND date(due_date) >= date('now', 'localtime')
              AND date(due_date) <= date('now', 'localtime', '+7 day')
              AND status NOT IN ('filed', 'closed', 'cancelled')
            """,
            (tenant_id,),
        ).fetchone()["c"]

        pending_documents_tasks = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status = 'pending_documents'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        pending_document_requests = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_requests
            WHERE tenant_id = ? AND status = 'requested'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        awaiting_review = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status = 'under_review'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        changes_required = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status = 'changes_required'
            """,
            (tenant_id,),
        ).fetchone()["c"]

        ai_queued_or_processing = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ? AND status IN ('ai_queued', 'ai_processing')
            """,
            (tenant_id,),
        ).fetchone()["c"]

        high_risk_ai_outputs = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM ai_outputs ao
            WHERE ao.tenant_id = ?
              AND ao.id = (
                  SELECT MAX(inner_ao.id)
                  FROM ai_outputs inner_ao
                  WHERE inner_ao.tenant_id = ?
                    AND inner_ao.task_id = ao.task_id
              )
              AND (
                  ao.status_recommendation = 'high_risk_review'
                  OR ao.confidence = 'low'
              )
            """,
            (tenant_id, tenant_id),
        ).fetchone()["c"]

        filed_this_month = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND status = 'filed'
              AND date(updated_at) >= date('now', 'start of month', 'localtime')
              AND date(updated_at) < date('now', 'start of month', '+1 month', 'localtime')
            """,
            (tenant_id,),
        ).fetchone()["c"]

        closed_this_month = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND status = 'closed'
              AND date(updated_at) >= date('now', 'start of month', 'localtime')
              AND date(updated_at) < date('now', 'start of month', '+1 month', 'localtime')
            """,
            (tenant_id,),
        ).fetchone()["c"]

    return {
        "active_clients": active_clients,
        "open_tasks": open_tasks,
        "overdue_tasks": overdue_tasks,
        "due_this_week": due_this_week,
        "pending_documents_tasks": pending_documents_tasks,
        "pending_document_requests": pending_document_requests,
        "awaiting_review": awaiting_review,
        "changes_required": changes_required,
        "ai_queued_or_processing": ai_queued_or_processing,
        "high_risk_ai_outputs": high_risk_ai_outputs,
        "filed_this_month": filed_this_month,
        "closed_this_month": closed_this_month,
    }


def get_overdue_tasks(tenant_id, limit=10):
    clean_limit = max(1, int(limit))
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                t.id,
                c.name AS client_name,
                t.title,
                t.period,
                t.due_date,
                t.status,
                t.priority
            FROM compliance_tasks t
            JOIN client_entities c ON c.id = t.client_entity_id AND c.tenant_id = t.tenant_id
            WHERE t.tenant_id = ?
              AND t.due_date IS NOT NULL
              AND date(t.due_date) < date('now', 'localtime')
              AND t.status NOT IN ('filed', 'closed', 'cancelled')
            ORDER BY date(t.due_date) ASC, t.id DESC
            LIMIT ?
            """,
            (tenant_id, clean_limit),
        ).fetchall()


def get_due_soon_tasks(tenant_id, days=7, limit=10):
    clean_days = max(1, int(days))
    clean_limit = max(1, int(limit))
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                t.id,
                c.name AS client_name,
                t.title,
                t.period,
                t.due_date,
                t.status,
                t.priority
            FROM compliance_tasks t
            JOIN client_entities c ON c.id = t.client_entity_id AND c.tenant_id = t.tenant_id
            WHERE t.tenant_id = ?
              AND t.due_date IS NOT NULL
              AND date(t.due_date) >= date('now', 'localtime')
              AND date(t.due_date) <= date('now', 'localtime', '+' || ? || ' day')
              AND t.status NOT IN ('filed', 'closed', 'cancelled')
            ORDER BY date(t.due_date) ASC, t.id DESC
            LIMIT ?
            """,
            (tenant_id, clean_days, clean_limit),
        ).fetchall()


def get_tasks_awaiting_review(tenant_id, limit=10):
    clean_limit = max(1, int(limit))
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                t.id,
                c.name AS client_name,
                t.title,
                t.period,
                t.status,
                ao.confidence,
                ao.status_recommendation
            FROM compliance_tasks t
            JOIN client_entities c ON c.id = t.client_entity_id AND c.tenant_id = t.tenant_id
            LEFT JOIN ai_outputs ao ON ao.id = (
                SELECT MAX(inner_ao.id)
                FROM ai_outputs inner_ao
                WHERE inner_ao.tenant_id = t.tenant_id
                  AND inner_ao.task_id = t.id
            )
            WHERE t.tenant_id = ?
              AND t.status = 'under_review'
            ORDER BY t.updated_at DESC, t.id DESC
            LIMIT ?
            """,
            (tenant_id, clean_limit),
        ).fetchall()


def get_tasks_pending_documents(tenant_id, limit=10):
    clean_limit = max(1, int(limit))
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                t.id,
                c.name AS client_name,
                t.title,
                t.period,
                t.status,
                COALESCE(dr.requested_count, 0) AS requested_docs_count
            FROM compliance_tasks t
            JOIN client_entities c ON c.id = t.client_entity_id AND c.tenant_id = t.tenant_id
            LEFT JOIN (
                SELECT tenant_id, task_id, COUNT(*) AS requested_count
                FROM document_requests
                WHERE status = 'requested'
                GROUP BY tenant_id, task_id
            ) dr ON dr.tenant_id = t.tenant_id AND dr.task_id = t.id
            WHERE t.tenant_id = ?
              AND t.status = 'pending_documents'
            ORDER BY COALESCE(dr.requested_count, 0) DESC, t.updated_at DESC, t.id DESC
            LIMIT ?
            """,
            (tenant_id, clean_limit),
        ).fetchall()


def get_client_wise_pending_summary(tenant_id, limit=10):
    clean_limit = max(1, int(limit))
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT
                c.id AS client_entity_id,
                c.name AS client_name,
                SUM(
                    CASE
                        WHEN t.id IS NOT NULL AND t.status NOT IN ('filed', 'closed', 'cancelled')
                        THEN 1 ELSE 0
                    END
                ) AS open_tasks,
                SUM(
                    CASE
                        WHEN t.id IS NOT NULL
                             AND t.due_date IS NOT NULL
                             AND date(t.due_date) < date('now', 'localtime')
                             AND t.status NOT IN ('filed', 'closed', 'cancelled')
                        THEN 1 ELSE 0
                    END
                ) AS overdue_tasks,
                SUM(CASE WHEN t.status = 'pending_documents' THEN 1 ELSE 0 END) AS pending_documents,
                SUM(CASE WHEN t.status = 'under_review' THEN 1 ELSE 0 END) AS awaiting_review,
                MIN(
                    CASE
                        WHEN t.status NOT IN ('filed', 'closed', 'cancelled')
                             AND t.due_date IS NOT NULL
                        THEN t.due_date
                        ELSE NULL
                    END
                ) AS next_due_date
            FROM client_entities c
            LEFT JOIN compliance_tasks t
                ON t.client_entity_id = c.id
               AND t.tenant_id = c.tenant_id
            WHERE c.tenant_id = ?
              AND c.status = 'active'
            GROUP BY c.id, c.name
            ORDER BY
                overdue_tasks DESC,
                pending_documents DESC,
                awaiting_review DESC,
                CASE WHEN next_due_date IS NULL THEN 1 ELSE 0 END,
                date(next_due_date) ASC,
                c.name ASC
            LIMIT ?
            """,
            (tenant_id, clean_limit),
        ).fetchall()


def get_recent_activity(tenant_id, limit=10):
    clean_limit = max(1, int(limit))
    with db.get_db() as conn:
        return conn.execute(
            """
            SELECT id, created_at, action, entity_type, entity_id, user_id
            FROM audit_logs
            WHERE tenant_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (tenant_id, clean_limit),
        ).fetchall()


def get_recent_ai_outputs(tenant_id, limit=5):
    clean_limit = max(1, int(limit))
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                ao.id,
                ao.task_id,
                ao.created_at,
                ao.confidence,
                ao.status_recommendation,
                ao.risk_flags_json,
                t.title,
                t.period,
                c.name AS client_name
            FROM ai_outputs ao
            JOIN compliance_tasks t ON t.id = ao.task_id AND t.tenant_id = ao.tenant_id
            JOIN client_entities c ON c.id = t.client_entity_id AND c.tenant_id = t.tenant_id
            WHERE ao.tenant_id = ?
            ORDER BY datetime(ao.created_at) DESC, ao.id DESC
            LIMIT ?
            """,
            (tenant_id, clean_limit),
        ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        risk_flags_count = 0
        raw_flags = item.get("risk_flags_json")
        if raw_flags:
            try:
                parsed = json.loads(raw_flags)
                if isinstance(parsed, list):
                    risk_flags_count = len(parsed)
            except Exception:
                risk_flags_count = 0
        item["risk_flags_count"] = risk_flags_count
        result.append(item)

    return result

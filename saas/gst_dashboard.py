from datetime import date

import db

GST_TASK_TYPES = ("gstr1", "gstr3b", "gstr9", "document_checklist", "general_query")
PENDING_GST_REVIEW_STATUSES = ("under_review", "pending_documents", "changes_required", "ai_draft_ready")


def _clean_text(value):
    return str(value or "").strip()


def _to_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def get_gst_dashboard_summary(tenant_id):
    month_start = date.today().replace(day=1).isoformat()

    with db.get_db() as conn:
        runs_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_reconciliation_runs,
                COALESCE(SUM(matched_count), 0) AS matched_total,
                COALESCE(SUM(missing_in_2b_count), 0) AS missing_in_2b_total,
                COALESCE(SUM(missing_in_books_count), 0) AS missing_in_books_total,
                COALESCE(SUM(amount_mismatch_count), 0) AS amount_mismatch_total,
                COALESCE(SUM(tax_mismatch_count), 0) AS tax_mismatch_total
            FROM gst_reconciliation_runs
            WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchone()

        runs_this_month = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM gst_reconciliation_runs
            WHERE tenant_id = ?
              AND date(created_at) >= date(?)
            """,
            (tenant_id, month_start),
        ).fetchone()["c"]

        runs_without_working_note = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM gst_reconciliation_runs r
            LEFT JOIN gst_reconciliation_notes n
              ON n.tenant_id = r.tenant_id
             AND n.reconciliation_run_id = r.id
            WHERE r.tenant_id = ?
              AND n.id IS NULL
            """,
            (tenant_id,),
        ).fetchone()["c"]

        runs_without_review_task = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM gst_reconciliation_runs r
            LEFT JOIN gst_reconciliation_task_links l
              ON l.tenant_id = r.tenant_id
             AND l.reconciliation_run_id = r.id
            WHERE r.tenant_id = ?
              AND l.id IS NULL
            """,
            (tenant_id,),
        ).fetchone()["c"]

        pending_gst_document_requests = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM document_requests dr
            JOIN compliance_tasks t
              ON t.id = dr.task_id
             AND t.tenant_id = dr.tenant_id
            WHERE dr.tenant_id = ?
              AND dr.status = 'requested'
              AND t.task_type IN ('gstr1', 'gstr3b', 'gstr9', 'document_checklist', 'general_query')
            """,
            (tenant_id,),
        ).fetchone()["c"]

        pending_gst_review_tasks = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM compliance_tasks
            WHERE tenant_id = ?
              AND task_type IN ('gstr1', 'gstr3b', 'gstr9', 'document_checklist')
              AND status IN ('under_review', 'pending_documents', 'changes_required', 'ai_draft_ready')
            """,
            (tenant_id,),
        ).fetchone()["c"]

        high_risk_working_notes = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM gst_reconciliation_notes
            WHERE tenant_id = ?
              AND (
                    LOWER(COALESCE(confidence, '')) = 'low'
                 OR (
                        COALESCE(TRIM(risk_flags_json), '') <> ''
                    AND LOWER(TRIM(risk_flags_json)) <> '[]'
                    AND LOWER(TRIM(risk_flags_json)) <> 'null'
                 )
              )
            """,
            (tenant_id,),
        ).fetchone()["c"]

    missing_in_2b_total = _to_int(runs_row["missing_in_2b_total"])
    missing_in_books_total = _to_int(runs_row["missing_in_books_total"])
    amount_mismatch_total = _to_int(runs_row["amount_mismatch_total"])
    tax_mismatch_total = _to_int(runs_row["tax_mismatch_total"])

    return {
        "total_reconciliation_runs": _to_int(runs_row["total_reconciliation_runs"]),
        "runs_this_month": _to_int(runs_this_month),
        "total_exceptions": (
            missing_in_2b_total
            + missing_in_books_total
            + amount_mismatch_total
            + tax_mismatch_total
        ),
        "matched_total": _to_int(runs_row["matched_total"]),
        "missing_in_2b_total": missing_in_2b_total,
        "missing_in_books_total": missing_in_books_total,
        "amount_mismatch_total": amount_mismatch_total,
        "tax_mismatch_total": tax_mismatch_total,
        "runs_without_working_note": _to_int(runs_without_working_note),
        "runs_without_review_task": _to_int(runs_without_review_task),
        "pending_gst_document_requests": _to_int(pending_gst_document_requests),
        "pending_gst_review_tasks": _to_int(pending_gst_review_tasks),
        "high_risk_working_notes": _to_int(high_risk_working_notes),
    }


def get_clients_needing_gst_attention(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))
    with db.get_db() as conn:
        rows = conn.execute(
            """
            WITH latest_runs AS (
                SELECT client_entity_id, MAX(id) AS latest_run_id
                FROM gst_reconciliation_runs
                WHERE tenant_id = ?
                GROUP BY client_entity_id
            )
            SELECT
                r.client_entity_id,
                c.name AS client_name,
                r.id AS latest_run_id,
                r.created_at AS latest_run_date,
                (
                    COALESCE(r.missing_in_2b_count, 0)
                  + COALESCE(r.missing_in_books_count, 0)
                  + COALESCE(r.amount_mismatch_count, 0)
                  + COALESCE(r.tax_mismatch_count, 0)
                ) AS exception_count,
                COALESCE(r.missing_in_2b_count, 0) AS missing_in_2b_count,
                COALESCE(r.missing_in_books_count, 0) AS missing_in_books_count,
                COALESCE(r.amount_mismatch_count, 0) AS amount_mismatch_count,
                COALESCE(r.tax_mismatch_count, 0) AS tax_mismatch_count,
                t.id AS linked_task_id,
                t.status AS linked_task_status,
                n.status AS latest_note_status
            FROM latest_runs lr
            JOIN gst_reconciliation_runs r
              ON r.id = lr.latest_run_id
             AND r.tenant_id = ?
            JOIN client_entities c
              ON c.id = r.client_entity_id
             AND c.tenant_id = r.tenant_id
            LEFT JOIN gst_reconciliation_task_links l
              ON l.id = (
                    SELECT MAX(l2.id)
                    FROM gst_reconciliation_task_links l2
                    WHERE l2.tenant_id = r.tenant_id
                      AND l2.reconciliation_run_id = r.id
                )
            LEFT JOIN compliance_tasks t
              ON t.id = l.task_id
             AND t.tenant_id = r.tenant_id
            LEFT JOIN gst_reconciliation_notes n
              ON n.id = (
                    SELECT MAX(n2.id)
                    FROM gst_reconciliation_notes n2
                    WHERE n2.tenant_id = r.tenant_id
                      AND n2.reconciliation_run_id = r.id
                )
            ORDER BY exception_count DESC, datetime(r.created_at) DESC, r.id DESC
            LIMIT ?
            """,
            (tenant_id, tenant_id, safe_limit),
        ).fetchall()

    return [dict(row) for row in rows]


def get_recent_gst_reconciliation_runs(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id,
                c.name AS client_name,
                r.created_at,
                r.total_books_invoices,
                r.total_2b_invoices,
                r.matched_count,
                r.missing_in_2b_count,
                r.missing_in_books_count,
                r.amount_mismatch_count,
                r.tax_mismatch_count,
                t.id AS linked_task_id,
                t.status AS linked_task_status,
                n.status AS note_status
            FROM gst_reconciliation_runs r
            JOIN client_entities c
              ON c.id = r.client_entity_id
             AND c.tenant_id = r.tenant_id
            LEFT JOIN gst_reconciliation_task_links l
              ON l.id = (
                    SELECT MAX(l2.id)
                    FROM gst_reconciliation_task_links l2
                    WHERE l2.tenant_id = r.tenant_id
                      AND l2.reconciliation_run_id = r.id
                )
            LEFT JOIN compliance_tasks t
              ON t.id = l.task_id
             AND t.tenant_id = r.tenant_id
            LEFT JOIN gst_reconciliation_notes n
              ON n.id = (
                    SELECT MAX(n2.id)
                    FROM gst_reconciliation_notes n2
                    WHERE n2.tenant_id = r.tenant_id
                      AND n2.reconciliation_run_id = r.id
                )
            WHERE r.tenant_id = ?
            ORDER BY datetime(r.created_at) DESC, r.id DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()

    return [dict(row) for row in rows]


def get_unresolved_gst_exceptions(tenant_id, limit=25, filters=None):
    filters = filters or {}
    safe_limit = max(1, min(int(limit or 25), 200))

    where_parts = ["res.tenant_id = ?", "res.match_status <> 'matched'"]
    params = [tenant_id]

    client_entity_id = _clean_text(filters.get("client_entity_id"))
    if client_entity_id:
        where_parts.append("res.client_entity_id = ?")
        params.append(client_entity_id)

    match_status = _clean_text(filters.get("match_status"))
    if match_status:
        where_parts.append("res.match_status = ?")
        params.append(match_status)

    supplier_gstin = _clean_text(filters.get("supplier_gstin")).upper()
    if supplier_gstin:
        where_parts.append("UPPER(COALESCE(res.supplier_gstin, '')) = ?")
        params.append(supplier_gstin)

    search = _clean_text(filters.get("search"))
    if search:
        like = f"%{search}%"
        where_parts.append("(c.name LIKE ? OR res.supplier_name LIKE ? OR res.invoice_number LIKE ? OR res.remarks LIKE ?)")
        params.extend([like, like, like, like])

    where_sql = " AND ".join(where_parts)
    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                res.id,
                res.reconciliation_run_id AS run_id,
                c.name AS client_name,
                res.match_status,
                res.supplier_gstin,
                res.supplier_name,
                res.invoice_number,
                res.books_total,
                res.gstr2b_total,
                res.difference_total,
                res.remarks,
                res.created_at
            FROM gst_reconciliation_results res
            JOIN gst_reconciliation_runs r
              ON r.id = res.reconciliation_run_id
             AND r.tenant_id = res.tenant_id
            JOIN client_entities c
              ON c.id = res.client_entity_id
             AND c.tenant_id = res.tenant_id
            WHERE {where_sql}
            ORDER BY datetime(res.created_at) DESC, res.id DESC
            LIMIT ?
            """,
            tuple(params + [safe_limit]),
        ).fetchall()

    return [dict(row) for row in rows]


def get_pending_gst_review_tasks(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                t.id,
                c.name AS client_name,
                t.title,
                t.status,
                t.pending_from,
                t.due_date,
                t.priority,
                COUNT(l.id) AS linked_reconciliation_count
            FROM compliance_tasks t
            JOIN client_entities c
              ON c.id = t.client_entity_id
             AND c.tenant_id = t.tenant_id
            LEFT JOIN gst_reconciliation_task_links l
              ON l.tenant_id = t.tenant_id
             AND l.task_id = t.id
            WHERE t.tenant_id = ?
              AND t.task_type IN ('gstr1', 'gstr3b', 'gstr9', 'document_checklist', 'general_query')
              AND t.status IN ('under_review', 'pending_documents', 'changes_required', 'ai_draft_ready')
            GROUP BY t.id, c.name, t.title, t.status, t.pending_from, t.due_date, t.priority
            ORDER BY datetime(t.created_at) DESC, t.id DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()

    return [dict(row) for row in rows]


def get_pending_gst_document_requests(tenant_id, limit=10):
    safe_limit = max(1, min(int(limit or 10), 100))
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                dr.id,
                dr.task_id,
                c.name AS client_name,
                dr.document_name,
                dr.status,
                dr.created_at,
                t.title AS task_title
            FROM document_requests dr
            JOIN compliance_tasks t
              ON t.id = dr.task_id
             AND t.tenant_id = dr.tenant_id
            JOIN client_entities c
              ON c.id = dr.client_entity_id
             AND c.tenant_id = dr.tenant_id
            WHERE dr.tenant_id = ?
              AND dr.status = 'requested'
              AND t.task_type IN ('gstr1', 'gstr3b', 'gstr9', 'document_checklist', 'general_query')
            ORDER BY datetime(dr.created_at) DESC, dr.id DESC
            LIMIT ?
            """,
            (tenant_id, safe_limit),
        ).fetchall()

    return [dict(row) for row in rows]

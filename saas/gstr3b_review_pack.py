"""
GSTR-3B Review Pack Builder
=============================
Aggregates GST reconciliation data, working notes, sales/purchase summaries,
and pending documents into a review-only pack for CA/staff review.

IMPORTANT: This pack is review-only. It does not:
- Compute final ITC claim
- File GSTR-3B
- Modify vouchers
- Call Paperclip/AI

Statuses:
  draft        = newly created
  under_review = being reviewed
  approved     = review approved (does NOT mean filing approved)
  archived     = no longer active
"""

import json
from datetime import datetime, timezone

import db
import gst_reconciliation
import gst_working_note
import compliance_tasks
import document_workflow

PACK_STATUSES = {"draft", "under_review", "approved", "archived"}


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _json_dumps(value):
    return json.dumps(value, ensure_ascii=False)


# =============================================================================
# 1. get_latest_review_pack_for_run
# =============================================================================

def get_latest_review_pack_for_run(tenant_id, reconciliation_run_id):
    """
    Tenant-safe.
    Returns latest review pack for a reconciliation run, or None if no packs exist.
    """
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gstr3b_review_packs
            WHERE tenant_id = ? AND reconciliation_run_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 1
            """,
            (tenant_id, reconciliation_run_id),
        ).fetchone()

    return dict(row) if row else None


# =============================================================================
# 2. list_review_packs_for_run
# =============================================================================

def list_review_packs_for_run(tenant_id, reconciliation_run_id):
    """
    Tenant-safe.
    Returns all review packs for a reconciliation run, newest first.
    """
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM gstr3b_review_packs
            WHERE tenant_id = ? AND reconciliation_run_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (tenant_id, reconciliation_run_id),
        ).fetchall()

    return [dict(row) for row in rows]


# =============================================================================
# 3. summarize_sales_for_client
# =============================================================================

def summarize_sales_for_client(tenant_id, client_entity_id, connection_id=None):
    """
    Summarize sales invoices from accounting_vouchers + accounting_invoice_lines.

    Returns:
      invoice_count: int
      taxable_value_total: float
      igst_total: float
      cgst_total: float
      sgst_total: float
      total_tax: float
      invoice_total: float
    """
    params = [tenant_id, client_entity_id, "sales_invoice"]
    connection_sql = ""
    if connection_id:
        connection_sql = " AND v.connection_id = ?"
        params.append(connection_id)

    with db.get_db() as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT v.id) AS invoice_count,
                COALESCE(SUM(il.taxable_value), 0) AS taxable_value_total,
                COALESCE(SUM(il.igst), 0) AS igst_total,
                COALESCE(SUM(il.cgst), 0) AS cgst_total,
                COALESCE(SUM(il.sgst), 0) AS sgst_total,
                COALESCE(SUM(il.igst + il.cgst + il.sgst), 0) AS total_tax,
                COALESCE(SUM(il.total), 0) AS invoice_total
            FROM accounting_vouchers v
            LEFT JOIN accounting_invoice_lines il
              ON il.voucher_id = v.id
             AND il.tenant_id = v.tenant_id
            WHERE v.tenant_id = ?
              AND v.client_entity_id = ?
              AND v.voucher_type = ?
              {connection_sql}
            """,
            tuple(params),
        ).fetchone()

    if not row:
        return {
            "invoice_count": 0,
            "taxable_value_total": 0.0,
            "igst_total": 0.0,
            "cgst_total": 0.0,
            "sgst_total": 0.0,
            "total_tax": 0.0,
            "invoice_total": 0.0,
        }

    return {
        "invoice_count": _safe_int(row["invoice_count"]),
        "taxable_value_total": round(_safe_float(row["taxable_value_total"]), 2),
        "igst_total": round(_safe_float(row["igst_total"]), 2),
        "cgst_total": round(_safe_float(row["cgst_total"]), 2),
        "sgst_total": round(_safe_float(row["sgst_total"]), 2),
        "total_tax": round(_safe_float(row["total_tax"]), 2),
        "invoice_total": round(_safe_float(row["invoice_total"]), 2),
    }


# =============================================================================
# 4. summarize_purchase_for_client
# =============================================================================

def summarize_purchase_for_client(tenant_id, client_entity_id, connection_id=None):
    """
    Summarize purchase invoices from accounting_vouchers + accounting_invoice_lines.

    Returns:
      invoice_count: int
      taxable_value_total: float
      igst_total: float
      cgst_total: float
      sgst_total: float
      total_tax: float
      invoice_total: float
    """
    params = [tenant_id, client_entity_id, "purchase_invoice"]
    connection_sql = ""
    if connection_id:
        connection_sql = " AND v.connection_id = ?"
        params.append(connection_id)

    with db.get_db() as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT v.id) AS invoice_count,
                COALESCE(SUM(il.taxable_value), 0) AS taxable_value_total,
                COALESCE(SUM(il.igst), 0) AS igst_total,
                COALESCE(SUM(il.cgst), 0) AS cgst_total,
                COALESCE(SUM(il.sgst), 0) AS sgst_total,
                COALESCE(SUM(il.igst + il.cgst + il.sgst), 0) AS total_tax,
                COALESCE(SUM(il.total), 0) AS invoice_total
            FROM accounting_vouchers v
            LEFT JOIN accounting_invoice_lines il
              ON il.voucher_id = v.id
             AND il.tenant_id = v.tenant_id
            WHERE v.tenant_id = ?
              AND v.client_entity_id = ?
              AND v.voucher_type = ?
              {connection_sql}
            """,
            tuple(params),
        ).fetchone()

    if not row:
        return {
            "invoice_count": 0,
            "taxable_value_total": 0.0,
            "igst_total": 0.0,
            "cgst_total": 0.0,
            "sgst_total": 0.0,
            "total_tax": 0.0,
            "invoice_total": 0.0,
        }

    return {
        "invoice_count": _safe_int(row["invoice_count"]),
        "taxable_value_total": round(_safe_float(row["taxable_value_total"]), 2),
        "igst_total": round(_safe_float(row["igst_total"]), 2),
        "cgst_total": round(_safe_float(row["cgst_total"]), 2),
        "sgst_total": round(_safe_float(row["sgst_total"]), 2),
        "total_tax": round(_safe_float(row["total_tax"]), 2),
        "invoice_total": round(_safe_float(row["invoice_total"]), 2),
    }


# =============================================================================
# 5. summarize_pending_documents_for_task
# =============================================================================

def summarize_pending_documents_for_task(tenant_id, task_id):
    """
    Return list of requested documents for a task.

    Returns list of dicts:
      document_name: str
      description: str
      status: str
      created_at: str
    """
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                document_name,
                description,
                status,
                created_at
            FROM document_requests
            WHERE tenant_id = ? AND task_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (tenant_id, task_id),
        ).fetchall()

    return [dict(row) for row in rows]


# =============================================================================
# 6. build_gstr3b_review_checklist
# =============================================================================

def build_gstr3b_review_checklist(context):
    """
    Build review checklist items based on context.

    context dict may include:
      - reconciliation_summary
      - risk_flags
      - pending_documents
      - etc.

    Returns list of checklist item strings.
    """
    checklist = [
        "Verify outward supplies from books/sales register.",
        "Verify purchase register vs GSTR-2B mismatches.",
        "Verify missing in 2B invoices.",
        "Verify missing in books invoices.",
        "Verify amount/tax mismatches.",
        "Verify RCM applicability separately.",
        "Verify ineligible ITC separately.",
        "Verify credit notes/debit notes if applicable.",
        "Verify cash ledger/challan separately.",
        "Confirm CA review before filing.",
    ]
    return checklist


# =============================================================================
# 7. build_risk_flags
# =============================================================================

def build_risk_flags(context):
    """
    Build risk flags based on context.

    context dict includes:
      - reconciliation_summary
      - pending_documents
      - working_note (if present)

    Returns list of risk flag strings.
    """
    flags = []

    recon_summary = context.get("reconciliation_summary", {})
    if _safe_int(recon_summary.get("missing_in_2b_count")) > 0:
        flags.append("Missing in GSTR-2B")
    if _safe_int(recon_summary.get("missing_in_books_count")) > 0:
        flags.append("Missing in books")
    if _safe_int(recon_summary.get("amount_mismatch_count")) > 0:
        flags.append("Amount mismatch")
    if _safe_int(recon_summary.get("tax_mismatch_count")) > 0:
        flags.append("Tax mismatch")

    pending_docs = context.get("pending_documents", [])
    if pending_docs and len(pending_docs) > 0:
        flags.append("Pending document requests")

    working_note = context.get("working_note")
    if not working_note:
        flags.append("No working note available")

    linked_task = context.get("linked_task")
    if not linked_task:
        flags.append("Not linked to review task")

    return flags


# =============================================================================
# 8. build_pack_markdown
# =============================================================================

def build_pack_markdown(context):
    """
    Build review pack markdown output.

    context dict includes:
      - run
      - client
      - period
      - sales_summary
      - purchase_summary
      - reconciliation_summary
      - risk_flags
      - checklist
      - pending_documents
      - working_note (optional)

    Returns markdown string with mandatory limitation text.
    """
    run = context.get("run", {})
    client = context.get("client", {})
    period = context.get("period", "N/A")
    sales_summary = context.get("sales_summary", {})
    purchase_summary = context.get("purchase_summary", {})
    recon_summary = context.get("reconciliation_summary", {})
    risk_flags = context.get("risk_flags", [])
    checklist = context.get("checklist", [])
    pending_docs = context.get("pending_documents", [])
    working_note = context.get("working_note")

    lines = []
    lines.append("# GSTR-3B Review Pack")
    lines.append("")
    lines.append(f"**Client**: {client.get('name', 'Unknown Client')}")
    lines.append(f"**Period**: {period}")
    lines.append(f"**Generated**: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # Data Sources
    lines.append("## Data Sources")
    lines.append("- Purchase Register (Accounting Books)")
    lines.append("- GSTR-2B (GST Portal)")
    lines.append("- GST Reconciliation Run")
    if working_note:
        lines.append("- GST Working Note")
    lines.append("")

    # Sales Summary
    lines.append("## Sales Summary")
    lines.append(f"- Invoice Count: {sales_summary.get('invoice_count', 0)}")
    lines.append(f"- Taxable Value: ₹ {sales_summary.get('taxable_value_total', 0):.2f}")
    lines.append(f"- IGST: ₹ {sales_summary.get('igst_total', 0):.2f}")
    lines.append(f"- CGST: ₹ {sales_summary.get('cgst_total', 0):.2f}")
    lines.append(f"- SGST: ₹ {sales_summary.get('sgst_total', 0):.2f}")
    lines.append(f"- Total Tax: ₹ {sales_summary.get('total_tax', 0):.2f}")
    lines.append(f"- Total Amount: ₹ {sales_summary.get('invoice_total', 0):.2f}")
    lines.append("")

    # Purchase Summary
    lines.append("## Purchase Summary")
    lines.append(f"- Invoice Count: {purchase_summary.get('invoice_count', 0)}")
    lines.append(f"- Taxable Value: ₹ {purchase_summary.get('taxable_value_total', 0):.2f}")
    lines.append(f"- IGST: ₹ {purchase_summary.get('igst_total', 0):.2f}")
    lines.append(f"- CGST: ₹ {purchase_summary.get('cgst_total', 0):.2f}")
    lines.append(f"- SGST: ₹ {purchase_summary.get('sgst_total', 0):.2f}")
    lines.append(f"- Total Tax: ₹ {purchase_summary.get('total_tax', 0):.2f}")
    lines.append(f"- Total Amount: ₹ {purchase_summary.get('invoice_total', 0):.2f}")
    lines.append("")

    # Reconciliation Summary
    lines.append("## Reconciliation Summary")
    lines.append(f"- Books Invoices: {recon_summary.get('total_books_invoices', 0)}")
    lines.append(f"- GSTR-2B Invoices: {recon_summary.get('total_2b_invoices', 0)}")
    lines.append(f"- Matched: {recon_summary.get('matched_count', 0)}")
    lines.append(f"- Missing in 2B: {recon_summary.get('missing_in_2b_count', 0)}")
    lines.append(f"- Missing in Books: {recon_summary.get('missing_in_books_count', 0)}")
    lines.append(f"- Amount Mismatch: {recon_summary.get('amount_mismatch_count', 0)}")
    lines.append(f"- Tax Mismatch: {recon_summary.get('tax_mismatch_count', 0)}")
    lines.append("")

    # Pending Documents
    if pending_docs:
        lines.append("## Pending Documents")
        for doc in pending_docs:
            lines.append(f"- {doc.get('document_name', 'Unnamed')} ({doc.get('status', 'unknown')})")
        lines.append("")

    # Risk Flags
    if risk_flags:
        lines.append("## Risk Flags")
        for flag in risk_flags:
            lines.append(f"⚠️ {flag}")
        lines.append("")

    # Review Checklist
    if checklist:
        lines.append("## Review Checklist")
        for item in checklist:
            lines.append(f"☐ {item}")
        lines.append("")

    # CA Review Required
    lines.append("## CA Review Required")
    lines.append(
        "This review pack must be reviewed and approved by the Chartered Accountant "
        "before proceeding with GSTR-3B filing."
    )
    lines.append("")

    # Limitations
    lines.append("## Limitations")
    lines.append(
        "**This review pack is not a GST return and does not constitute filing approval. "
        "CA review and portal verification are required before GSTR-3B filing.**"
    )

    return "\n".join(lines)


# =============================================================================
# 9. create_review_pack_for_reconciliation
# =============================================================================

def create_review_pack_for_reconciliation(
    tenant_id,
    reconciliation_run_id,
    period=None,
    user_id=None,
    ip_address=None,
):
    """
    Create a review pack for a GST reconciliation run.

    Steps:
      1. Load reconciliation run
      2. Get linked task (if any)
      3. Get latest working note (if any)
      4. Summarize sales for client
      5. Summarize purchase for client
      6. Summarize pending documents from linked task
      7. Load reconciliation summary
      8. Generate risk flags and checklist
      9. Build markdown
      10. Insert gstr3b_review_packs row
      11. Log audit action

    Returns: created pack row (dict)
    """
    with db.get_db() as conn:
        # Load run
        run = gst_reconciliation.get_reconciliation_run(tenant_id, reconciliation_run_id)
        if not run:
            raise ValueError("GST reconciliation run not found.")

        client_entity_id = run.get("client_entity_id")

        # Get linked task
        linked_task = gst_reconciliation.get_linked_task_for_reconciliation(tenant_id, reconciliation_run_id)

        # Get latest working note
        working_note = gst_working_note.get_latest_working_note_for_run(tenant_id, reconciliation_run_id)

        # Summarize sales and purchase
        sales_summary = summarize_sales_for_client(tenant_id, client_entity_id)
        purchase_summary = summarize_purchase_for_client(tenant_id, client_entity_id)

        # Summarize pending documents if task exists
        pending_documents = []
        if linked_task:
            pending_documents = summarize_pending_documents_for_task(tenant_id, linked_task["task_id"])

        # Reconciliation summary (load from run + results)
        recon_results = gst_reconciliation.list_reconciliation_results(tenant_id, reconciliation_run_id, filters=None)
        reconciliation_summary = {
            "total_books_invoices": _safe_int(run.get("total_books_invoices")),
            "total_2b_invoices": _safe_int(run.get("total_2b_invoices")),
            "matched_count": _safe_int(run.get("matched_count")),
            "missing_in_2b_count": _safe_int(run.get("missing_in_2b_count")),
            "missing_in_books_count": _safe_int(run.get("missing_in_books_count")),
            "amount_mismatch_count": _safe_int(run.get("amount_mismatch_count")),
            "tax_mismatch_count": _safe_int(run.get("tax_mismatch_count")),
        }

        # Build context
        context = {
            "run": run,
            "client": {"name": run.get("client_name", "Unknown Client")},
            "period": period or "Unknown Period",
            "sales_summary": sales_summary,
            "purchase_summary": purchase_summary,
            "reconciliation_summary": reconciliation_summary,
            "pending_documents": pending_documents,
            "working_note": working_note,
            "linked_task": linked_task,
        }

        # Build risk flags and checklist
        risk_flags = build_risk_flags(context)
        checklist = build_gstr3b_review_checklist(context)

        # Build markdown
        pack_markdown = build_pack_markdown(context)

        # Insert pack row
        cur = conn.execute(
            """
            INSERT INTO gstr3b_review_packs (
                tenant_id,
                client_entity_id,
                reconciliation_run_id,
                linked_task_id,
                note_id,
                status,
                period,
                sales_summary_json,
                purchase_summary_json,
                reconciliation_summary_json,
                pending_documents_json,
                risk_flags_json,
                review_checklist_json,
                pack_markdown,
                created_by
            ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                client_entity_id,
                reconciliation_run_id,
                linked_task["task_id"] if linked_task else None,
                working_note["id"] if working_note else None,
                period,
                _json_dumps(sales_summary),
                _json_dumps(purchase_summary),
                _json_dumps(reconciliation_summary),
                _json_dumps(pending_documents),
                _json_dumps(risk_flags),
                _json_dumps(checklist),
                pack_markdown,
                user_id,
            ),
        )
        pack_id = cur.lastrowid

        # Log audit action
        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gstr3b_review_pack_created",
            entity_type="gstr3b_review_pack",
            entity_id=pack_id,
            old_value=None,
            new_value={
                "reconciliation_run_id": reconciliation_run_id,
                "status": "draft",
                "period": period,
            },
            metadata={
                "client_entity_id": client_entity_id,
                "linked_task_id": linked_task["task_id"] if linked_task else None,
            },
            ip_address=ip_address,
        )

    # Fetch and return created pack
    pack = get_review_pack(tenant_id, pack_id)
    return pack


# =============================================================================
# 10. get_review_pack
# =============================================================================

def get_review_pack(tenant_id, pack_id):
    """
    Tenant-safe.
    Returns review pack row by ID, or None if not found.
    """
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gstr3b_review_packs
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, pack_id),
        ).fetchone()

    return dict(row) if row else None


# =============================================================================
# 11. update_review_pack_status
# =============================================================================

def update_review_pack_status(tenant_id, pack_id, status, user_id=None, ip_address=None):
    """
    Update review pack status.

    Allowed statuses:
      - draft
      - under_review
      - approved (review approved, NOT filing approved)
      - archived

    Logs audit action.
    Returns updated pack row (dict).
    """
    if status not in PACK_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(PACK_STATUSES))}")

    with db.get_db() as conn:
        pack = conn.execute(
            """
            SELECT *
            FROM gstr3b_review_packs
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, pack_id),
        ).fetchone()
        if not pack:
            raise ValueError("Review pack not found.")

        old_status = pack["status"]

        conn.execute(
            """
            UPDATE gstr3b_review_packs
            SET status = ?, updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (status, datetime.now(timezone.utc).isoformat(), tenant_id, pack_id),
        )

        # Log audit action
        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gstr3b_review_pack_status_changed",
            entity_type="gstr3b_review_pack",
            entity_id=pack_id,
            old_value={"status": old_status},
            new_value={"status": status},
            metadata={
                "reconciliation_run_id": pack["reconciliation_run_id"],
                "client_entity_id": pack["client_entity_id"],
            },
            ip_address=ip_address,
        )

    # Fetch and return updated pack
    return get_review_pack(tenant_id, pack_id)


# =============================================================================
# 12. list_review_packs
# =============================================================================

def list_review_packs(tenant_id, filters=None):
    """
    Tenant-safe.
    List all review packs with joined client, reconciliation run, and task info.

    Filters:
      - client_entity_id
      - status
      - period
      - search (searches client name, period, pack_markdown content, task title)

    Returns list of dicts with:
      - pack_id
      - client_entity_id
      - client_name
      - reconciliation_run_id
      - linked_task_id
      - linked_task_title
      - linked_task_status
      - note_id
      - status
      - period
      - created_at
      - updated_at
      - sales_taxable_total
      - purchase_taxable_total
      - exception_count
      - pending_document_count
      - risk_flag_count
    """
    if filters is None:
        filters = {}

    client_entity_id = (filters.get("client_entity_id") or "").strip()
    status = (filters.get("status") or "").strip()
    period = (filters.get("period") or "").strip()
    search = (filters.get("search") or "").strip().lower()

    where_clauses = ["p.tenant_id = ?"]
    params = [tenant_id]

    if client_entity_id:
        where_clauses.append("p.client_entity_id = ?")
        params.append(int(client_entity_id))

    if status:
        where_clauses.append("p.status = ?")
        params.append(status)

    if period:
        where_clauses.append("p.period LIKE ?")
        params.append(f"%{period}%")

    search_clause = ""
    if search:
        search_clause = (
            "AND (LOWER(c.name) LIKE ? "
            "OR LOWER(COALESCE(p.period, '')) LIKE ? "
            "OR LOWER(p.pack_markdown) LIKE ? "
            "OR LOWER(COALESCE(t.title, '')) LIKE ?)"
        )
        search_term = f"%{search}%"
        # Will be added to params after WHERE clause

    where_sql = " AND ".join(where_clauses)

    with db.get_db() as conn:
        base_query = f"""
            SELECT
                p.id AS pack_id,
                p.client_entity_id,
                c.name AS client_name,
                p.reconciliation_run_id,
                p.linked_task_id,
                COALESCE(t.title, '') AS linked_task_title,
                COALESCE(t.status, '') AS linked_task_status,
                p.note_id,
                p.status,
                p.period,
                p.created_at,
                p.updated_at,
                COALESCE(
                    (SELECT value FROM json_each(p.sales_summary_json) WHERE key = 'taxable_value_total'),
                    0
                ) AS sales_taxable_total,
                COALESCE(
                    (SELECT value FROM json_each(p.purchase_summary_json) WHERE key = 'taxable_value_total'),
                    0
                ) AS purchase_taxable_total,
                COALESCE(json_array_length(p.pending_documents_json), 0) AS pending_document_count,
                COALESCE(json_array_length(p.risk_flags_json), 0) AS risk_flag_count
            FROM gstr3b_review_packs p
            LEFT JOIN client_entities c
              ON c.id = p.client_entity_id
             AND c.tenant_id = p.tenant_id
            LEFT JOIN compliance_tasks t
              ON t.id = p.linked_task_id
             AND t.tenant_id = p.tenant_id
            WHERE {where_sql}
            {search_clause}
            ORDER BY datetime(p.created_at) DESC, p.id DESC
        """

        if search:
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term, search_term])

        rows = conn.execute(base_query, params).fetchall()

    result = []
    for row in rows:
        row_dict = dict(row)
        # Parse exception count from reconciliation summary
        try:
            recon_summary = json.loads(row_dict.get("reconciliation_summary_json") or "{}")
            exception_count = (
                _safe_int(recon_summary.get("missing_in_2b_count"))
                + _safe_int(recon_summary.get("missing_in_books_count"))
                + _safe_int(recon_summary.get("amount_mismatch_count"))
                + _safe_int(recon_summary.get("tax_mismatch_count"))
            )
            row_dict["exception_count"] = exception_count
        except (TypeError, ValueError):
            row_dict["exception_count"] = 0

        result.append(row_dict)

    return result


# =============================================================================
# 13. get_review_pack_register_summary
# =============================================================================

def get_review_pack_register_summary(tenant_id, filters=None):
    """
    Tenant-safe.
    Get summary statistics for the review pack register.

    Returns dict:
      - total_packs
      - draft_count
      - under_review_count
      - approved_count
      - archived_count
      - packs_this_month
      - high_risk_count
      - with_pending_documents_count
    """
    if filters is None:
        filters = {}

    client_entity_id = (filters.get("client_entity_id") or "").strip()
    status = (filters.get("status") or "").strip()
    period = (filters.get("period") or "").strip()

    where_clauses = ["tenant_id = ?"]
    params = [tenant_id]

    if client_entity_id:
        where_clauses.append("client_entity_id = ?")
        params.append(int(client_entity_id))

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if period:
        where_clauses.append("period LIKE ?")
        params.append(f"%{period}%")

    where_sql = " AND ".join(where_clauses)

    with db.get_db() as conn:
        # Total and by-status counts
        summary_query = f"""
            SELECT
                COUNT(*) AS total_packs,
                SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) AS draft_count,
                SUM(CASE WHEN status = 'under_review' THEN 1 ELSE 0 END) AS under_review_count,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) AS archived_count,
                SUM(CASE WHEN date(created_at) >= date('now', 'start of month') THEN 1 ELSE 0 END) AS packs_this_month,
                SUM(CASE WHEN risk_flags_json IS NOT NULL AND json_array_length(risk_flags_json) > 0 THEN 1 ELSE 0 END) AS high_risk_count,
                SUM(CASE WHEN pending_documents_json IS NOT NULL AND json_array_length(pending_documents_json) > 0 THEN 1 ELSE 0 END) AS with_pending_documents_count
            FROM gstr3b_review_packs
            WHERE {where_sql}
        """

        row = conn.execute(summary_query, params).fetchone()

    if not row:
        return {
            "total_packs": 0,
            "draft_count": 0,
            "under_review_count": 0,
            "approved_count": 0,
            "archived_count": 0,
            "packs_this_month": 0,
            "high_risk_count": 0,
            "with_pending_documents_count": 0,
        }

    return {
        "total_packs": _safe_int(row["total_packs"]),
        "draft_count": _safe_int(row["draft_count"]),
        "under_review_count": _safe_int(row["under_review_count"]),
        "approved_count": _safe_int(row["approved_count"]),
        "archived_count": _safe_int(row["archived_count"]),
        "packs_this_month": _safe_int(row["packs_this_month"]),
        "high_risk_count": _safe_int(row["high_risk_count"]),
        "with_pending_documents_count": _safe_int(row["with_pending_documents_count"]),
    }


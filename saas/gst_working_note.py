import json

import db
import gst_reconciliation
import usage

NOTE_STATUSES = {"draft", "under_review", "approved", "archived"}


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


def summarize_reconciliation_for_note(tenant_id, run_id):
    run = gst_reconciliation.get_reconciliation_run(tenant_id, run_id)
    if not run:
        raise ValueError("GST reconciliation run not found.")

    results = gst_reconciliation.list_reconciliation_results(tenant_id, run_id, filters=None)

    matched_count = _safe_int(run.get("matched_count"))
    missing_in_2b_count = _safe_int(run.get("missing_in_2b_count"))
    missing_in_books_count = _safe_int(run.get("missing_in_books_count"))
    amount_mismatch_count = _safe_int(run.get("amount_mismatch_count"))
    tax_mismatch_count = _safe_int(run.get("tax_mismatch_count"))

    total_books_invoices = _safe_int(run.get("total_books_invoices"))
    total_2b_invoices = _safe_int(run.get("total_2b_invoices"))

    missing_in_2b_rows = []
    missing_in_books_rows = []
    amount_mismatch_rows = []
    tax_mismatch_rows = []

    total_potential_difference = 0.0
    review_required_count = 0

    for row in results:
        status = (row.get("match_status") or "").strip().lower()

        if status == "missing_in_2b":
            missing_in_2b_rows.append(
                {
                    "supplier_gstin": row.get("supplier_gstin"),
                    "supplier_name": row.get("supplier_name"),
                    "invoice_number": row.get("invoice_number"),
                    "invoice_date": row.get("invoice_date_books"),
                    "amount": _safe_float(row.get("books_total")),
                    "remarks": row.get("remarks"),
                }
            )
            total_potential_difference += abs(_safe_float(row.get("books_total")))
        elif status == "missing_in_books":
            missing_in_books_rows.append(
                {
                    "supplier_gstin": row.get("supplier_gstin"),
                    "supplier_name": row.get("supplier_name"),
                    "invoice_number": row.get("invoice_number"),
                    "invoice_date": row.get("invoice_date_2b"),
                    "amount": _safe_float(row.get("gstr2b_total")),
                    "remarks": row.get("remarks"),
                }
            )
            total_potential_difference += abs(_safe_float(row.get("gstr2b_total")))
        elif status == "amount_mismatch":
            amount_mismatch_rows.append(
                {
                    "supplier_gstin": row.get("supplier_gstin"),
                    "supplier_name": row.get("supplier_name"),
                    "invoice_number": row.get("invoice_number"),
                    "books_total": _safe_float(row.get("books_total")),
                    "gstr2b_total": _safe_float(row.get("gstr2b_total")),
                    "difference_total": _safe_float(row.get("difference_total")),
                    "remarks": row.get("remarks"),
                }
            )
            total_potential_difference += abs(_safe_float(row.get("difference_total")))
        elif status == "tax_mismatch":
            books_tax = _safe_float(row.get("books_igst")) + _safe_float(row.get("books_cgst")) + _safe_float(row.get("books_sgst"))
            gstr_tax = _safe_float(row.get("gstr2b_igst")) + _safe_float(row.get("gstr2b_cgst")) + _safe_float(row.get("gstr2b_sgst"))
            tax_mismatch_rows.append(
                {
                    "supplier_gstin": row.get("supplier_gstin"),
                    "supplier_name": row.get("supplier_name"),
                    "invoice_number": row.get("invoice_number"),
                    "books_tax": books_tax,
                    "gstr2b_tax": gstr_tax,
                    "difference_tax": _safe_float(row.get("difference_tax")),
                    "remarks": row.get("remarks"),
                }
            )
            total_potential_difference += abs(_safe_float(row.get("difference_tax")))
        elif status in {"possible_duplicate", "review_required"}:
            review_required_count += 1

    exception_count = (
        missing_in_2b_count
        + missing_in_books_count
        + amount_mismatch_count
        + tax_mismatch_count
        + review_required_count
    )
    baseline = max(total_books_invoices, total_2b_invoices, 1)
    exception_ratio = exception_count / baseline

    if exception_count == 0:
        risk_level = "low"
    elif exception_count >= 25 or exception_ratio >= 0.25:
        risk_level = "high"
    elif exception_ratio <= 0.05 and matched_count >= max(1, int(0.9 * baseline)):
        risk_level = "low"
    else:
        risk_level = "medium"

    return {
        "client_name": run.get("client_name") or "Unknown Client",
        "client_entity_id": run.get("client_entity_id"),
        "run_id": run.get("id"),
        "total_books_invoices": total_books_invoices,
        "total_2b_invoices": total_2b_invoices,
        "matched_count": matched_count,
        "missing_in_2b_count": missing_in_2b_count,
        "missing_in_books_count": missing_in_books_count,
        "amount_mismatch_count": amount_mismatch_count,
        "tax_mismatch_count": tax_mismatch_count,
        "review_required_count": review_required_count,
        "top_missing_in_2b": missing_in_2b_rows[:10],
        "top_missing_in_books": missing_in_books_rows[:10],
        "top_amount_mismatches": amount_mismatch_rows[:10],
        "top_tax_mismatches": tax_mismatch_rows[:10],
        "total_potential_difference": round(total_potential_difference, 2),
        "risk_level": risk_level,
    }


def generate_document_requests(summary):
    requests = [
        "Supplier invoice copies for missing or mismatched invoices.",
        "Vendor GST confirmation for invoices missing in GSTR-2B.",
        "Books correction details for invoices missing in books or with differences.",
        "GSTR-2B supporting export for the same tax period used in this run.",
        "ITC eligibility confirmation from the client/CA review checklist.",
    ]

    if _safe_int(summary.get("missing_in_2b_count")) > 0:
        requests.append("Supplier follow-up proof for upload/filing lag impacting GSTR-2B visibility.")
    if _safe_int(summary.get("amount_mismatch_count")) + _safe_int(summary.get("tax_mismatch_count")) > 0:
        requests.append("Rate-wise and tax-component reconciliation sheet signed by preparer.")

    # Keep list stable and unique in insertion order.
    seen = set()
    deduped = []
    for item in requests:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def generate_risk_flags(summary):
    flags = []
    if _safe_int(summary.get("missing_in_2b_count")) > 0:
        flags.append("Missing in GSTR-2B")
    if _safe_int(summary.get("missing_in_books_count")) > 0:
        flags.append("Missing in books")
    if _safe_int(summary.get("amount_mismatch_count")) > 0:
        flags.append("Amount mismatch")
    if _safe_int(summary.get("tax_mismatch_count")) > 0:
        flags.append("Tax mismatch")

    exception_total = (
        _safe_int(summary.get("missing_in_2b_count"))
        + _safe_int(summary.get("missing_in_books_count"))
        + _safe_int(summary.get("amount_mismatch_count"))
        + _safe_int(summary.get("tax_mismatch_count"))
        + _safe_int(summary.get("review_required_count"))
    )
    if exception_total >= 25 or summary.get("risk_level") == "high":
        flags.append("High exception count")

    return flags


def _format_exception_rows(rows, amount_field):
    if not rows:
        return "- None"

    lines = []
    for row in rows[:10]:
        supplier = row.get("supplier_name") or "Unknown Supplier"
        gstin = row.get("supplier_gstin") or "N/A"
        invoice = row.get("invoice_number") or "N/A"
        amount = _safe_float(row.get(amount_field))
        lines.append(f"- {supplier} ({gstin}) | Invoice: {invoice} | Amount: {amount:.2f}")
    return "\n".join(lines)


def build_working_note_markdown(summary):
    missing_2b_md = _format_exception_rows(summary.get("top_missing_in_2b") or [], "amount")
    missing_books_md = _format_exception_rows(summary.get("top_missing_in_books") or [], "amount")

    amount_mismatch_rows = summary.get("top_amount_mismatches") or []
    if amount_mismatch_rows:
        amount_mismatch_md = "\n".join(
            [
                (
                    f"- {(row.get('supplier_name') or 'Unknown Supplier')} "
                    f"({row.get('supplier_gstin') or 'N/A'}) | Invoice: {row.get('invoice_number') or 'N/A'} | "
                    f"Books: {_safe_float(row.get('books_total')):.2f} | "
                    f"2B: {_safe_float(row.get('gstr2b_total')):.2f} | "
                    f"Diff: {_safe_float(row.get('difference_total')):.2f}"
                )
                for row in amount_mismatch_rows[:10]
            ]
        )
    else:
        amount_mismatch_md = "- None"

    tax_mismatch_rows = summary.get("top_tax_mismatches") or []
    if tax_mismatch_rows:
        tax_mismatch_md = "\n".join(
            [
                (
                    f"- {(row.get('supplier_name') or 'Unknown Supplier')} "
                    f"({row.get('supplier_gstin') or 'N/A'}) | Invoice: {row.get('invoice_number') or 'N/A'} | "
                    f"Books Tax: {_safe_float(row.get('books_tax')):.2f} | "
                    f"2B Tax: {_safe_float(row.get('gstr2b_tax')):.2f} | "
                    f"Diff: {_safe_float(row.get('difference_tax')):.2f}"
                )
                for row in tax_mismatch_rows[:10]
            ]
        )
    else:
        tax_mismatch_md = "- None"

    document_requests = generate_document_requests(summary)
    document_requests_md = "\n".join([f"- {item}" for item in document_requests])

    risk_flags = generate_risk_flags(summary)
    risk_flags_md = "\n".join([f"- {item}" for item in risk_flags]) if risk_flags else "- None"

    return "\n".join(
        [
            "# GST Reconciliation Working Note",
            "",
            "## Client",
            f"- Name: {summary.get('client_name')}",
            f"- Reconciliation Run ID: {summary.get('run_id')}",
            "",
            "## Scope",
            "- Purchase Register vs GSTR-2B reconciliation review for the selected run.",
            "- This note is for review support only and does not modify accounting data.",
            "",
            "## Data Used",
            f"- Books invoices considered: {_safe_int(summary.get('total_books_invoices'))}",
            f"- GSTR-2B invoices considered: {_safe_int(summary.get('total_2b_invoices'))}",
            "",
            "## Summary",
            f"- Matched: {_safe_int(summary.get('matched_count'))}",
            f"- Missing in GSTR-2B: {_safe_int(summary.get('missing_in_2b_count'))}",
            f"- Missing in books: {_safe_int(summary.get('missing_in_books_count'))}",
            f"- Amount mismatches: {_safe_int(summary.get('amount_mismatch_count'))}",
            f"- Tax mismatches: {_safe_int(summary.get('tax_mismatch_count'))}",
            f"- Review-required items: {_safe_int(summary.get('review_required_count'))}",
            f"- Potential gross difference: {_safe_float(summary.get('total_potential_difference')):.2f}",
            "",
            "## Key Exceptions",
            "- Refer to the exception sections below for sample top items.",
            "",
            "## Missing in GSTR-2B",
            missing_2b_md,
            "",
            "## Missing in Books",
            missing_books_md,
            "",
            "## Amount/Tax Mismatches",
            "### Amount mismatches",
            amount_mismatch_md,
            "",
            "### Tax mismatches",
            tax_mismatch_md,
            "",
            "## Suggested Follow-up Documents",
            document_requests_md,
            "",
            "## Risk Assessment",
            f"- Risk level: {(summary.get('risk_level') or 'medium').upper()}",
            risk_flags_md,
            "",
            "## CA Review Required",
            "- This is a draft working note generated from deterministic reconciliation outputs.",
            "- CA review is mandatory before any downstream action.",
            "",
            "## Limitations",
            "- No automatic ITC decision is made.",
            "- No GSTR-3B computation is performed.",
            "- No filing action is performed.",
            "- No voucher modification is performed.",
            "- No portal or RPA action is performed.",
            "- No client communication is sent automatically.",
        ]
    )


def create_working_note_for_run(tenant_id, run_id, user_id=None, ip_address=None):
    summary = summarize_reconciliation_for_note(tenant_id, run_id)
    risk_flags = generate_risk_flags(summary)
    document_requests = generate_document_requests(summary)
    working_note_markdown = build_working_note_markdown(summary)

    with db.get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO gst_reconciliation_notes (
                tenant_id,
                reconciliation_run_id,
                client_entity_id,
                note_type,
                status,
                confidence,
                summary_json,
                risk_flags_json,
                document_requests_json,
                working_note_markdown,
                created_by
            )
            VALUES (?, ?, ?, 'gst_reconciliation_working_note', 'draft', ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                run_id,
                summary.get("client_entity_id"),
                "medium",
                _json_dumps(summary),
                _json_dumps(risk_flags),
                _json_dumps(document_requests),
                working_note_markdown,
                user_id,
            ),
        )
        note_id = cur.lastrowid

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gst_reconciliation_working_note_created",
            entity_type="gst_reconciliation_note",
            entity_id=note_id,
            old_value=None,
            new_value={
                "reconciliation_run_id": run_id,
                "status": "draft",
                "risk_level": summary.get("risk_level"),
                "confidence": "medium",
            },
            metadata={"phase": "gst_reconciliation_working_note_phase_1"},
            ip_address=ip_address,
        )

    return get_working_note(tenant_id, note_id)


def get_working_note(tenant_id, note_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_notes
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, note_id),
        ).fetchone()

    return dict(row) if row else None


def get_latest_working_note_for_run(tenant_id, run_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_notes
            WHERE tenant_id = ? AND reconciliation_run_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 1
            """,
            (tenant_id, run_id),
        ).fetchone()

    return dict(row) if row else None


def list_working_notes_for_run(tenant_id, run_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_notes
            WHERE tenant_id = ? AND reconciliation_run_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (tenant_id, run_id),
        ).fetchall()

    return [dict(row) for row in rows]


def update_working_note_status(tenant_id, note_id, status, user_id=None, ip_address=None):
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in NOTE_STATUSES:
        raise ValueError("Invalid status. Use draft, under_review, approved, or archived.")

    with db.get_db() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_notes
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, note_id),
        ).fetchone()
        if not existing:
            return None

        old_status = existing["status"]
        conn.execute(
            """
            UPDATE gst_reconciliation_notes
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE tenant_id = ? AND id = ?
            """,
            (normalized_status, tenant_id, note_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gst_reconciliation_working_note_status_changed",
            entity_type="gst_reconciliation_note",
            entity_id=note_id,
            old_value={"status": old_status},
            new_value={"status": normalized_status},
            metadata={
                "reconciliation_run_id": existing["reconciliation_run_id"],
                "client_entity_id": existing["client_entity_id"],
            },
            ip_address=ip_address,
        )

    return get_working_note(tenant_id, note_id)


def create_document_requests_from_working_note(
    tenant_id,
    note_id,
    task_id=None,
    user_id=None,
    ip_address=None,
):
    def _parse_json_list(value):
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _is_high_risk(note_row):
        confidence = str(note_row.get("confidence") or "").strip().lower()
        if confidence == "high":
            return True

        risk_flags = _parse_json_list(note_row.get("risk_flags_json"))
        flags_lower = {str(item).strip().lower() for item in risk_flags if str(item).strip()}
        return "high exception count" in flags_lower

    def _normalize_request_item(item):
        if isinstance(item, str):
            name = item.strip()
            return (name, None) if name else (None, None)

        if isinstance(item, dict):
            name = (
                str(
                    item.get("document_name")
                    or item.get("name")
                    or item.get("title")
                    or ""
                )
                .strip()
            )
            description = str(item.get("description") or "").strip() or None
            return (name, description) if name else (None, None)

        return (None, None)

    with db.get_db() as conn:
        note = conn.execute(
            """
            SELECT
                n.*,
                r.id AS run_id,
                r.client_entity_id AS run_client_entity_id
            FROM gst_reconciliation_notes n
            JOIN gst_reconciliation_runs r
              ON r.id = n.reconciliation_run_id
             AND r.tenant_id = n.tenant_id
            WHERE n.tenant_id = ? AND n.id = ?
            LIMIT 1
            """,
            (tenant_id, note_id),
        ).fetchone()

        if not note:
            raise ValueError("GST working note not found.")

        suggested_items = _parse_json_list(note["document_requests_json"])
        if not suggested_items:
            raise ValueError("No suggested document requests are available in this working note.")

        resolved_task_id = None
        if task_id not in (None, ""):
            try:
                resolved_task_id = int(task_id)
            except (TypeError, ValueError):
                raise ValueError("Task ID must be a valid number.") from None

            task_row = conn.execute(
                """
                SELECT *
                FROM compliance_tasks
                WHERE tenant_id = ? AND id = ?
                LIMIT 1
                """,
                (tenant_id, resolved_task_id),
            ).fetchone()
            if not task_row:
                raise ValueError("Selected task was not found.")
            if int(task_row["client_entity_id"]) != int(note["client_entity_id"]):
                raise ValueError("Selected task does not belong to the same client as the working note.")

        if resolved_task_id is None:
            existing_task = conn.execute(
                """
                SELECT *
                FROM compliance_tasks
                WHERE tenant_id = ?
                  AND client_entity_id = ?
                  AND task_type IN ('gstr3b', 'gstr1', 'document_checklist', 'general_query')
                  AND status NOT IN ('closed', 'cancelled', 'filed')
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
                """,
                (tenant_id, note["client_entity_id"]),
            ).fetchone()

            if existing_task:
                resolved_task_id = int(existing_task["id"])
            else:
                priority = "high" if _is_high_risk(note) else "normal"
                created_task = conn.execute(
                    """
                    INSERT INTO compliance_tasks (
                        tenant_id,
                        client_entity_id,
                        task_type,
                        title,
                        description,
                        status,
                        priority,
                        pending_from,
                        created_by
                    ) VALUES (?, ?, 'document_checklist', ?, ?, 'pending_documents', ?, 'client', ?)
                    """,
                    (
                        tenant_id,
                        note["client_entity_id"],
                        "GST reconciliation document follow-up",
                        "Document requests generated from GST reconciliation working note.",
                        priority,
                        user_id,
                    ),
                )
                resolved_task_id = int(created_task.lastrowid)

                conn.execute(
                    """
                    INSERT INTO task_status_history
                        (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        resolved_task_id,
                        None,
                        "pending_documents",
                        user_id,
                        "Task auto-created from GST reconciliation working note",
                    ),
                )

        task_row = conn.execute(
            """
            SELECT *
            FROM compliance_tasks
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, resolved_task_id),
        ).fetchone()
        if not task_row:
            raise ValueError("Could not resolve task for document request creation.")

        created_count = 0
        skipped_count = 0

        for item in suggested_items:
            document_name, description = _normalize_request_item(item)
            if not document_name:
                skipped_count += 1
                continue

            duplicate = conn.execute(
                """
                SELECT id
                FROM document_requests
                WHERE tenant_id = ?
                  AND task_id = ?
                  AND LOWER(document_name) = LOWER(?)
                  AND status = 'requested'
                LIMIT 1
                """,
                (tenant_id, resolved_task_id, document_name),
            ).fetchone()
            if duplicate:
                skipped_count += 1
                continue

            conn.execute(
                """
                INSERT INTO document_requests (
                    tenant_id,
                    client_entity_id,
                    task_id,
                    document_name,
                    description,
                    requested_from,
                    status
                ) VALUES (?, ?, ?, ?, ?, 'client', 'requested')
                """,
                (
                    tenant_id,
                    note["client_entity_id"],
                    resolved_task_id,
                    document_name,
                    description,
                ),
            )
            created_count += 1

        if created_count > 0:
            usage.increment_document_request_usage(tenant_id=tenant_id, amount=created_count, conn=conn)

        task_status = str(task_row["status"] or "").strip().lower()
        if task_status not in {"closed", "cancelled", "filed"}:
            if task_status != "pending_documents" or (task_row["pending_from"] or "") != "client":
                conn.execute(
                    """
                    UPDATE compliance_tasks
                    SET status = 'pending_documents',
                        pending_from = 'client'
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (tenant_id, resolved_task_id),
                )
                db.touch_updated_at(conn, "compliance_tasks", resolved_task_id)

                conn.execute(
                    """
                    INSERT INTO task_status_history
                        (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
                    VALUES (?, ?, ?, 'pending_documents', ?, ?)
                    """,
                    (
                        tenant_id,
                        resolved_task_id,
                        task_row["status"],
                        user_id,
                        "Document requests created from GST reconciliation working note",
                    ),
                )

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (
                tenant_id,
                resolved_task_id,
                user_id,
                "Document requests created from GST reconciliation working note.",
            ),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gst_working_note_document_requests_created",
            entity_type="gst_reconciliation_note",
            entity_id=note_id,
            old_value=None,
            new_value={
                "task_id": resolved_task_id,
                "created_count": created_count,
                "skipped_count": skipped_count,
            },
            metadata={
                "reconciliation_run_id": note["reconciliation_run_id"],
                "client_entity_id": note["client_entity_id"],
            },
            ip_address=ip_address,
        )

    return {
        "task_id": resolved_task_id,
        "created_count": created_count,
        "skipped_count": skipped_count,
    }

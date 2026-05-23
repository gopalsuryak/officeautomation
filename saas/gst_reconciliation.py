import json
import re
from collections import defaultdict

import db
from manual_upload_importer import parse_amount

MATCH_STATUSES = {
    "matched",
    "missing_in_2b",
    "missing_in_books",
    "amount_mismatch",
    "tax_mismatch",
    "possible_duplicate",
    "review_required",
}


def _clean_text(value):
    return str(value or "").strip()


def normalize_invoice_number(value):
    text = _clean_text(value).upper()
    if not text:
        return ""
    text = re.sub(r"[\s\-/]+", "", text)
    return text


def normalize_gstin(value):
    return _clean_text(value).upper()


def build_match_key(gstin, invoice_number):
    return f"{normalize_gstin(gstin)}::{normalize_invoice_number(invoice_number)}"


def _json_load_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _to_float(value):
    return float(parse_amount(value))


def load_books_purchase_invoices(tenant_id, client_entity_id, connection_id=None):
    params = [tenant_id, client_entity_id]
    connection_sql = ""
    if connection_id:
        connection_sql = " AND v.connection_id = ?"
        params.append(connection_id)

    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                v.id AS voucher_id,
                v.connection_id,
                v.voucher_number,
                v.voucher_date,
                v.party_name,
                v.gstin,
                v.raw_json,
                COALESCE(SUM(il.taxable_value), 0) AS taxable_value,
                COALESCE(SUM(il.igst), 0) AS igst,
                COALESCE(SUM(il.cgst), 0) AS cgst,
                COALESCE(SUM(il.sgst), 0) AS sgst,
                COALESCE(SUM(il.total), v.amount, 0) AS total
            FROM accounting_vouchers v
            LEFT JOIN accounting_invoice_lines il
              ON il.voucher_id = v.id
             AND il.tenant_id = v.tenant_id
            WHERE v.tenant_id = ?
              AND v.client_entity_id = ?
              AND v.voucher_type = 'purchase_invoice'
              {connection_sql}
            GROUP BY
                v.id,
                v.connection_id,
                v.voucher_number,
                v.voucher_date,
                v.party_name,
                v.gstin,
                v.raw_json,
                v.amount
            ORDER BY v.id DESC
            """,
            tuple(params),
        ).fetchall()

    invoices = []
    for row in rows:
        item = dict(row)
        item["supplier_gstin"] = normalize_gstin(item.get("gstin"))
        item["invoice_number"] = _clean_text(item.get("voucher_number"))
        item["invoice_date"] = _clean_text(item.get("voucher_date"))
        item["supplier_name"] = _clean_text(item.get("party_name"))
        item["taxable_value"] = _to_float(item.get("taxable_value"))
        item["igst"] = _to_float(item.get("igst"))
        item["cgst"] = _to_float(item.get("cgst"))
        item["sgst"] = _to_float(item.get("sgst"))
        item["total"] = _to_float(item.get("total"))
        item["match_key"] = build_match_key(item["supplier_gstin"], item["invoice_number"])
        invoices.append(item)

    return invoices


def load_gstr2b_rows_from_preview(tenant_id, preview_id):
    with db.get_db() as conn:
        preview = conn.execute(
            """
            SELECT *
            FROM accounting_upload_previews
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, preview_id),
        ).fetchone()

    if not preview:
        raise ValueError("GSTR-2B preview not found.")

    upload_type = _clean_text(preview["upload_type"]).lower()
    if upload_type != "gstr2b":
        raise ValueError("Selected preview is not a GSTR-2B preview.")

    validation_status = _clean_text(preview["validation_status"]).lower()
    if validation_status != "valid":
        raise ValueError("Only valid GSTR-2B previews can be reconciled.")

    rows = _json_load_list(preview["preview_rows_json"])
    normalized = []
    for idx, raw_row in enumerate(rows, start=1):
        row = raw_row if isinstance(raw_row, dict) else {}
        item = {
            "row_uid": idx,
            "supplier_gstin": normalize_gstin(row.get("supplier_gstin")),
            "supplier_name": _clean_text(row.get("supplier_name")),
            "invoice_number": _clean_text(row.get("invoice_number")),
            "invoice_date": _clean_text(row.get("invoice_date")),
            "taxable_value": _to_float(row.get("taxable_value")),
            "igst": _to_float(row.get("igst")),
            "cgst": _to_float(row.get("cgst")),
            "sgst": _to_float(row.get("sgst")),
            "total": _to_float(row.get("total")),
            "raw_json": row,
        }
        item["match_key"] = build_match_key(item["supplier_gstin"], item["invoice_number"])
        normalized.append(item)

    return normalized


def compare_invoice_amounts(books, gstr2b, tolerance=1.0):
    books_taxable = _to_float(books.get("taxable_value"))
    books_tax = _to_float(books.get("igst")) + _to_float(books.get("cgst")) + _to_float(books.get("sgst"))
    books_total = _to_float(books.get("total"))

    gstr_taxable = _to_float(gstr2b.get("taxable_value"))
    gstr_tax = _to_float(gstr2b.get("igst")) + _to_float(gstr2b.get("cgst")) + _to_float(gstr2b.get("sgst"))
    gstr_total = _to_float(gstr2b.get("total"))

    diff_taxable = books_taxable - gstr_taxable
    diff_tax = books_tax - gstr_tax
    diff_total = books_total - gstr_total

    taxable_ok = abs(diff_taxable) <= tolerance
    tax_ok = abs(diff_tax) <= tolerance
    total_ok = abs(diff_total) <= tolerance

    if taxable_ok and tax_ok and total_ok:
        status = "matched"
        remarks = "Books and GSTR-2B values are within tolerance."
    elif not tax_ok and taxable_ok and total_ok:
        status = "tax_mismatch"
        remarks = "Tax values differ beyond tolerance."
    else:
        status = "amount_mismatch"
        remarks = "Taxable value and/or total differ beyond tolerance."

    return {
        "match_status": status,
        "difference_taxable_value": diff_taxable,
        "difference_tax": diff_tax,
        "difference_total": diff_total,
        "remarks": remarks,
    }


def _insert_result(conn, tenant_id, run_id, client_entity_id, data):
    conn.execute(
        """
        INSERT INTO gst_reconciliation_results (
            tenant_id,
            reconciliation_run_id,
            client_entity_id,
            match_status,
            supplier_gstin,
            supplier_name,
            invoice_number,
            invoice_date_books,
            invoice_date_2b,
            books_voucher_id,
            books_taxable_value,
            books_igst,
            books_cgst,
            books_sgst,
            books_total,
            gstr2b_taxable_value,
            gstr2b_igst,
            gstr2b_cgst,
            gstr2b_sgst,
            gstr2b_total,
            difference_taxable_value,
            difference_tax,
            difference_total,
            remarks,
            raw_books_json,
            raw_2b_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            run_id,
            client_entity_id,
            data.get("match_status") or "review_required",
            data.get("supplier_gstin"),
            data.get("supplier_name"),
            data.get("invoice_number"),
            data.get("invoice_date_books"),
            data.get("invoice_date_2b"),
            data.get("books_voucher_id"),
            _to_float(data.get("books_taxable_value")),
            _to_float(data.get("books_igst")),
            _to_float(data.get("books_cgst")),
            _to_float(data.get("books_sgst")),
            _to_float(data.get("books_total")),
            _to_float(data.get("gstr2b_taxable_value")),
            _to_float(data.get("gstr2b_igst")),
            _to_float(data.get("gstr2b_cgst")),
            _to_float(data.get("gstr2b_sgst")),
            _to_float(data.get("gstr2b_total")),
            _to_float(data.get("difference_taxable_value")),
            _to_float(data.get("difference_tax")),
            _to_float(data.get("difference_total")),
            data.get("remarks"),
            json.dumps(data.get("raw_books_json") or {}, ensure_ascii=False),
            json.dumps(data.get("raw_2b_json") or {}, ensure_ascii=False),
        ),
    )


def run_purchase_vs_2b_reconciliation(
    tenant_id,
    client_entity_id,
    gstr2b_preview_id,
    connection_id=None,
    user_id=None,
    ip_address=None,
):
    books_rows = load_books_purchase_invoices(tenant_id, client_entity_id, connection_id=connection_id)
    gstr2b_rows = load_gstr2b_rows_from_preview(tenant_id, gstr2b_preview_id)

    two_b_by_key = defaultdict(list)
    for row in gstr2b_rows:
        two_b_by_key[row["match_key"]].append(row)

    matched_two_b_row_uids = set()

    counts = {
        "matched": 0,
        "missing_in_2b": 0,
        "missing_in_books": 0,
        "amount_mismatch": 0,
        "tax_mismatch": 0,
    }

    with db.get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO gst_reconciliation_runs (
                tenant_id,
                client_entity_id,
                connection_id,
                gstr2b_preview_id,
                run_type,
                status,
                total_books_invoices,
                total_2b_invoices,
                matched_count,
                missing_in_2b_count,
                missing_in_books_count,
                amount_mismatch_count,
                tax_mismatch_count,
                created_by
            )
            VALUES (?, ?, ?, ?, 'purchase_vs_2b', 'completed', 0, 0, 0, 0, 0, 0, 0, ?)
            """,
            (tenant_id, client_entity_id, connection_id, gstr2b_preview_id, user_id),
        )
        run_id = cur.lastrowid

        for books in books_rows:
            key = books.get("match_key")
            default_payload = {
                "supplier_gstin": books.get("supplier_gstin"),
                "supplier_name": books.get("supplier_name"),
                "invoice_number": books.get("invoice_number"),
                "invoice_date_books": books.get("invoice_date"),
                "books_voucher_id": books.get("voucher_id"),
                "books_taxable_value": books.get("taxable_value"),
                "books_igst": books.get("igst"),
                "books_cgst": books.get("cgst"),
                "books_sgst": books.get("sgst"),
                "books_total": books.get("total"),
                "raw_books_json": books,
            }

            if not normalize_gstin(books.get("supplier_gstin")) or not normalize_invoice_number(books.get("invoice_number")):
                _insert_result(
                    conn,
                    tenant_id,
                    run_id,
                    client_entity_id,
                    {
                        **default_payload,
                        "match_status": "review_required",
                        "remarks": "Books invoice is missing supplier GSTIN or invoice number.",
                    },
                )
                continue

            candidates = two_b_by_key.get(key, [])
            if not candidates:
                counts["missing_in_2b"] += 1
                _insert_result(
                    conn,
                    tenant_id,
                    run_id,
                    client_entity_id,
                    {
                        **default_payload,
                        "match_status": "missing_in_2b",
                        "remarks": "Invoice present in books but not found in GSTR-2B preview.",
                    },
                )
                continue

            if len(candidates) > 1:
                _insert_result(
                    conn,
                    tenant_id,
                    run_id,
                    client_entity_id,
                    {
                        **default_payload,
                        "match_status": "possible_duplicate",
                        "remarks": "Multiple GSTR-2B rows found for the same GSTIN + invoice number.",
                        "raw_2b_json": candidates,
                    },
                )
                continue

            two_b = candidates[0]
            matched_two_b_row_uids.add(two_b["row_uid"])
            comparison = compare_invoice_amounts(books, two_b)
            status = comparison["match_status"]
            if status in counts:
                counts[status] += 1

            _insert_result(
                conn,
                tenant_id,
                run_id,
                client_entity_id,
                {
                    **default_payload,
                    "match_status": status,
                    "invoice_date_2b": two_b.get("invoice_date"),
                    "gstr2b_taxable_value": two_b.get("taxable_value"),
                    "gstr2b_igst": two_b.get("igst"),
                    "gstr2b_cgst": two_b.get("cgst"),
                    "gstr2b_sgst": two_b.get("sgst"),
                    "gstr2b_total": two_b.get("total"),
                    "difference_taxable_value": comparison["difference_taxable_value"],
                    "difference_tax": comparison["difference_tax"],
                    "difference_total": comparison["difference_total"],
                    "remarks": comparison["remarks"],
                    "raw_2b_json": two_b,
                },
            )

        for two_b in gstr2b_rows:
            if two_b["row_uid"] in matched_two_b_row_uids:
                continue

            status = "missing_in_books"
            remarks = "Invoice present in GSTR-2B preview but not found in books purchase invoices."
            counts["missing_in_books"] += 1

            _insert_result(
                conn,
                tenant_id,
                run_id,
                client_entity_id,
                {
                    "match_status": status,
                    "supplier_gstin": two_b.get("supplier_gstin"),
                    "supplier_name": two_b.get("supplier_name"),
                    "invoice_number": two_b.get("invoice_number"),
                    "invoice_date_2b": two_b.get("invoice_date"),
                    "gstr2b_taxable_value": two_b.get("taxable_value"),
                    "gstr2b_igst": two_b.get("igst"),
                    "gstr2b_cgst": two_b.get("cgst"),
                    "gstr2b_sgst": two_b.get("sgst"),
                    "gstr2b_total": two_b.get("total"),
                    "remarks": remarks,
                    "raw_2b_json": two_b,
                },
            )

        conn.execute(
            """
            UPDATE gst_reconciliation_runs
            SET total_books_invoices = ?,
                total_2b_invoices = ?,
                matched_count = ?,
                missing_in_2b_count = ?,
                missing_in_books_count = ?,
                amount_mismatch_count = ?,
                tax_mismatch_count = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                len(books_rows),
                len(gstr2b_rows),
                counts["matched"],
                counts["missing_in_2b"],
                counts["missing_in_books"],
                counts["amount_mismatch"],
                counts["tax_mismatch"],
                tenant_id,
                run_id,
            ),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gst_purchase_2b_reconciliation_run",
            entity_type="gst_reconciliation_run",
            entity_id=run_id,
            old_value=None,
            new_value={
                "client_entity_id": client_entity_id,
                "connection_id": connection_id,
                "gstr2b_preview_id": gstr2b_preview_id,
                "summary": {
                    "total_books_invoices": len(books_rows),
                    "total_2b_invoices": len(gstr2b_rows),
                    "matched_count": counts["matched"],
                    "missing_in_2b_count": counts["missing_in_2b"],
                    "missing_in_books_count": counts["missing_in_books"],
                    "amount_mismatch_count": counts["amount_mismatch"],
                    "tax_mismatch_count": counts["tax_mismatch"],
                },
            },
            metadata={"phase": "purchase_vs_2b_reconciliation_phase_1"},
            ip_address=ip_address,
        )

    return get_reconciliation_run(tenant_id, run_id)


def get_reconciliation_run(tenant_id, run_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT
                r.*,
                c.name AS client_name
            FROM gst_reconciliation_runs r
            JOIN client_entities c
              ON c.id = r.client_entity_id
             AND c.tenant_id = r.tenant_id
            WHERE r.tenant_id = ? AND r.id = ?
            LIMIT 1
            """,
            (tenant_id, run_id),
        ).fetchone()

    return dict(row) if row else None


def list_reconciliation_runs(tenant_id, filters=None):
    filters = filters or {}
    where_parts = ["r.tenant_id = ?"]
    params = [tenant_id]

    client_entity_id = _clean_text(filters.get("client_entity_id"))
    if client_entity_id:
        where_parts.append("r.client_entity_id = ?")
        params.append(client_entity_id)

    status = _clean_text(filters.get("status"))
    if status:
        where_parts.append("r.status = ?")
        params.append(status)

    search = _clean_text(filters.get("search"))
    if search:
        like = f"%{search}%"
        where_parts.append("(c.name LIKE ? OR CAST(r.id AS TEXT) LIKE ?)")
        params.extend([like, like])

    where_sql = " AND ".join(where_parts)
    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                r.*,
                c.name AS client_name
            FROM gst_reconciliation_runs r
            JOIN client_entities c
              ON c.id = r.client_entity_id
             AND c.tenant_id = r.tenant_id
            WHERE {where_sql}
            ORDER BY datetime(r.created_at) DESC, r.id DESC
            """,
            tuple(params),
        ).fetchall()

    return [dict(row) for row in rows]


def list_reconciliation_results(tenant_id, run_id, filters=None):
    filters = filters or {}
    where_parts = ["tenant_id = ?", "reconciliation_run_id = ?"]
    params = [tenant_id, run_id]

    match_status = _clean_text(filters.get("match_status"))
    if match_status:
        where_parts.append("match_status = ?")
        params.append(match_status)

    supplier_gstin = normalize_gstin(filters.get("supplier_gstin"))
    if supplier_gstin:
        where_parts.append("supplier_gstin = ?")
        params.append(supplier_gstin)

    search = _clean_text(filters.get("search"))
    if search:
        like = f"%{search}%"
        where_parts.append("(supplier_name LIKE ? OR invoice_number LIKE ? OR remarks LIKE ?)")
        params.extend([like, like, like])

    where_sql = " AND ".join(where_parts)
    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM gst_reconciliation_results
            WHERE {where_sql}
            ORDER BY id DESC
            """,
            tuple(params),
        ).fetchall()

    return [dict(row) for row in rows]


def list_valid_gstr2b_previews(tenant_id, client_entity_id=None):
    params = [tenant_id]
    client_sql = ""
    if client_entity_id:
        client_sql = " AND p.client_entity_id = ?"
        params.append(client_entity_id)

    with db.get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                p.id,
                p.client_entity_id,
                p.connection_id,
                p.uploaded_file_id,
                p.created_at,
                p.validation_status,
                f.original_filename,
                c.name AS client_name
            FROM accounting_upload_previews p
            JOIN accounting_uploaded_files f
              ON f.id = p.uploaded_file_id
             AND f.tenant_id = p.tenant_id
            JOIN client_entities c
              ON c.id = p.client_entity_id
             AND c.tenant_id = p.tenant_id
            WHERE p.tenant_id = ?
              AND p.upload_type = 'gstr2b'
              AND p.validation_status = 'valid'
              {client_sql}
            ORDER BY datetime(p.created_at) DESC, p.id DESC
            """,
            tuple(params),
        ).fetchall()

    return [dict(row) for row in rows]


def get_linked_task_for_reconciliation(tenant_id, run_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT
                l.id AS link_id,
                l.reconciliation_run_id,
                l.task_id,
                l.client_entity_id,
                l.link_type,
                l.created_at AS linked_at,
                t.title AS task_title,
                t.task_type,
                t.status AS task_status,
                t.pending_from,
                t.priority,
                t.created_at AS task_created_at
            FROM gst_reconciliation_task_links l
            JOIN compliance_tasks t
              ON t.id = l.task_id
             AND t.tenant_id = l.tenant_id
            WHERE l.tenant_id = ?
              AND l.reconciliation_run_id = ?
            ORDER BY datetime(l.created_at) DESC, l.id DESC
            LIMIT 1
            """,
            (tenant_id, run_id),
        ).fetchone()

    return dict(row) if row else None


def get_reconciliations_for_task(tenant_id, task_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                l.id AS link_id,
                l.link_type,
                l.created_at AS linked_at,
                r.id,
                r.client_entity_id,
                r.status,
                r.total_books_invoices,
                r.total_2b_invoices,
                r.matched_count,
                r.missing_in_2b_count,
                r.missing_in_books_count,
                r.amount_mismatch_count,
                r.tax_mismatch_count,
                r.created_at,
                c.name AS client_name
            FROM gst_reconciliation_task_links l
            JOIN gst_reconciliation_runs r
              ON r.id = l.reconciliation_run_id
             AND r.tenant_id = l.tenant_id
            JOIN client_entities c
              ON c.id = r.client_entity_id
             AND c.tenant_id = r.tenant_id
            WHERE l.tenant_id = ?
              AND l.task_id = ?
            ORDER BY datetime(l.created_at) DESC, l.id DESC
            """,
            (tenant_id, task_id),
        ).fetchall()

    return [dict(row) for row in rows]


def link_reconciliation_to_task(tenant_id, run_id, task_id, user_id=None, ip_address=None):
    with db.get_db() as conn:
        run = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_runs
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, run_id),
        ).fetchone()
        if not run:
            raise ValueError("GST reconciliation run not found.")

        task = conn.execute(
            """
            SELECT *
            FROM compliance_tasks
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, task_id),
        ).fetchone()
        if not task:
            raise ValueError("Compliance task not found.")

        if int(task["client_entity_id"]) != int(run["client_entity_id"]):
            raise ValueError("Task must belong to the same client as the reconciliation run.")

        existing = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_task_links
            WHERE tenant_id = ?
              AND reconciliation_run_id = ?
              AND task_id = ?
            LIMIT 1
            """,
            (tenant_id, run_id, task_id),
        ).fetchone()
        if existing:
            return dict(existing)

        cur = conn.execute(
            """
            INSERT INTO gst_reconciliation_task_links (
                tenant_id,
                reconciliation_run_id,
                task_id,
                client_entity_id,
                link_type,
                created_by
            ) VALUES (?, ?, ?, ?, 'gst_review', ?)
            """,
            (tenant_id, run_id, task_id, run["client_entity_id"], user_id),
        )
        link_id = cur.lastrowid

        conn.execute(
            """
            INSERT INTO task_comments (tenant_id, task_id, user_id, comment_type, body)
            VALUES (?, ?, ?, 'system', ?)
            """,
            (
                tenant_id,
                task_id,
                user_id,
                "GST reconciliation run linked to this task.",
            ),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gst_reconciliation_task_linked",
            entity_type="gst_reconciliation_task_link",
            entity_id=link_id,
            old_value=None,
            new_value={
                "reconciliation_run_id": run_id,
                "task_id": task_id,
                "client_entity_id": run["client_entity_id"],
            },
            metadata={"link_type": "gst_review"},
            ip_address=ip_address,
        )

        link_row = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_task_links
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, link_id),
        ).fetchone()

    return dict(link_row) if link_row else None


def create_or_link_review_task_for_reconciliation(
    tenant_id,
    run_id,
    task_id=None,
    user_id=None,
    ip_address=None,
):
    with db.get_db() as conn:
        run = conn.execute(
            """
            SELECT *
            FROM gst_reconciliation_runs
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, run_id),
        ).fetchone()
        if not run:
            raise ValueError("GST reconciliation run not found.")

        resolved_task_id = None
        created_new = False

        if task_id not in (None, ""):
            try:
                resolved_task_id = int(task_id)
            except (TypeError, ValueError):
                raise ValueError("Task ID must be valid.") from None

            selected_task = conn.execute(
                """
                SELECT *
                FROM compliance_tasks
                WHERE tenant_id = ? AND id = ?
                LIMIT 1
                """,
                (tenant_id, resolved_task_id),
            ).fetchone()
            if not selected_task:
                raise ValueError("Selected task not found.")
            if int(selected_task["client_entity_id"]) != int(run["client_entity_id"]):
                raise ValueError("Selected task does not belong to this reconciliation client.")
        else:
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
                (tenant_id, run["client_entity_id"]),
            ).fetchone()

            if existing_task:
                resolved_task_id = int(existing_task["id"])
            else:
                mismatch_count = (
                    int(run["missing_in_2b_count"] or 0)
                    + int(run["missing_in_books_count"] or 0)
                    + int(run["amount_mismatch_count"] or 0)
                    + int(run["tax_mismatch_count"] or 0)
                )
                priority = "high" if mismatch_count > 0 else "normal"

                cur = conn.execute(
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
                    ) VALUES (?, ?, 'gstr3b', ?, ?, 'under_review', ?, 'reviewer', ?)
                    """,
                    (
                        tenant_id,
                        run["client_entity_id"],
                        "GST reconciliation review",
                        "Review purchase register vs GSTR-2B reconciliation and working note.",
                        priority,
                        user_id,
                    ),
                )
                resolved_task_id = cur.lastrowid
                created_new = True

                conn.execute(
                    """
                    INSERT INTO task_status_history
                        (tenant_id, task_id, old_status, new_status, changed_by_user_id, reason)
                    VALUES (?, ?, ?, 'under_review', ?, ?)
                    """,
                    (
                        tenant_id,
                        resolved_task_id,
                        None,
                        user_id,
                        "Task created from GST reconciliation run",
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
                        "Task created from GST reconciliation run.",
                    ),
                )

    link_row = link_reconciliation_to_task(
        tenant_id=tenant_id,
        run_id=run_id,
        task_id=resolved_task_id,
        user_id=user_id,
        ip_address=ip_address,
    )

    with db.get_db() as conn:
        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="gst_reconciliation_review_task_created_or_linked",
            entity_type="gst_reconciliation_run",
            entity_id=run_id,
            old_value=None,
            new_value={
                "task_id": resolved_task_id,
                "created_new": created_new,
                "link_id": link_row["id"] if link_row else None,
            },
            metadata={"phase": "gst_reconciliation_review_task_integration"},
            ip_address=ip_address,
        )

    return {
        "task_id": resolved_task_id,
        "created_new": created_new,
        "link_id": link_row["id"] if link_row else None,
    }

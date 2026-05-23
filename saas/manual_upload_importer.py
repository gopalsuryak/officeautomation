import json
import re
from datetime import date, datetime, timezone

import db

IMPORTABLE_UPLOAD_TYPES = {
    "trial_balance",
    "ledger_dump",
    "sales_register",
    "purchase_register",
}

REQUIRED_COLUMNS_FOR_LEDGER_IMPORT = {
    "trial_balance": ["ledger_name", "closing_balance"],
    "ledger_dump": ["ledger_name", "group_name", "closing_balance"],
}

REQUIRED_COLUMNS_FOR_SALES_REGISTER_IMPORT = [
    "invoice_number",
    "invoice_date",
    "taxable_value",
    "gst_rate",
    "total",
]

REQUIRED_COLUMNS_FOR_PURCHASE_REGISTER_IMPORT = [
    "invoice_number",
    "invoice_date",
    "supplier_name",
    "taxable_value",
    "gst_rate",
    "total",
]


def _clean_text(value):
    return str(value or "").strip()


def parse_date_string(value):
    if value is None:
        return ""

    if isinstance(value, date):
        return value.isoformat()

    text = _clean_text(value)
    if not text:
        return ""

    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue

    return text


def get_first_value(row, keys, default=""):
    if not isinstance(row, dict):
        return default

    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value

    return default


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load_json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _load_preview_bundle(tenant_id, preview_id):
    with db.get_db() as conn:
        preview_bundle = conn.execute(
            """
            SELECT
                p.*,
                f.id AS uploaded_file_id,
                f.sync_run_id,
                f.status AS uploaded_status,
                c.id AS connection_id,
                c.provider
            FROM accounting_upload_previews p
            JOIN accounting_uploaded_files f
              ON f.id = p.uploaded_file_id
             AND f.tenant_id = p.tenant_id
            JOIN accounting_connections c
              ON c.id = p.connection_id
             AND c.tenant_id = p.tenant_id
            WHERE p.tenant_id = ? AND p.id = ?
            LIMIT 1
            """,
            (tenant_id, preview_id),
        ).fetchone()
    return preview_bundle


def parse_amount(value):
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    text = _clean_text(value)
    if not text:
        return 0.0

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    text = text.replace(",", "")
    text = re.sub(r"[^0-9.\-]+", "", text)

    if not text:
        return 0.0

    try:
        amount = float(text)
    except ValueError:
        return 0.0

    if negative:
        amount = -abs(amount)

    return amount


def import_ledgers_from_preview(tenant_id, preview_id, user_id=None, ip_address=None):
    preview_bundle = _load_preview_bundle(tenant_id, preview_id)
    if not preview_bundle:
        raise ValueError("Upload preview not found.")

    with db.get_db() as conn:

        if not preview_bundle:
            raise ValueError("Upload preview not found.")

        upload_type = _clean_text(preview_bundle["upload_type"]).lower()
        if upload_type not in {"trial_balance", "ledger_dump"}:
            raise ValueError("Import is available only for trial_balance and ledger_dump previews.")

        validation_status = _clean_text(preview_bundle["validation_status"]).lower()
        if validation_status != "valid":
            raise ValueError("Only previews marked valid can be imported.")

        detected_columns = _load_json_list(preview_bundle["detected_columns_json"])
        preview_rows = _load_json_list(preview_bundle["preview_rows_json"])
        required_columns = REQUIRED_COLUMNS_FOR_LEDGER_IMPORT.get(upload_type, [])
        missing_columns = [col for col in required_columns if col not in detected_columns]
        if missing_columns:
            raise ValueError(f"Preview is missing required columns: {', '.join(missing_columns)}")

        imported_count = 0
        skipped_count = 0

        # TODO: Phase 3 should dedupe/upsert by deterministic keys.
        # TODO: Phase 3 should import full source file rows, not preview-limited rows.
        for raw_row in preview_rows:
            row = raw_row if isinstance(raw_row, dict) else {}

            ledger_name = _clean_text(row.get("ledger_name"))
            if not ledger_name:
                skipped_count += 1
                continue

            group_name = _clean_text(row.get("group_name"))
            opening_balance = parse_amount(row.get("opening_balance"))
            closing_balance = parse_amount(row.get("closing_balance"))

            conn.execute(
                """
                INSERT INTO accounting_ledgers (
                    tenant_id,
                    client_entity_id,
                    connection_id,
                    provider,
                    external_id,
                    ledger_name,
                    group_name,
                    opening_balance,
                    closing_balance,
                    raw_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    preview_bundle["client_entity_id"],
                    preview_bundle["connection_id"],
                    preview_bundle["provider"],
                    ledger_name,
                    group_name or None,
                    opening_balance,
                    closing_balance,
                    json.dumps(row, ensure_ascii=False),
                    _now_iso(),
                    _now_iso(),
                ),
            )
            imported_count += 1

        if preview_bundle["sync_run_id"]:
            conn.execute(
                """
                UPDATE accounting_sync_runs
                SET records_synced = ?,
                    status = 'completed',
                    completed_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (imported_count, _now_iso(), tenant_id, preview_bundle["sync_run_id"]),
            )

        conn.execute(
            """
            UPDATE accounting_uploaded_files
            SET status = 'processed',
                validation_message = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                "Imported preview rows into accounting_ledgers. Full-file import comes later.",
                tenant_id,
                preview_bundle["uploaded_file_id"],
            ),
        )

        conn.execute(
            """
            UPDATE accounting_upload_previews
            SET validation_status = 'valid',
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (_now_iso(), tenant_id, preview_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_ledgers_imported",
            entity_type="accounting_upload_preview",
            entity_id=preview_id,
            old_value=None,
            new_value={
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "upload_type": upload_type,
            },
            metadata={
                "uploaded_file_id": preview_bundle["uploaded_file_id"],
                "connection_id": preview_bundle["connection_id"],
                "sync_run_id": preview_bundle["sync_run_id"],
                "warning": "Phase 2 imports preview rows only; full-file import comes later.",
            },
            ip_address=ip_address,
        )

    return {
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "upload_type": upload_type,
    }


def import_sales_register_from_preview(tenant_id, preview_id, user_id=None, ip_address=None):
    preview_bundle = _load_preview_bundle(tenant_id, preview_id)
    if not preview_bundle:
        raise ValueError("Upload preview not found.")

    upload_type = _clean_text(preview_bundle["upload_type"]).lower()
    if upload_type != "sales_register":
        raise ValueError("Import is available only for sales_register previews.")

    validation_status = _clean_text(preview_bundle["validation_status"]).lower()
    if validation_status != "valid":
        raise ValueError("Only previews marked valid can be imported.")

    detected_columns = _load_json_list(preview_bundle["detected_columns_json"])
    preview_rows = _load_json_list(preview_bundle["preview_rows_json"])
    missing_columns = [col for col in REQUIRED_COLUMNS_FOR_SALES_REGISTER_IMPORT if col not in detected_columns]
    if missing_columns:
        raise ValueError(f"Preview is missing required columns: {', '.join(missing_columns)}")

    imported_count = 0
    skipped_count = 0

    with db.get_db() as conn:
        # TODO: Full-file import and duplicate detection/upsert will come in a later phase.
        for raw_row in preview_rows:
            row = raw_row if isinstance(raw_row, dict) else {}

            invoice_number = _clean_text(get_first_value(row, ["invoice_number"], default=""))
            if not invoice_number:
                skipped_count += 1
                continue

            invoice_date = parse_date_string(get_first_value(row, ["invoice_date"], default=""))
            customer_name = _clean_text(get_first_value(row, ["customer_name", "party_name", "buyer_name"], default=""))
            gstin = _clean_text(get_first_value(row, ["gstin", "customer_gstin", "recipient_gstin"], default=""))
            narration = _clean_text(get_first_value(row, ["narration"], default="")) or "Imported from sales register preview"
            item_name = _clean_text(get_first_value(row, ["item_name"], default="")) or None
            hsn_sac = _clean_text(get_first_value(row, ["hsn_sac"], default="")) or None

            taxable_value = parse_amount(get_first_value(row, ["taxable_value"], default=""))
            gst_rate = parse_amount(get_first_value(row, ["gst_rate"], default=""))
            igst = parse_amount(get_first_value(row, ["igst"], default="0"))
            cgst = parse_amount(get_first_value(row, ["cgst"], default="0"))
            sgst = parse_amount(get_first_value(row, ["sgst"], default="0"))
            total = parse_amount(get_first_value(row, ["total"], default=""))

            cur = conn.execute(
                """
                INSERT INTO accounting_vouchers (
                    tenant_id,
                    client_entity_id,
                    connection_id,
                    provider,
                    external_id,
                    voucher_type,
                    voucher_number,
                    voucher_date,
                    party_name,
                    amount,
                    gstin,
                    narration,
                    raw_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, NULL, 'sales_invoice', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    preview_bundle["client_entity_id"],
                    preview_bundle["connection_id"],
                    preview_bundle["provider"],
                    invoice_number,
                    invoice_date or None,
                    customer_name or None,
                    total,
                    gstin or None,
                    narration,
                    json.dumps(row, ensure_ascii=False),
                    _now_iso(),
                    _now_iso(),
                ),
            )
            voucher_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO accounting_invoice_lines (
                    tenant_id,
                    client_entity_id,
                    voucher_id,
                    item_name,
                    hsn_sac,
                    taxable_value,
                    gst_rate,
                    igst,
                    cgst,
                    sgst,
                    total,
                    raw_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    preview_bundle["client_entity_id"],
                    voucher_id,
                    item_name,
                    hsn_sac,
                    taxable_value,
                    gst_rate,
                    igst,
                    cgst,
                    sgst,
                    total,
                    json.dumps(row, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            imported_count += 1

        if preview_bundle["sync_run_id"]:
            conn.execute(
                """
                UPDATE accounting_sync_runs
                SET records_synced = ?,
                    status = 'completed',
                    completed_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (imported_count, _now_iso(), tenant_id, preview_bundle["sync_run_id"]),
            )

        conn.execute(
            """
            UPDATE accounting_uploaded_files
            SET status = 'processed',
                validation_message = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                "Imported preview rows into accounting_vouchers and accounting_invoice_lines. Full-file import comes later.",
                tenant_id,
                preview_bundle["uploaded_file_id"],
            ),
        )

        conn.execute(
            """
            UPDATE accounting_upload_previews
            SET validation_status = 'valid',
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (_now_iso(), tenant_id, preview_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_sales_register_imported",
            entity_type="accounting_upload_preview",
            entity_id=preview_id,
            old_value=None,
            new_value={
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "upload_type": upload_type,
            },
            metadata={
                "uploaded_file_id": preview_bundle["uploaded_file_id"],
                "connection_id": preview_bundle["connection_id"],
                "sync_run_id": preview_bundle["sync_run_id"],
                "warning": "Phase 1 imports preview rows only; full-file import comes later.",
            },
            ip_address=ip_address,
        )

    return {
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "upload_type": upload_type,
    }


def import_purchase_register_from_preview(tenant_id, preview_id, user_id=None, ip_address=None):
    preview_bundle = _load_preview_bundle(tenant_id, preview_id)
    if not preview_bundle:
        raise ValueError("Upload preview not found.")

    upload_type = _clean_text(preview_bundle["upload_type"]).lower()
    if upload_type != "purchase_register":
        raise ValueError("Import is available only for purchase_register previews.")

    validation_status = _clean_text(preview_bundle["validation_status"]).lower()
    if validation_status != "valid":
        raise ValueError("Only previews marked valid can be imported.")

    detected_columns = _load_json_list(preview_bundle["detected_columns_json"])
    preview_rows = _load_json_list(preview_bundle["preview_rows_json"])
    missing_columns = [col for col in REQUIRED_COLUMNS_FOR_PURCHASE_REGISTER_IMPORT if col not in detected_columns]
    if missing_columns:
        raise ValueError(f"Preview is missing required columns: {', '.join(missing_columns)}")

    imported_count = 0
    skipped_count = 0

    with db.get_db() as conn:
        # TODO: Full-file import and duplicate detection/upsert will come in a later phase.
        for raw_row in preview_rows:
            row = raw_row if isinstance(raw_row, dict) else {}

            invoice_number = _clean_text(get_first_value(row, ["invoice_number"], default=""))
            if not invoice_number:
                skipped_count += 1
                continue

            invoice_date = parse_date_string(get_first_value(row, ["invoice_date"], default=""))
            supplier_name = _clean_text(
                get_first_value(row, ["supplier_name", "vendor_name", "party_name"], default="")
            )
            gstin = _clean_text(
                get_first_value(row, ["gstin", "supplier_gstin", "vendor_gstin"], default="")
            )
            narration = _clean_text(get_first_value(row, ["narration"], default="")) or "Imported from purchase register preview"
            item_name = _clean_text(get_first_value(row, ["item_name"], default="")) or None
            hsn_sac = _clean_text(get_first_value(row, ["hsn_sac"], default="")) or None

            taxable_value = parse_amount(get_first_value(row, ["taxable_value"], default=""))
            gst_rate = parse_amount(get_first_value(row, ["gst_rate"], default=""))
            igst = parse_amount(get_first_value(row, ["igst"], default="0"))
            cgst = parse_amount(get_first_value(row, ["cgst"], default="0"))
            sgst = parse_amount(get_first_value(row, ["sgst"], default="0"))
            total = parse_amount(get_first_value(row, ["total"], default=""))

            cur = conn.execute(
                """
                INSERT INTO accounting_vouchers (
                    tenant_id,
                    client_entity_id,
                    connection_id,
                    provider,
                    external_id,
                    voucher_type,
                    voucher_number,
                    voucher_date,
                    party_name,
                    amount,
                    gstin,
                    narration,
                    raw_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, NULL, 'purchase_invoice', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    preview_bundle["client_entity_id"],
                    preview_bundle["connection_id"],
                    preview_bundle["provider"],
                    invoice_number,
                    invoice_date or None,
                    supplier_name or None,
                    total,
                    gstin or None,
                    narration,
                    json.dumps(row, ensure_ascii=False),
                    _now_iso(),
                    _now_iso(),
                ),
            )
            voucher_id = cur.lastrowid

            conn.execute(
                """
                INSERT INTO accounting_invoice_lines (
                    tenant_id,
                    client_entity_id,
                    voucher_id,
                    item_name,
                    hsn_sac,
                    taxable_value,
                    gst_rate,
                    igst,
                    cgst,
                    sgst,
                    total,
                    raw_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    preview_bundle["client_entity_id"],
                    voucher_id,
                    item_name,
                    hsn_sac,
                    taxable_value,
                    gst_rate,
                    igst,
                    cgst,
                    sgst,
                    total,
                    json.dumps(row, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            imported_count += 1

        if preview_bundle["sync_run_id"]:
            conn.execute(
                """
                UPDATE accounting_sync_runs
                SET records_synced = ?,
                    status = 'completed',
                    completed_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (imported_count, _now_iso(), tenant_id, preview_bundle["sync_run_id"]),
            )

        conn.execute(
            """
            UPDATE accounting_uploaded_files
            SET status = 'processed',
                validation_message = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                "Imported preview rows into accounting_vouchers and accounting_invoice_lines. Full-file import comes later.",
                tenant_id,
                preview_bundle["uploaded_file_id"],
            ),
        )

        conn.execute(
            """
            UPDATE accounting_upload_previews
            SET validation_status = 'valid',
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (_now_iso(), tenant_id, preview_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_purchase_register_imported",
            entity_type="accounting_upload_preview",
            entity_id=preview_id,
            old_value=None,
            new_value={
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "upload_type": upload_type,
            },
            metadata={
                "uploaded_file_id": preview_bundle["uploaded_file_id"],
                "connection_id": preview_bundle["connection_id"],
                "sync_run_id": preview_bundle["sync_run_id"],
                "warning": "Phase 1 imports preview rows only; full-file import and duplicate detection will come later.",
            },
            ip_address=ip_address,
        )

    return {
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "upload_type": upload_type,
    }


def import_from_preview(tenant_id, preview_id, user_id=None, ip_address=None):
    preview_bundle = _load_preview_bundle(tenant_id, preview_id)
    if not preview_bundle:
        raise ValueError("Upload preview not found.")

    upload_type = _clean_text(preview_bundle["upload_type"]).lower()
    if upload_type in {"trial_balance", "ledger_dump"}:
        return import_ledgers_from_preview(tenant_id, preview_id, user_id=user_id, ip_address=ip_address)
    if upload_type == "sales_register":
        return import_sales_register_from_preview(tenant_id, preview_id, user_id=user_id, ip_address=ip_address)
    if upload_type == "purchase_register":
        return import_purchase_register_from_preview(tenant_id, preview_id, user_id=user_id, ip_address=ip_address)

    raise ValueError("Import is not available for this upload type yet.")


def get_import_summary_for_connection(tenant_id, connection_id):
    with db.get_db() as conn:
        summary_row = conn.execute(
            """
            SELECT
                COUNT(*) AS ledger_count,
                MAX(created_at) AS latest_ledger_import_at
            FROM accounting_ledgers
            WHERE tenant_id = ? AND connection_id = ?
            """,
            (tenant_id, connection_id),
        ).fetchone()

        voucher_row = conn.execute(
            """
            SELECT
                COUNT(*) AS voucher_count,
                SUM(CASE WHEN voucher_type = 'sales_invoice' THEN 1 ELSE 0 END) AS sales_invoice_count,
                SUM(CASE WHEN voucher_type = 'purchase_invoice' THEN 1 ELSE 0 END) AS purchase_invoice_count,
                MAX(created_at) AS latest_voucher_import_at
            FROM accounting_vouchers
            WHERE tenant_id = ? AND connection_id = ?
            """,
            (tenant_id, connection_id),
        ).fetchone()

        purchase_invoice_row = conn.execute(
            """
            SELECT MAX(created_at) AS latest_purchase_import_at
            FROM accounting_vouchers
            WHERE tenant_id = ? AND connection_id = ? AND voucher_type = 'purchase_invoice'
            """,
            (tenant_id, connection_id),
        ).fetchone()

        sales_invoice_row = conn.execute(
            """
            SELECT MAX(created_at) AS latest_sales_import_at
            FROM accounting_vouchers
            WHERE tenant_id = ? AND connection_id = ? AND voucher_type = 'sales_invoice'
            """,
            (tenant_id, connection_id),
        ).fetchone()

        invoice_line_row = conn.execute(
            """
            SELECT COUNT(*) AS invoice_line_count
            FROM accounting_invoice_lines il
            JOIN accounting_vouchers v
              ON v.id = il.voucher_id
             AND v.tenant_id = il.tenant_id
            WHERE il.tenant_id = ? AND v.connection_id = ?
            """,
            (tenant_id, connection_id),
        ).fetchone()

        by_group_rows = conn.execute(
            """
            SELECT
                COALESCE(NULLIF(TRIM(group_name), ''), 'Ungrouped') AS group_name,
                COUNT(*) AS ledger_count
            FROM accounting_ledgers
            WHERE tenant_id = ? AND connection_id = ?
            GROUP BY COALESCE(NULLIF(TRIM(group_name), ''), 'Ungrouped')
            ORDER BY ledger_count DESC, group_name ASC
            """,
            (tenant_id, connection_id),
        ).fetchall()

    return {
        "ledger_count": int(summary_row["ledger_count"] or 0) if summary_row else 0,
        "latest_ledger_import_at": summary_row["latest_ledger_import_at"] if summary_row else None,
        "voucher_count": int(voucher_row["voucher_count"] or 0) if voucher_row else 0,
        "sales_invoice_count": int(voucher_row["sales_invoice_count"] or 0) if voucher_row else 0,
        "purchase_invoice_count": int(voucher_row["purchase_invoice_count"] or 0) if voucher_row else 0,
        "latest_sales_import_at": sales_invoice_row["latest_sales_import_at"] if sales_invoice_row else None,
        "latest_voucher_import_at": voucher_row["latest_voucher_import_at"] if voucher_row else None,
        "latest_purchase_import_at": purchase_invoice_row["latest_purchase_import_at"] if purchase_invoice_row else None,
        "invoice_line_count": int(invoice_line_row["invoice_line_count"] or 0) if invoice_line_row else 0,
        "by_group": [
            {
                "group_name": row["group_name"],
                "ledger_count": int(row["ledger_count"] or 0),
            }
            for row in by_group_rows
        ],
    }

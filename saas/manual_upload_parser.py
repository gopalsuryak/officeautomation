import json
import os
import re
from datetime import datetime, timezone

import pandas as pd

import db

REQUIRED_COLUMNS_BY_UPLOAD_TYPE = {
    "sales_register": ["invoice_number", "invoice_date", "taxable_value", "gst_rate", "total"],
    "purchase_register": ["invoice_number", "invoice_date", "supplier_name", "taxable_value", "gst_rate", "total"],
    "gstr2b": ["supplier_gstin", "supplier_name", "invoice_number", "invoice_date", "taxable_value", "gst_rate", "total"],
    "trial_balance": ["ledger_name", "closing_balance"],
    "ledger_dump": ["ledger_name", "group_name", "closing_balance"],
    "voucher_dump": ["voucher_type", "voucher_number", "voucher_date", "amount"],
    "bank_statement": ["transaction_date", "description", "amount"],
    "zoho_export": [],
    "other": [],
}

ALLOWED_PARSE_EXTENSIONS = {".csv", ".xlsx", ".xls"}

_COLUMN_ALIAS_MAP = {
    "gstin_of_supplier": "supplier_gstin",
    "supplier_gstin": "supplier_gstin",
    "trade_legal_name": "supplier_name",
    "invoice_no": "invoice_number",
    "invoice_no_": "invoice_number",
    "invoice_number": "invoice_number",
    "invoice_num": "invoice_number",
    "invoice": "invoice_number",
    "invoice_value": "total",
    "invoice_date": "invoice_date",
    "date_of_invoice": "invoice_date",
    "supplier": "supplier_name",
    "supplier_name": "supplier_name",
    "vendor_name": "supplier_name",
    "integrated_tax": "igst",
    "central_tax": "cgst",
    "state_ut_tax": "sgst",
    "cess": "cess",
    "place_of_supply": "place_of_supply",
    "itc_availability": "itc_available",
    "reverse_charge": "reverse_charge",
    "invoice_type": "invoice_type",
    "period": "period",
    "taxable_value": "taxable_value",
    "taxable_amount": "taxable_value",
    "gst_rate": "gst_rate",
    "gst_rate_percent": "gst_rate",
    "gst_rate_percentage": "gst_rate",
    "total": "total",
    "total_value": "total",
    "total_amount": "total",
    "ledger": "ledger_name",
    "ledger_name": "ledger_name",
    "group": "group_name",
    "group_name": "group_name",
    "closing_balance": "closing_balance",
    "voucher_type": "voucher_type",
    "voucher_no": "voucher_number",
    "voucher_number": "voucher_number",
    "voucher_date": "voucher_date",
    "transaction_date": "transaction_date",
    "description": "description",
    "narration": "description",
    "amount": "amount",
}


def _clean_text(value):
    return str(value or "").strip()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_column_name(name):
    raw = _clean_text(name).lower()
    if not raw:
        return "column"

    normalized = raw
    normalized = normalized.replace("%", " percent ")
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")

    if normalized in _COLUMN_ALIAS_MAP:
        return _COLUMN_ALIAS_MAP[normalized]

    return normalized or "column"


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False)


def _json_load(value, default):
    if not value:
        return default
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def _upsert_preview(
    conn,
    tenant_id,
    uploaded_file,
    detected_columns,
    preview_rows,
    row_count,
    validation_status,
    validation_errors,
):
    existing = conn.execute(
        """
        SELECT id
        FROM accounting_upload_previews
        WHERE tenant_id = ? AND uploaded_file_id = ?
        LIMIT 1
        """,
        (tenant_id, uploaded_file["id"]),
    ).fetchone()

    now_iso = _now_iso()
    detected_columns_json = _json_dump(detected_columns)
    preview_rows_json = _json_dump(preview_rows)
    validation_errors_json = _json_dump(validation_errors)

    if existing:
        preview_id = existing["id"]
        conn.execute(
            """
            UPDATE accounting_upload_previews
            SET upload_type = ?,
                detected_columns_json = ?,
                preview_rows_json = ?,
                row_count = ?,
                validation_status = ?,
                validation_errors_json = ?,
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                uploaded_file["upload_type"],
                detected_columns_json,
                preview_rows_json,
                row_count,
                validation_status,
                validation_errors_json,
                now_iso,
                tenant_id,
                preview_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO accounting_upload_previews (
                tenant_id,
                uploaded_file_id,
                connection_id,
                client_entity_id,
                upload_type,
                detected_columns_json,
                preview_rows_json,
                row_count,
                validation_status,
                validation_errors_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                uploaded_file["id"],
                uploaded_file["connection_id"],
                uploaded_file["client_entity_id"],
                uploaded_file["upload_type"],
                detected_columns_json,
                preview_rows_json,
                row_count,
                validation_status,
                validation_errors_json,
                now_iso,
                now_iso,
            ),
        )
        preview_id = cur.lastrowid

    row = conn.execute(
        """
        SELECT *
        FROM accounting_upload_previews
        WHERE tenant_id = ? AND id = ?
        LIMIT 1
        """,
        (tenant_id, preview_id),
    ).fetchone()

    return dict(row) if row else None


def _status_for_validation(validation_status):
    if validation_status == "valid":
        return "validated", "Preview parsed successfully."
    if validation_status in {"invalid", "rejected"}:
        return "rejected", "Validation issues found."
    return "uploaded", "Preview pending."


def _prepare_preview_rows(df, max_rows):
    sample_df = df.head(max_rows).copy()
    sample_df = sample_df.where(pd.notna(sample_df), None)
    rows = sample_df.to_dict(orient="records")

    normalized_rows = []
    for row in rows:
        safe_row = {}
        for key, value in row.items():
            if isinstance(value, (dict, list, tuple, set)):
                safe_row[str(key)] = json.dumps(value, ensure_ascii=False)
            else:
                safe_row[str(key)] = value
        normalized_rows.append(safe_row)
    return normalized_rows


def _load_uploaded_file_row(conn, tenant_id, uploaded_file_id):
    return conn.execute(
        """
        SELECT *
        FROM accounting_uploaded_files
        WHERE tenant_id = ? AND id = ?
        LIMIT 1
        """,
        (tenant_id, uploaded_file_id),
    ).fetchone()


def parse_uploaded_file_for_preview(tenant_id, uploaded_file_id, max_rows=20, user_id=None, ip_address=None):
    with db.get_db() as conn:
        uploaded_file = _load_uploaded_file_row(conn, tenant_id, uploaded_file_id)
        if not uploaded_file:
            raise ValueError("Uploaded file not found.")

        connection = conn.execute(
            """
            SELECT id, provider
            FROM accounting_connections
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, uploaded_file["connection_id"]),
        ).fetchone()
        if not connection:
            raise ValueError("Accounting connection not found for uploaded file.")

        if connection["provider"] != "manual_upload":
            raise ValueError("Preview parsing is allowed only for manual_upload connections.")

        file_ext = _clean_text(uploaded_file["file_ext"]).lower()
        file_path = _clean_text(uploaded_file["file_path"])

        if not file_path or not os.path.exists(file_path):
            raise ValueError("Stored file could not be found.")

        validation_status = "pending"
        validation_errors = []
        detected_columns = []
        preview_rows = []
        row_count = 0

        if file_ext == ".xml":
            validation_status = "invalid"
            validation_errors = ["XML/Tally parsing will be added in a later phase."]
        elif file_ext not in ALLOWED_PARSE_EXTENSIONS:
            validation_status = "invalid"
            validation_errors = ["Only CSV/XLSX/XLS preview parsing is supported in this phase."]
        else:
            try:
                if file_ext == ".csv":
                    df = pd.read_csv(file_path)
                elif file_ext == ".xlsx":
                    df = pd.read_excel(file_path, engine="openpyxl")
                else:
                    # Legacy .xls support via xlrd.
                    df = pd.read_excel(file_path, engine="xlrd")
            except Exception as exc:
                validation_status = "invalid"
                validation_errors = [f"Could not parse file preview: {exc}"]
                df = None

            if df is not None:
                normalized_columns = [normalize_column_name(col) for col in df.columns]
                df.columns = normalized_columns
                detected_columns = normalized_columns
                row_count = int(len(df.index))
                preview_rows = _prepare_preview_rows(df, max_rows=max_rows)

                required_columns = REQUIRED_COLUMNS_BY_UPLOAD_TYPE.get(uploaded_file["upload_type"], [])
                missing_columns = [col for col in required_columns if col not in detected_columns]

                if missing_columns:
                    validation_status = "invalid"
                    validation_errors = [f"Missing required columns: {', '.join(missing_columns)}"]
                else:
                    validation_status = "valid"
                    validation_errors = []

        preview = _upsert_preview(
            conn=conn,
            tenant_id=tenant_id,
            uploaded_file=uploaded_file,
            detected_columns=detected_columns,
            preview_rows=preview_rows,
            row_count=row_count,
            validation_status=validation_status,
            validation_errors=validation_errors,
        )

        upload_status, validation_message = _status_for_validation(validation_status)
        if validation_errors:
            validation_message = validation_errors[0]

        conn.execute(
            """
            UPDATE accounting_uploaded_files
            SET status = ?,
                validation_message = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (upload_status, validation_message, tenant_id, uploaded_file_id),
        )

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_file_preview_parsed",
            entity_type="accounting_uploaded_file",
            entity_id=uploaded_file_id,
            old_value=None,
            new_value={
                "validation_status": validation_status,
                "row_count": row_count,
                "detected_columns": detected_columns,
            },
            metadata={
                "upload_type": uploaded_file["upload_type"],
                "connection_id": uploaded_file["connection_id"],
                "validation_errors": validation_errors,
            },
            ip_address=ip_address,
        )

        return _format_preview_row(preview)


def _format_preview_row(preview):
    if not preview:
        return None
    row = dict(preview)
    row["detected_columns"] = _json_load(row.get("detected_columns_json"), [])
    row["preview_rows"] = _json_load(row.get("preview_rows_json"), [])
    row["validation_errors"] = _json_load(row.get("validation_errors_json"), [])
    return row


def get_upload_preview(tenant_id, uploaded_file_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM accounting_upload_previews
            WHERE tenant_id = ? AND uploaded_file_id = ?
            LIMIT 1
            """,
            (tenant_id, uploaded_file_id),
        ).fetchone()
    return _format_preview_row(row)


def list_upload_previews_for_connection(tenant_id, connection_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM accounting_upload_previews
            WHERE tenant_id = ? AND connection_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (tenant_id, connection_id),
        ).fetchall()
    return [_format_preview_row(row) for row in rows]


def mark_upload_preview_status(tenant_id, preview_id, status, user_id=None, ip_address=None):
    status = _clean_text(status).lower()
    if status not in {"valid", "rejected"}:
        raise ValueError("Invalid preview status. Allowed: valid, rejected")

    with db.get_db() as conn:
        existing = conn.execute(
            """
            SELECT *
            FROM accounting_upload_previews
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, preview_id),
        ).fetchone()
        if not existing:
            return None

        now_iso = _now_iso()
        conn.execute(
            """
            UPDATE accounting_upload_previews
            SET validation_status = ?,
                updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (status, now_iso, tenant_id, preview_id),
        )

        uploaded_status = "validated" if status == "valid" else "rejected"
        validation_message = "Marked valid by user." if status == "valid" else "Rejected by user."

        conn.execute(
            """
            UPDATE accounting_uploaded_files
            SET status = ?,
                validation_message = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (uploaded_status, validation_message, tenant_id, existing["uploaded_file_id"]),
        )

        updated = conn.execute(
            """
            SELECT *
            FROM accounting_upload_previews
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, preview_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_upload_preview_status_changed",
            entity_type="accounting_upload_preview",
            entity_id=preview_id,
            old_value={"validation_status": existing["validation_status"]},
            new_value={"validation_status": status},
            metadata={"uploaded_file_id": existing["uploaded_file_id"]},
            ip_address=ip_address,
        )

    return _format_preview_row(updated)

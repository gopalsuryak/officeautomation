import os
import re
import secrets
from datetime import datetime, timezone

import db

try:
    from werkzeug.utils import secure_filename as werkzeug_secure_filename
except Exception:  # pragma: no cover
    werkzeug_secure_filename = None


ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".xml"}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
UPLOAD_TYPES = {
    "sales_register",
    "purchase_register",
    "gstr2b",
    "trial_balance",
    "ledger_dump",
    "voucher_dump",
    "bank_statement",
    "tally_xml",
    "zoho_export",
    "other",
}


def is_allowed_file(filename):
    name = str(filename or "").strip()
    if not name:
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def safe_filename(filename):
    raw_name = str(filename or "").strip()
    if not raw_name:
        return "file"

    if werkzeug_secure_filename:
        cleaned = werkzeug_secure_filename(raw_name)
        return cleaned or "file"

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_name)
    cleaned = cleaned.strip("._")
    return cleaned or "file"


def get_upload_storage_dir(tenant_id, client_entity_id, connection_id):
    base_dir = os.environ.get("ACCOUNTING_UPLOAD_DIR", "").strip()
    if base_dir:
        root = base_dir
    else:
        root = os.path.join(os.path.dirname(__file__), "uploads", "accounting")

    return os.path.join(
        root,
        str(tenant_id),
        str(client_entity_id),
        str(connection_id),
    )


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_upload_sync_run(conn, tenant_id, client_entity_id, connection_id, provider):
    now_iso = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO accounting_sync_runs (
            tenant_id,
            client_entity_id,
            connection_id,
            provider,
            sync_type,
            status,
            started_at,
            completed_at,
            records_synced,
            error_message,
            created_at
        )
        VALUES (?, ?, ?, ?, 'manual_upload', 'completed', ?, ?, 0, NULL, ?)
        """,
        (tenant_id, client_entity_id, connection_id, provider, now_iso, now_iso, now_iso),
    )
    return cur.lastrowid


def _get_file_size_bytes(file_storage):
    stream = file_storage.stream
    current_pos = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(current_pos, os.SEEK_SET)
    return int(size)


def save_manual_upload(tenant_id, connection_id, file_storage, upload_type, user_id=None, ip_address=None):
    upload_type = str(upload_type or "").strip().lower()
    if upload_type not in UPLOAD_TYPES:
        raise ValueError("Invalid upload type.")

    if not file_storage:
        raise ValueError("File is required.")

    original_filename = str(getattr(file_storage, "filename", "") or "").strip()
    if not original_filename:
        raise ValueError("Please choose a file.")

    if not is_allowed_file(original_filename):
        raise ValueError("Unsupported file type. Allowed: .xlsx, .xls, .csv, .xml")

    file_ext = os.path.splitext(original_filename)[1].lower()
    file_size_bytes = _get_file_size_bytes(file_storage)
    if file_size_bytes > MAX_FILE_SIZE_BYTES:
        raise ValueError("File exceeds max size of 25 MB.")

    with db.get_db() as conn:
        connection = conn.execute(
            """
            SELECT id, tenant_id, client_entity_id, provider, connection_name
            FROM accounting_connections
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, connection_id),
        ).fetchone()

        if not connection:
            raise ValueError("Accounting connection not found.")

        if connection["provider"] != "manual_upload":
            raise ValueError("File upload is allowed only for manual_upload connections.")

        storage_dir = get_upload_storage_dir(
            tenant_id=tenant_id,
            client_entity_id=connection["client_entity_id"],
            connection_id=connection_id,
        )
        os.makedirs(storage_dir, exist_ok=True)

        base_name = safe_filename(original_filename)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        token = secrets.token_hex(6)
        stored_filename = f"{ts}_{token}_{base_name}"
        full_path = os.path.join(storage_dir, stored_filename)

        file_storage.stream.seek(0)
        file_storage.save(full_path)

        sync_run_id = create_upload_sync_run(
            conn=conn,
            tenant_id=tenant_id,
            client_entity_id=connection["client_entity_id"],
            connection_id=connection_id,
            provider=connection["provider"],
        )

        cur = conn.execute(
            """
            INSERT INTO accounting_uploaded_files (
                tenant_id,
                client_entity_id,
                connection_id,
                sync_run_id,
                original_filename,
                stored_filename,
                file_path,
                file_ext,
                file_size_bytes,
                upload_type,
                status,
                validation_message,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?, ?)
            """,
            (
                tenant_id,
                connection["client_entity_id"],
                connection_id,
                sync_run_id,
                original_filename,
                stored_filename,
                full_path,
                file_ext,
                file_size_bytes,
                upload_type,
                "Stored only. Parsing comes next phase.",
                user_id,
                _now_iso(),
            ),
        )
        uploaded_file_id = cur.lastrowid

        uploaded = conn.execute(
            """
            SELECT *
            FROM accounting_uploaded_files
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, uploaded_file_id),
        ).fetchone()

        db.log_audit(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="accounting_file_uploaded",
            entity_type="accounting_uploaded_file",
            entity_id=uploaded_file_id,
            old_value=None,
            new_value={
                "connection_id": connection_id,
                "upload_type": upload_type,
                "original_filename": original_filename,
                "stored_filename": stored_filename,
                "file_ext": file_ext,
                "file_size_bytes": file_size_bytes,
                "sync_run_id": sync_run_id,
            },
            metadata={
                "connection_name": connection["connection_name"],
                "provider": connection["provider"],
            },
            ip_address=ip_address,
        )

    return dict(uploaded)


def list_uploaded_files(tenant_id, connection_id):
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM accounting_uploaded_files
            WHERE tenant_id = ? AND connection_id = ?
            ORDER BY datetime(created_at) DESC, id DESC
            """,
            (tenant_id, connection_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_uploaded_file(tenant_id, uploaded_file_id):
    with db.get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM accounting_uploaded_files
            WHERE tenant_id = ? AND id = ?
            LIMIT 1
            """,
            (tenant_id, uploaded_file_id),
        ).fetchone()
    return dict(row) if row else None

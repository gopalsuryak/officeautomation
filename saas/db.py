"""
CA Assist — database layer (Wave 1 restructure)
================================================
SQLite only. No external dependencies.

Tenant isolation model
----------------------
Every business table has a tenant_id column.
- tenants    = the CA firm using the SaaS product
- firm_users = staff / partners inside that CA firm
- client_entities = clients of that CA firm (companies/individuals they serve)

All queries in the application must filter by tenant_id so one CA firm
can never see another firm's data.

Paperclip columns (paperclip_issue_id, paperclip_run_id, etc.) are
lightweight references to background AI jobs. Paperclip is NOT the
source of truth — our tables are.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "ca_saas.db")

# Tables that expose an updated_at column and can be touched safely.
_UPDATABLE_TABLES = {"client_entities", "compliance_tasks", "usage_meters"}


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    """
    Yields an open sqlite3 connection with Row factory enabled.
    Commits on clean exit, rolls back on exception.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enforce foreign-key constraints for this connection.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def init_db():
    """
    Create all tables and indexes if they do not already exist.
    Safe to call on every application startup (idempotent).
    Existing tables and their data are never touched.
    """
    with get_db() as conn:
        # ── Original tables (unchanged — login/signup/billing depend on these) ──
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name          TEXT NOT NULL,
                firm_name     TEXT NOT NULL,
                gstin         TEXT,
                phone         TEXT,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );

            -- One tenant row per CA firm that subscribes.
            -- paperclip_* columns are internal references to the background AI layer.
            CREATE TABLE IF NOT EXISTS tenants (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id               INTEGER NOT NULL REFERENCES users(id),
                paperclip_company_id  TEXT,
                paperclip_agent_id    TEXT,
                plan                  TEXT DEFAULT 'starter',
                status                TEXT DEFAULT 'pending',
                created_at            TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id            INTEGER NOT NULL REFERENCES tenants(id),
                razorpay_payment_id  TEXT,
                razorpay_order_id    TEXT,
                plan                 TEXT NOT NULL,
                status               TEXT DEFAULT 'active',
                created_at           TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # ── Wave 1 new tables ─────────────────────────────────────────────

        # 1. firm_users — staff/partners inside a CA firm
        # role values: owner, partner, manager, senior, assistant, viewer
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS firm_users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
                user_id     INTEGER NOT NULL REFERENCES users(id),
                role        TEXT NOT NULL DEFAULT 'owner',
                is_active   INTEGER NOT NULL DEFAULT 1,
                invited_by  INTEGER REFERENCES users(id),
                invited_at  TEXT,
                accepted_at TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. client_entities — clients of the CA firm (not users of the SaaS)
        # entity_type: individual, huf, proprietorship, partnership, llp,
        #              pvt_ltd, public_ltd, trust, aop, other
        # status: active, inactive
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS client_entities (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
                name             TEXT NOT NULL,
                legal_name       TEXT,
                entity_type      TEXT,
                pan              TEXT,
                gstin            TEXT,
                cin              TEXT,
                email            TEXT,
                phone            TEXT,
                address          TEXT,
                state_code       TEXT,
                assigned_user_id INTEGER REFERENCES users(id),
                status           TEXT NOT NULL DEFAULT 'active',
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 3. compliance_tasks — the core work item owned fully by CA Assist
        # task_type examples: gstr1, gstr3b, gstr9, tds_return, itr, aoc4, mgt7, pf_esi ...
        # status: draft, pending_documents, ready_for_ai, ai_queued, ai_processing,
        #         ai_draft_ready, under_review, changes_required, approved,
        #         filed, closed, cancelled, ai_failed
        # priority: low, normal, high, urgent
        # pending_from: client, staff, reviewer, partner, system, none
        # paperclip_* columns are nullable internal references — not customer-visible
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS compliance_tasks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id    INTEGER NOT NULL REFERENCES client_entities(id),
                task_type           TEXT NOT NULL,
                title               TEXT NOT NULL,
                description         TEXT,
                period              TEXT,
                financial_year      TEXT,
                due_date            TEXT,
                status              TEXT NOT NULL DEFAULT 'draft',
                priority            TEXT NOT NULL DEFAULT 'normal',
                pending_from        TEXT NOT NULL DEFAULT 'staff',
                assigned_user_id    INTEGER REFERENCES users(id),
                reviewer_user_id    INTEGER REFERENCES users(id),
                paperclip_issue_id  TEXT,
                paperclip_run_id    TEXT,
                created_by          INTEGER REFERENCES users(id),
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 4. ai_outputs — structured results written back by the CA agent
        # Each agent run produces one row here (multiple versions per task allowed).
        # raw_json stores the full LLM JSON; individual fields are parsed out for
        # easy querying without JSON extraction.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ai_outputs (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id               INTEGER NOT NULL REFERENCES tenants(id),
                task_id                 INTEGER NOT NULL REFERENCES compliance_tasks(id),
                provider                TEXT,
                model                   TEXT,
                prompt_version          TEXT,
                output_type             TEXT,
                status_recommendation   TEXT,
                confidence              TEXT,
                missing_inputs_json     TEXT,
                risk_flags_json         TEXT,
                applicable_laws_json    TEXT,
                document_requests_json  TEXT,
                client_message_draft    TEXT,
                internal_working_note   TEXT,
                output_markdown         TEXT,
                raw_json                TEXT,
                paperclip_comment_id    TEXT,
                paperclip_run_id        TEXT,
                created_at              TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 5. document_requests — track what docs have been asked from the client
        # requested_from: client, third_party, internal
        # status: requested, received, waived, not_required
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS document_requests (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id INTEGER NOT NULL REFERENCES client_entities(id),
                task_id          INTEGER NOT NULL REFERENCES compliance_tasks(id),
                document_name    TEXT NOT NULL,
                description      TEXT,
                requested_from   TEXT DEFAULT 'client',
                status           TEXT NOT NULL DEFAULT 'requested',
                notes            TEXT,
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
                received_at      TEXT
            );
        """)

        # 6. task_comments — internal thread on a task (staff + AI + system messages)
        # comment_type: user, ai, system, review
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_comments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id    INTEGER NOT NULL REFERENCES tenants(id),
                task_id      INTEGER NOT NULL REFERENCES compliance_tasks(id),
                user_id      INTEGER REFERENCES users(id),
                comment_type TEXT NOT NULL DEFAULT 'user',
                body         TEXT NOT NULL,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 7. task_status_history — immutable record of every status change
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_status_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id           INTEGER NOT NULL REFERENCES tenants(id),
                task_id             INTEGER NOT NULL REFERENCES compliance_tasks(id),
                old_status          TEXT,
                new_status          TEXT NOT NULL,
                changed_by_user_id  INTEGER REFERENCES users(id),
                reason              TEXT,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 8. review_actions — partner/reviewer approval decisions on AI outputs
        # action: approved, rejected, changes_requested, sent_for_review
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS review_actions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
                task_id          INTEGER NOT NULL REFERENCES compliance_tasks(id),
                ai_output_id     INTEGER REFERENCES ai_outputs(id),
                reviewer_user_id INTEGER REFERENCES users(id),
                action           TEXT NOT NULL,
                comment          TEXT,
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 9. audit_logs — security and change trail for every significant action
        # old_value_json / new_value_json / metadata_json store JSON strings.
        # tenant_id is nullable so system-level events (e.g. failed login) can be recorded.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id       INTEGER REFERENCES tenants(id),
                user_id         INTEGER REFERENCES users(id),
                action          TEXT NOT NULL,
                entity_type     TEXT,
                entity_id       TEXT,
                old_value_json  TEXT,
                new_value_json  TEXT,
                metadata_json   TEXT,
                ip_address      TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 10. usage_meters — per-tenant monthly usage for billing enforcement
        # period_month format: YYYY-MM  (e.g. "2026-05")
        # UNIQUE constraint prevents duplicate rows per tenant per month.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS usage_meters (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id                   INTEGER NOT NULL REFERENCES tenants(id),
                period_month                TEXT NOT NULL,
                ai_tasks_used               INTEGER NOT NULL DEFAULT 0,
                llm_tokens_used             INTEGER NOT NULL DEFAULT 0,
                llm_cost_usd                REAL NOT NULL DEFAULT 0,
                documents_uploaded          INTEGER NOT NULL DEFAULT 0,
                document_requests_created   INTEGER NOT NULL DEFAULT 0,
                created_at                  TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at                  TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id, period_month)
            );
        """)

        # 11. client_credentials — credential vault foundation metadata
        # NOTE: this phase does not implement real encryption or portal login execution.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS client_credentials (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id               INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id        INTEGER NOT NULL REFERENCES client_entities(id),
                portal_type             TEXT NOT NULL,
                display_name            TEXT NOT NULL,
                username                TEXT,
                secret_value_encrypted  TEXT,
                secret_hint             TEXT,
                otp_required            INTEGER NOT NULL DEFAULT 0,
                status                  TEXT NOT NULL DEFAULT 'draft',
                last_verified_at        TEXT,
                last_login_status       TEXT,
                last_error              TEXT,
                metadata_json           TEXT,
                created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 12. accounting_connections — connector registry per client/provider
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounting_connections (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id        INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id INTEGER NOT NULL REFERENCES client_entities(id),
                provider         TEXT NOT NULL,
                connection_name  TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'draft',
                auth_type        TEXT,
                last_sync_at     TEXT,
                last_error       TEXT,
                metadata_json    TEXT,
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 13. accounting_sync_runs — history of sync operations (foundation only)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounting_sync_runs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id INTEGER NOT NULL REFERENCES client_entities(id),
                connection_id  INTEGER NOT NULL REFERENCES accounting_connections(id),
                provider       TEXT NOT NULL,
                sync_type      TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'queued',
                started_at     TEXT,
                completed_at   TEXT,
                records_synced INTEGER DEFAULT 0,
                error_message  TEXT,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 14. accounting_ledgers — normalized ledger snapshots
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounting_ledgers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id INTEGER NOT NULL REFERENCES client_entities(id),
                connection_id   INTEGER REFERENCES accounting_connections(id),
                provider        TEXT,
                external_id     TEXT,
                ledger_name     TEXT NOT NULL,
                group_name      TEXT,
                opening_balance REAL DEFAULT 0,
                closing_balance REAL DEFAULT 0,
                raw_json        TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 15. accounting_vouchers — normalized voucher records
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounting_vouchers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id       INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id INTEGER NOT NULL REFERENCES client_entities(id),
                connection_id   INTEGER REFERENCES accounting_connections(id),
                provider        TEXT,
                external_id     TEXT,
                voucher_type    TEXT,
                voucher_number  TEXT,
                voucher_date    TEXT,
                party_name      TEXT,
                amount          REAL DEFAULT 0,
                gstin           TEXT,
                narration       TEXT,
                raw_json        TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 16. accounting_invoice_lines — invoice item/tax level detail
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounting_invoice_lines (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id      INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id INTEGER NOT NULL REFERENCES client_entities(id),
                voucher_id     INTEGER NOT NULL REFERENCES accounting_vouchers(id),
                item_name      TEXT,
                hsn_sac        TEXT,
                taxable_value  REAL DEFAULT 0,
                gst_rate       REAL DEFAULT 0,
                igst           REAL DEFAULT 0,
                cgst           REAL DEFAULT 0,
                sgst           REAL DEFAULT 0,
                total          REAL DEFAULT 0,
                raw_json       TEXT,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 17. accounting_uploaded_files — manual upload file metadata and status
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounting_uploaded_files (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id         INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id  INTEGER NOT NULL REFERENCES client_entities(id),
                connection_id     INTEGER NOT NULL REFERENCES accounting_connections(id),
                sync_run_id       INTEGER REFERENCES accounting_sync_runs(id),
                original_filename TEXT NOT NULL,
                stored_filename   TEXT NOT NULL,
                file_path         TEXT NOT NULL,
                file_ext          TEXT,
                file_size_bytes   INTEGER DEFAULT 0,
                upload_type       TEXT,
                status            TEXT NOT NULL DEFAULT 'uploaded',
                validation_message TEXT,
                created_by        INTEGER REFERENCES users(id),
                created_at        TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 18. accounting_upload_previews — parser preview metadata and validation status
        # Reused by ledger, sales, purchase, and GSTR-2B preview-only phases.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounting_upload_previews (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id              INTEGER NOT NULL REFERENCES tenants(id),
                uploaded_file_id       INTEGER NOT NULL REFERENCES accounting_uploaded_files(id),
                connection_id          INTEGER NOT NULL REFERENCES accounting_connections(id),
                client_entity_id       INTEGER NOT NULL REFERENCES client_entities(id),
                upload_type            TEXT,
                detected_columns_json  TEXT,
                preview_rows_json      TEXT,
                row_count              INTEGER DEFAULT 0,
                validation_status      TEXT NOT NULL DEFAULT 'pending',
                validation_errors_json TEXT,
                created_at             TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at             TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 19. gst_reconciliation_runs — reconciliation execution snapshots
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gst_reconciliation_runs (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id               INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id        INTEGER NOT NULL REFERENCES client_entities(id),
                connection_id           INTEGER REFERENCES accounting_connections(id),
                gstr2b_preview_id       INTEGER NOT NULL REFERENCES accounting_upload_previews(id),
                run_type                TEXT NOT NULL DEFAULT 'purchase_vs_2b',
                status                  TEXT NOT NULL DEFAULT 'completed',
                total_books_invoices    INTEGER DEFAULT 0,
                total_2b_invoices       INTEGER DEFAULT 0,
                matched_count           INTEGER DEFAULT 0,
                missing_in_2b_count     INTEGER DEFAULT 0,
                missing_in_books_count  INTEGER DEFAULT 0,
                amount_mismatch_count   INTEGER DEFAULT 0,
                tax_mismatch_count      INTEGER DEFAULT 0,
                created_by              INTEGER REFERENCES users(id),
                created_at              TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 20. gst_reconciliation_results — row-level purchase vs GSTR-2B comparison
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gst_reconciliation_results (
                id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id                  INTEGER NOT NULL REFERENCES tenants(id),
                reconciliation_run_id      INTEGER NOT NULL REFERENCES gst_reconciliation_runs(id),
                client_entity_id           INTEGER NOT NULL REFERENCES client_entities(id),
                match_status               TEXT NOT NULL,
                supplier_gstin             TEXT,
                supplier_name              TEXT,
                invoice_number             TEXT,
                invoice_date_books         TEXT,
                invoice_date_2b            TEXT,
                books_voucher_id           INTEGER REFERENCES accounting_vouchers(id),
                books_taxable_value        REAL DEFAULT 0,
                books_igst                 REAL DEFAULT 0,
                books_cgst                 REAL DEFAULT 0,
                books_sgst                 REAL DEFAULT 0,
                books_total                REAL DEFAULT 0,
                gstr2b_taxable_value       REAL DEFAULT 0,
                gstr2b_igst                REAL DEFAULT 0,
                gstr2b_cgst                REAL DEFAULT 0,
                gstr2b_sgst                REAL DEFAULT 0,
                gstr2b_total               REAL DEFAULT 0,
                difference_taxable_value   REAL DEFAULT 0,
                difference_tax             REAL DEFAULT 0,
                difference_total           REAL DEFAULT 0,
                remarks                    TEXT,
                raw_books_json             TEXT,
                raw_2b_json                TEXT,
                created_at                 TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 21. gst_reconciliation_notes — review-only GST working notes
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gst_reconciliation_notes (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id               INTEGER NOT NULL REFERENCES tenants(id),
                reconciliation_run_id   INTEGER NOT NULL REFERENCES gst_reconciliation_runs(id),
                client_entity_id        INTEGER NOT NULL REFERENCES client_entities(id),
                note_type               TEXT NOT NULL DEFAULT 'gst_reconciliation_working_note',
                status                  TEXT NOT NULL DEFAULT 'draft',
                confidence              TEXT DEFAULT 'medium',
                summary_json            TEXT,
                risk_flags_json         TEXT,
                document_requests_json  TEXT,
                working_note_markdown   TEXT,
                created_by              INTEGER REFERENCES users(id),
                created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 22. gst_reconciliation_task_links — links reconciliation runs to review tasks
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gst_reconciliation_task_links (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id               INTEGER NOT NULL REFERENCES tenants(id),
                reconciliation_run_id   INTEGER NOT NULL REFERENCES gst_reconciliation_runs(id),
                task_id                 INTEGER NOT NULL REFERENCES compliance_tasks(id),
                client_entity_id        INTEGER NOT NULL REFERENCES client_entities(id),
                link_type               TEXT NOT NULL DEFAULT 'gst_review',
                created_by              INTEGER REFERENCES users(id),
                created_at              TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 23. gstr3b_review_packs — review-only GST packs (not for filing)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gstr3b_review_packs (
                id                            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id                     INTEGER NOT NULL REFERENCES tenants(id),
                client_entity_id              INTEGER NOT NULL REFERENCES client_entities(id),
                reconciliation_run_id         INTEGER NOT NULL REFERENCES gst_reconciliation_runs(id),
                linked_task_id                INTEGER REFERENCES compliance_tasks(id),
                note_id                       INTEGER REFERENCES gst_reconciliation_notes(id),
                status                        TEXT NOT NULL DEFAULT 'draft',
                period                        TEXT,
                sales_summary_json            TEXT,
                purchase_summary_json         TEXT,
                reconciliation_summary_json   TEXT,
                pending_documents_json        TEXT,
                risk_flags_json               TEXT,
                review_checklist_json         TEXT,
                pack_markdown                 TEXT,
                created_by                    INTEGER REFERENCES users(id),
                created_at                    TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at                    TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 24. document_communication_drafts — internal drafts for document requests (email/whatsapp)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS document_communication_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                client_entity_id INTEGER NOT NULL,
                draft_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                subject TEXT,
                body TEXT NOT NULL,
                document_request_ids_json TEXT,
                created_by INTEGER,
                reviewed_by INTEGER,
                reviewed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Indexes for document_communication_drafts
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_document_communication_drafts_tenant_task_created
                ON document_communication_drafts(tenant_id, task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_document_communication_drafts_tenant_client_status
                ON document_communication_drafts(tenant_id, client_entity_id, status);
        """)

        # 25. email_send_queue — queued emails for future sending (manual review phase)
        # status includes queued, ready_to_send, approved_to_send, sent, failed, cancelled.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_send_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                client_entity_id INTEGER NOT NULL,
                task_id INTEGER,
                draft_id INTEGER NOT NULL,
                provider_setting_id INTEGER,
                to_email TEXT,
                cc_email TEXT,
                bcc_email TEXT,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                send_mode TEXT NOT NULL DEFAULT 'manual_review',
                provider TEXT,
                queued_by INTEGER,
                queued_at TEXT DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                failed_at TEXT,
                error_message TEXT,
                metadata_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Backward-compatible column add for existing installations.
        email_queue_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(email_send_queue)").fetchall()
        }
        if "provider_setting_id" not in email_queue_columns:
            conn.execute("ALTER TABLE email_send_queue ADD COLUMN provider_setting_id INTEGER")

        # Backward-compatible column add for ai_outputs (added in production readiness phase)
        ai_output_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(ai_outputs)").fetchall()
        }
        if "paperclip_run_id" not in ai_output_columns:
            conn.execute("ALTER TABLE ai_outputs ADD COLUMN paperclip_run_id TEXT")

        # Indexes for email_send_queue
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_email_send_queue_tenant_status_queued_at
                ON email_send_queue(tenant_id, status, queued_at);
            CREATE INDEX IF NOT EXISTS idx_email_send_queue_tenant_client_queued_at
                ON email_send_queue(tenant_id, client_entity_id, queued_at);
            CREATE INDEX IF NOT EXISTS idx_email_send_queue_tenant_draft_id
                ON email_send_queue(tenant_id, draft_id);
            CREATE INDEX IF NOT EXISTS idx_email_send_queue_tenant_provider_setting_id
                ON email_send_queue(tenant_id, provider_setting_id);
        """)

        # 26. email_send_approvals — manual approval gate over dry-run previews
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_send_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                queue_id INTEGER NOT NULL,
                dry_run_preview_id INTEGER NOT NULL,
                provider_setting_id INTEGER,
                approval_status TEXT NOT NULL DEFAULT 'approved',
                approved_by INTEGER,
                approved_at TEXT DEFAULT CURRENT_TIMESTAMP,
                approval_note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_email_send_approvals_tenant_queue
                ON email_send_approvals(tenant_id, queue_id);
            CREATE INDEX IF NOT EXISTS idx_email_send_approvals_tenant_dry_run_preview
                ON email_send_approvals(tenant_id, dry_run_preview_id);
            CREATE INDEX IF NOT EXISTS idx_email_send_approvals_tenant_approved_at
                ON email_send_approvals(tenant_id, approved_at);
        """)

        # 27. email_failure_reviews — review and reopen metadata for failed email queue items
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_failure_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                queue_id INTEGER NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'reviewed',
                review_note TEXT,
                reopen_note TEXT,
                reviewed_by INTEGER,
                reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                reopened_by INTEGER,
                reopened_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_email_failure_reviews_tenant_queue
                ON email_failure_reviews(tenant_id, queue_id);
            CREATE INDEX IF NOT EXISTS idx_email_failure_reviews_tenant_reviewed_at
                ON email_failure_reviews(tenant_id, reviewed_at);
        """)

        # 28. email_provider_settings — provider metadata foundation for future reviewed email sending
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_provider_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                provider_type TEXT NOT NULL,
                display_name TEXT NOT NULL,
                from_name TEXT,
                from_email TEXT,
                smtp_host TEXT,
                smtp_port INTEGER,
                smtp_username TEXT,
                smtp_password_secret TEXT,
                oauth_client_id TEXT,
                oauth_status TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                is_default INTEGER DEFAULT 0,
                last_checked_at TEXT,
                last_check_status TEXT,
                last_error TEXT,
                metadata_json TEXT,
                created_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Indexes for email_provider_settings
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_email_provider_settings_tenant_provider_status
                ON email_provider_settings(tenant_id, provider_type, status);
            CREATE INDEX IF NOT EXISTS idx_email_provider_settings_tenant_default
                ON email_provider_settings(tenant_id, is_default);
            CREATE INDEX IF NOT EXISTS idx_email_provider_settings_tenant_from_email
                ON email_provider_settings(tenant_id, from_email);
        """)

        # 29. email_dry_run_previews — local payload preview snapshots (no provider calls)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_dry_run_previews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                queue_id INTEGER NOT NULL,
                provider_setting_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'generated',
                from_email TEXT,
                from_name TEXT,
                to_email TEXT,
                cc_email TEXT,
                bcc_email TEXT,
                subject TEXT,
                body TEXT,
                validation_json TEXT,
                created_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_email_dry_run_previews_tenant_queue_created
                ON email_dry_run_previews(tenant_id, queue_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_email_dry_run_previews_tenant_provider_created
                ON email_dry_run_previews(tenant_id, provider_setting_id, created_at);
        """)

        # 30. email_readiness_checks — internal production-readiness checklist for email module
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS email_readiness_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER NOT NULL,
                check_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                notes TEXT,
                completed_by INTEGER,
                completed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_email_readiness_checks_tenant_check_key
                ON email_readiness_checks(tenant_id, check_key);
            CREATE INDEX IF NOT EXISTS idx_email_readiness_checks_tenant_status
                ON email_readiness_checks(tenant_id, status);
        """)

        # ── Indexes ───────────────────────────────────────────────────────
        # All queries must filter by tenant_id first — these indexes support that.
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_firm_users_tenant_user
                ON firm_users(tenant_id, user_id);

            CREATE INDEX IF NOT EXISTS idx_client_entities_tenant_status
                ON client_entities(tenant_id, status);

            CREATE INDEX IF NOT EXISTS idx_client_entities_tenant_gstin
                ON client_entities(tenant_id, gstin);

            CREATE INDEX IF NOT EXISTS idx_compliance_tasks_tenant_status
                ON compliance_tasks(tenant_id, status);

            CREATE INDEX IF NOT EXISTS idx_compliance_tasks_tenant_client
                ON compliance_tasks(tenant_id, client_entity_id);

            CREATE INDEX IF NOT EXISTS idx_compliance_tasks_tenant_due_date
                ON compliance_tasks(tenant_id, due_date);

            CREATE INDEX IF NOT EXISTS idx_ai_outputs_tenant_task
                ON ai_outputs(tenant_id, task_id);

            CREATE INDEX IF NOT EXISTS idx_ai_outputs_tenant_run
                ON ai_outputs(tenant_id, paperclip_run_id);

            CREATE INDEX IF NOT EXISTS idx_document_requests_tenant_task_status
                ON document_requests(tenant_id, task_id, status);

            CREATE INDEX IF NOT EXISTS idx_task_status_history_tenant_task
                ON task_status_history(tenant_id, task_id);

            CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created
                ON audit_logs(tenant_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_usage_meters_tenant_month
                ON usage_meters(tenant_id, period_month);

            CREATE INDEX IF NOT EXISTS idx_client_credentials_tenant_client
                ON client_credentials(tenant_id, client_entity_id);

            CREATE INDEX IF NOT EXISTS idx_client_credentials_tenant_portal_status
                ON client_credentials(tenant_id, portal_type, status);

            CREATE INDEX IF NOT EXISTS idx_accounting_connections_tenant_client
                ON accounting_connections(tenant_id, client_entity_id);

            CREATE INDEX IF NOT EXISTS idx_accounting_connections_tenant_provider_status
                ON accounting_connections(tenant_id, provider, status);

            CREATE INDEX IF NOT EXISTS idx_accounting_sync_runs_tenant_connection_created
                ON accounting_sync_runs(tenant_id, connection_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_accounting_ledgers_tenant_client_ledger
                ON accounting_ledgers(tenant_id, client_entity_id, ledger_name);

            CREATE INDEX IF NOT EXISTS idx_accounting_vouchers_tenant_client_date
                ON accounting_vouchers(tenant_id, client_entity_id, voucher_date);

            CREATE INDEX IF NOT EXISTS idx_accounting_vouchers_tenant_client_type
                ON accounting_vouchers(tenant_id, client_entity_id, voucher_type);

            CREATE INDEX IF NOT EXISTS idx_accounting_invoice_lines_tenant_voucher
                ON accounting_invoice_lines(tenant_id, voucher_id);

            CREATE INDEX IF NOT EXISTS idx_accounting_uploaded_files_tenant_connection_created
                ON accounting_uploaded_files(tenant_id, connection_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_accounting_uploaded_files_tenant_client_upload_type
                ON accounting_uploaded_files(tenant_id, client_entity_id, upload_type);

            CREATE INDEX IF NOT EXISTS idx_accounting_upload_previews_tenant_uploaded_file
                ON accounting_upload_previews(tenant_id, uploaded_file_id);

            CREATE INDEX IF NOT EXISTS idx_accounting_upload_previews_tenant_connection_created
                ON accounting_upload_previews(tenant_id, connection_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_runs_tenant_client_created
                ON gst_reconciliation_runs(tenant_id, client_entity_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_runs_tenant_preview
                ON gst_reconciliation_runs(tenant_id, gstr2b_preview_id);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_results_tenant_run
                ON gst_reconciliation_results(tenant_id, reconciliation_run_id);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_results_tenant_client_status
                ON gst_reconciliation_results(tenant_id, client_entity_id, match_status);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_results_tenant_gstin_invoice
                ON gst_reconciliation_results(tenant_id, supplier_gstin, invoice_number);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_notes_tenant_run
                ON gst_reconciliation_notes(tenant_id, reconciliation_run_id);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_notes_tenant_client_created
                ON gst_reconciliation_notes(tenant_id, client_entity_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_task_links_tenant_run
                ON gst_reconciliation_task_links(tenant_id, reconciliation_run_id);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_task_links_tenant_task
                ON gst_reconciliation_task_links(tenant_id, task_id);

            CREATE INDEX IF NOT EXISTS idx_gst_reconciliation_task_links_tenant_client
                ON gst_reconciliation_task_links(tenant_id, client_entity_id);

            CREATE INDEX IF NOT EXISTS idx_gstr3b_review_packs_tenant_run
                ON gstr3b_review_packs(tenant_id, reconciliation_run_id);

            CREATE INDEX IF NOT EXISTS idx_gstr3b_review_packs_tenant_client_created
                ON gstr3b_review_packs(tenant_id, client_entity_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_gstr3b_review_packs_tenant_task
                ON gstr3b_review_packs(tenant_id, linked_task_id);
        """)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def ensure_owner_firm_user(user_id: int, tenant_id: int) -> None:
    """
    Creates a firm_users row with role='owner' for the given user+tenant
    if one does not already exist. Called after signup to seed the owner role.
    """
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM firm_users WHERE user_id = ? AND tenant_id = ?",
            (user_id, tenant_id),
        ).fetchone()
        if not existing:
            conn.execute(
                """
                INSERT INTO firm_users (tenant_id, user_id, role, is_active, accepted_at)
                VALUES (?, ?, 'owner', 1, ?)
                """,
                (tenant_id, user_id, datetime.now(timezone.utc).isoformat()),
            )


def get_current_tenant_for_user(user_id: int):
    """
    Returns the active tenant row for a user, or None if not found.
    In the current model every user has one tenant (their own CA firm).
    """
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM tenants WHERE user_id = ? LIMIT 1",
            (user_id,),
        ).fetchone()


def get_user_role(user_id: int, tenant_id: int) -> str | None:
    """
    Returns the role string from firm_users for (user_id, tenant_id),
    or None if no active row exists.
    """
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT role FROM firm_users
            WHERE user_id = ? AND tenant_id = ? AND is_active = 1
            LIMIT 1
            """,
            (user_id, tenant_id),
        ).fetchone()
    return row["role"] if row else None


def log_audit(
    conn,
    tenant_id,
    user_id,
    action: str,
    entity_type: str = None,
    entity_id=None,
    old_value=None,
    new_value=None,
    metadata=None,
    ip_address: str = None,
) -> None:
    """
    Insert a row into audit_logs.

    Pass the existing open connection (conn) so this can participate in the
    caller's transaction — if the caller rolls back, the audit entry rolls back too.

    old_value, new_value, metadata can be dicts or lists; they will be
    JSON-serialised. Passing None results in a NULL column value.
    """
    def _to_json(value):
        if value is None:
            return None
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            # Fallback: convert to string so we never lose the record
            return json.dumps(str(value))

    conn.execute(
        """
        INSERT INTO audit_logs
            (tenant_id, user_id, action, entity_type, entity_id,
             old_value_json, new_value_json, metadata_json, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            user_id,
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            _to_json(old_value),
            _to_json(new_value),
            _to_json(metadata),
            ip_address,
        ),
    )


def touch_updated_at(conn, table_name: str, row_id: int) -> None:
    """
    Set updated_at = current UTC timestamp for a single row.

    table_name is checked against a strict whitelist to prevent SQL injection —
    only tables that actually have an updated_at column are allowed.
    """
    if table_name not in _UPDATABLE_TABLES:
        raise ValueError(
            f"touch_updated_at: '{table_name}' is not an allowed table. "
            f"Allowed: {sorted(_UPDATABLE_TABLES)}"
        )
    # Table name is safe: validated against the whitelist above.
    # Row ID is passed as a parameter, never interpolated.
    conn.execute(
        f"UPDATE {table_name} SET updated_at = ? WHERE id = ?",  # noqa: S608
        (datetime.now(timezone.utc).isoformat(), row_id),
    )

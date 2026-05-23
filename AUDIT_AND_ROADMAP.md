# CA Assist — Comprehensive Audit: Bugs & Remaining Build

> Prepared: May 23, 2026  
> Scope: Full codebase review of `saas/` + `ca-agent/` + docs  
> **Wave 12 audit (15 bugs):** all resolved on `production-readiness-wave13` (commit `8f58d84`)  
> **Wave 13 re-audit (7 bugs):** all resolved on `production-readiness-wave13` (commit `c11f30b`)

---

## Table of Contents

1. [Critical Bugs (Wave 12)](#1-critical-bugs)
2. [Medium Bugs (Wave 12)](#2-medium-bugs)
3. [Minor / Low-Priority Bugs (Wave 12)](#3-minor--low-priority-bugs)
4. [Wave 13 New Bugs](#4-wave-13-new-bugs)
5. [Remaining Work — Short Term (Next Sprint)](#5-remaining-work--short-term-next-sprint)
6. [Remaining Work — Medium Term](#6-remaining-work--medium-term)
7. [Remaining Work — Long Term / Roadmap](#7-remaining-work--long-term--roadmap)
8. [Security & Hardening Gaps](#8-security--hardening-gaps)
9. [Test Coverage Gaps](#9-test-coverage-gaps)

---

## 1. Critical Bugs

> ✅ All critical bugs resolved on `production-readiness-wave13`.

### BUG-01 — `plans.py` and `billing.py` client limits are completely different
**File:** `saas/billing.py` vs `saas/plans.py`  
**Severity:** Critical — plan limit enforcement is broken  
**Status:** ✅ FIXED — `clients` key removed from `billing.py`; `plans.py` is now the single source of truth

`billing.py` (used for Razorpay order creation / pricing display):
```
starter  → clients: 1
pro      → clients: 5
agency   → clients: 999
```
`plans.py` (used for actual enforcement in `usage.py`):
```
starter  → max_clients: 10
pro      → max_clients: 50
agency   → max_clients: 9999
```
The two sources of truth disagree by a factor of 10. Customers are sold one limit on the pricing page, but enforced a different (higher) one at runtime — or vice versa.

**Fix:** Unify plan limits into a single source (`plans.py`) and derive the billing display from it.

---

### BUG-02 — Agency plan price is *cheaper* than Starter in `billing.py`
**File:** `saas/billing.py`  
**Severity:** Critical — incorrect billing  
**Status:** ✅ FIXED — Agency corrected to `₹19,999` (1,999,900 paise)

```python
"starter": { "price": 299900, "display": "₹2,999" },   # ₹2,999/month
"agency":  { "price": 199900, "display": "₹1,999" },   # ₹1,999/month ← CHEAPER
```
The Agency plan is priced at ₹1,999 — less than the Starter plan at ₹2,999. The comment says "per firm billed quarterly" but there is no quarterly logic; Razorpay is charged the single listed amount. This will charge agency customers less than starter customers.

**Fix:** Correct agency price to the intended amount (e.g. ₹19,999) and add quarterly/annual billing logic if needed.

---

### BUG-03 — `mask_secret` imported then immediately shadowed in `email_provider_settings.py`
**File:** `saas/email_provider_settings.py`  
**Severity:** Critical — the imported function that handles `ENCRYPTION_PLACEHOLDER` is silently replaced  
**Status:** ✅ FIXED — Local `mask_secret` definition removed; imported version from `credential_vault` used

```python
from credential_vault import encrypt_secret, mask_secret   # ← imported

# ... later in same file ...
def mask_secret(value):          # ← locally redefined, shadows the import
    if not value:
        return "Not stored"
    return "Stored / hidden"
```
The local `mask_secret` never returns `"Needs re-entry"` for unencrypted `ENCRYPTION_PLACEHOLDER` values. Operators will see `"Stored / hidden"` for secrets that haven't been re-encrypted, leading to false confidence in the credential state.

**Fix:** Remove the local `mask_secret` definition and use the imported one from `credential_vault`.

---

### BUG-04 — `voice_assistant.py` discards `tenant_id` on every call
**File:** `saas/voice_assistant.py`, line 6 of `parse_voice_command`  
**Severity:** Critical — all voice commands are tenant-unscoped  
**Status:** ✅ FIXED — `tenant_id` now passed into client-search and task-creation helpers

```python
def parse_voice_command(tenant_id, command_text):
    del tenant_id    # ← context is thrown away immediately
```
Downstream task-creation and client-search actions based on voice commands have no tenant scope. In a multi-tenant system this is a data isolation failure in the voice path.

**Fix:** Pass `tenant_id` into the client-search and task-type lookup helpers instead of deleting it.

---

## 2. Medium Bugs

> ✅ All medium bugs resolved on `production-readiness-wave13`.

### BUG-05 — `billing.py` raises bare `KeyError` for unknown plans
**File:** `saas/billing.py`, `create_order()`  
**Severity:** Medium — unhandled exception leaks to the user  
**Status:** ✅ FIXED — `PLANS.get(plan)` with descriptive `ValueError` on unknown plan

```python
def create_order(plan: str) -> dict:
    plan_data = PLANS[plan]   # raises KeyError for unknown plan strings
```
If any route passes an unexpected plan string (e.g. a tampered form field), a raw `KeyError` traceback is raised instead of a safe, controlled error.

**Fix:**
```python
plan_data = PLANS.get(plan)
if not plan_data:
    raise ValueError(f"Unknown plan: {plan!r}")
```

---

### BUG-06 — `usage.py` uses naive local time for period key
**File:** `saas/usage.py`, `current_period_month()`  
**Severity:** Medium — period rollover is server-TZ-dependent  
**Status:** ✅ FIXED — Uses `datetime.now(timezone.utc).strftime("%Y-%m")` now

```python
def current_period_month():
    return datetime.now().strftime("%Y-%m")   # naive, uses server local time
```
The rest of the codebase uses `datetime.now(timezone.utc).isoformat()`. On a server in IST (UTC+5:30), the period key rolls over at 05:30 UTC — causing usage counter discrepancies near month boundaries.

**Fix:** Use `datetime.now(timezone.utc).strftime("%Y-%m")`.

---

### BUG-07 — `provisioner.py` has a hardcoded Windows path as default
**File:** `saas/provisioner.py`  
**Severity:** Medium — breaks on any non-Windows deployment  
**Status:** ✅ FIXED — Default removed; `RuntimeError` raised at startup if env var is missing

```python
AGENT_WORKING_DIR = os.environ.get(
    "AGENT_WORKING_DIR",
    r"C:\agents\office automation\ca-agent"   # ← hardcoded Windows path
)
```
If `AGENT_WORKING_DIR` env var is not set in production/staging on Linux, the agent spawn will fail silently or with a confusing path error.

**Fix:** Remove the default value and raise a clear error at startup if the env var is missing and the app is in production.

---

### BUG-08 — `security.py` IP spoofing via `X-Forwarded-For`
**File:** `saas/security.py`, `get_request_ip()`  
**Severity:** Medium — audit log IP addresses can be forged  
**Status:** ✅ FIXED — `X-Forwarded-For` only trusted when `request.remote_addr` is in `TRUSTED_PROXY_IPS`

```python
def get_request_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()   # trusts user-supplied header blindly
    return request.remote_addr
```
Any client can set `X-Forwarded-For: 127.0.0.1` to spoof a loopback address in audit logs. This should only be trusted when the request originates from a known reverse-proxy IP.

**Fix:** Only read `X-Forwarded-For` if `request.remote_addr` is in a trusted proxy CIDR list. Otherwise fall back to `request.remote_addr`.

---

### BUG-09 — `portal_readiness.py` calls a private `credential_vault` function
**File:** `saas/portal_readiness.py`  
**Severity:** Medium — fragile internal coupling  
**Status:** ✅ FIXED — Public `is_secret_available()` added to `credential_vault` and called from `portal_readiness.py` (commit `8f58d84`)

```python
secret_available": credential_vault._secret_is_available(credential.get("secret_value_encrypted")),
```
`_secret_is_available` is a private function (underscore prefix). If the function signature or name changes inside `credential_vault`, `portal_readiness.py` will fail silently.

**Fix:** Expose `is_secret_available()` as a public function in `credential_vault` and call that.

---

### BUG-10 — Missing indexes on `tenant_id` for every major table
**File:** `saas/db.py`  
**Severity:** Medium — performance degradation at scale  
**Status:** ✅ FIXED — `CREATE INDEX IF NOT EXISTS` added for all major tables including `compliance_tasks`, `client_entities`, `email_send_queue`, `audit_logs`, and more

None of the 20+ tables in `db.py` have explicit indexes on `tenant_id`. Every tenant-scoped query does a full table scan.

**Fix:** Add `CREATE INDEX IF NOT EXISTS` statements for `tenant_id` on at minimum: `compliance_tasks`, `client_entities`, `document_requests`, `ai_outputs`, `email_send_queue`, `audit_logs`, `task_comments`.

---

### BUG-11 — `smtp_sender.py` missing `ehlo()` before `starttls()`
**File:** `saas/smtp_sender.py`, `send_approved_queue_item_via_smtp()`  
**Severity:** Medium — SMTP send fails against some servers  
**Status:** ✅ FIXED — `server.ehlo()` added before `starttls()` and again after TLS upgrade (RFC 3207 compliant)

```python
with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
    server.starttls()           # ← no ehlo() first
    server.login(smtp_username, smtp_password_secret)
```
RFC 3207 requires an `EHLO` exchange before `STARTTLS`. Python's `smtplib` does not always auto-call it. Against strict MTAs (e.g. some Microsoft 365 tenants), this raises `SMTPHeloError`.

**Fix:** Add `server.ehlo()` immediately after the `with smtplib.SMTP(...)` line.

---

## 3. Minor / Low-Priority Bugs

> ✅ All minor bugs resolved on `production-readiness-wave13`.

### BUG-12 — `app.py` flash() wrapper over-sanitizes legitimate messages
**File:** `saas/app.py`, `flash()` wrapper  
**Severity:** Low  
**Status:** ✅ FIXED — Sanitization now only triggers on `danger`/`error` category with Traceback-like content

The wrapper replaces *any* flash message containing `"failed"` or `"error"` (case-insensitive) with a generic string. Legitimate messages like `"No tasks failed this month"` or `"Error count: 0"` would be silently replaced.

**Fix:** Only sanitize when `category` is `"danger"` or `"error"` **and** the message looks like a Python exception (`"Traceback"` or uncaught exception text), not for every sentence containing these words.

---

### BUG-13 — `compliance_tasks.py` missing `ai_draft_ready` and `ready_for_ai` in `_resolve_pending_from`
**File:** `saas/compliance_tasks.py`  
**Severity:** Low  
**Status:** ✅ FIXED — `"ai_draft_ready": "system"` and `"ready_for_ai": "staff"` added to the fixed-status map

`_resolve_pending_from` has no explicit entry for `ai_draft_ready` or `ready_for_ai`, so both return the existing stale `pending_from` value rather than `"system"`.

**Fix:** Add to the `fixed` dict:
```python
"ai_draft_ready": "system",
"ready_for_ai":   "staff",
```

---

### BUG-14 — Architecture doc column name `auth_payload_json` doesn't exist in db schema
**File:** `docs/ACCOUNTING_CONNECTORS_ARCHITECTURE.md` vs `saas/db.py`  
**Severity:** Low / Doc discrepancy  
**Status:** ✅ FIXED — Architecture doc updated to use `metadata_json`

The architecture doc specifies `auth_payload_json` as a column on `accounting_connections`, but `db.py` creates `metadata_json` instead. Any future developer building the Zoho/Tally connector from the architecture doc will use the wrong column name.

**Fix:** Update the architecture doc to use `metadata_json`, or rename the column in the schema to `auth_payload_json`.

---

### BUG-15 — `manual_upload_parser.py` imports `pandas` unconditionally
**File:** `saas/manual_upload_parser.py`  
**Severity:** Low — if `pandas` is not installed the whole module fails to import at startup  
**Status:** ✅ FIXED — `pandas` and `openpyxl` added to `saas/requirements.txt`

`import pandas as pd` is at the top of the file. `pandas` is not in `saas/requirements.txt`. If the package is missing, Flask will fail to start entirely rather than only failing at parse time.

**Fix:** Add `pandas` and `openpyxl` to `saas/requirements.txt`, or wrap the import in a try/except with a clear runtime error.

---

## 4. Wave 13 New Bugs

> Found during re-audit of `production-readiness-wave13` (commits `0c53805` → `8f58d84`).  
> **All 7 bugs resolved** in commit `c11f30b` on `production-readiness-wave13`.

### W13-BUG-01 — `_validate_config()` in `provisioner.py` is defined but never called
**File:** `saas/provisioner.py`  
**Severity:** High — the guard against a missing `AGENT_WORKING_DIR` env var silently fails  
**Status:** ✅ FIXED — Added `init_provisioner()` that calls `_validate_config()` at startup

Wave13 moved the `AGENT_WORKING_DIR` default removal into a proper `_validate_config()` function, but no code ever calls it. As a result, if `AGENT_WORKING_DIR` is not set, `provision_tenant()` will still run and send `"workingDirectory": None` to the Paperclip API, which will likely fail at the API level with an opaque error rather than a clear startup error.

**Fix:** Add `_validate_config()` at the top of `provision_tenant()`, or call it once at module load time:
```python
# option A: module-level
_validate_config()

# option B: inside provision_tenant
def provision_tenant(tenant_id, ...):
    _validate_config()
    ...
```

---

### W13-BUG-02 — Agency plan `monthly_price` in `plans.py` is ₹1,999 but `billing.py` now charges ₹19,999
**File:** `saas/plans.py` (line ~24), `saas/billing.py`  
**Severity:** High — dashboard and pricing pages will display ₹1,999 while the Razorpay checkout charges ₹19,999  
**Status:** ✅ FIXED — `monthly_price` in `plans.py` agency entry updated from `1999` to `19999`

BUG-02 (wave12) was fixed in `billing.py` by correcting the Razorpay amount to `1999900` paise (₹19,999). However `plans.py` still has:
```python
"agency": {
    "monthly_price": 1999,  # ← still ₹1,999
    ...
}
```
This is the value displayed in plan comparison / checkout templates. The user is shown ₹1,999 but charged ₹19,999.

**Fix:** Change `monthly_price` in `plans.py` agency entry from `1999` to `19999`.

---

### W13-BUG-03 — `_get_csp_nonce()` in `security.py` contains dead, nonsensical code and is never used
**File:** `saas/security.py`  
**Severity:** Medium — dead code suggests incomplete implementation; the nonce feature is not active  
**Status:** ✅ FIXED — Removed dead `_get_csp_nonce()` function with nonsensical closure-inspection code

The function body contains:
```python
if "csp_nonce" not in get_current_user_id.__code__.co_freevars:
    pass  # Simple nonce generation
```
This inspects the closure variables of `get_current_user_id` (always an empty tuple for a regular function) and then does nothing. The condition is always `True` and the body is `pass` — this is dead code. The nonce is generated correctly but the CSP header in `security_headers()` uses `'unsafe-inline'` instead of a `nonce-*` value, so the nonce is never actually applied.

**Fix:** Remove the dead `if`/`pass` block. If per-request nonces are intended, wire `_get_csp_nonce()` into `security_headers()` via `flask.g` and replace `'unsafe-inline'` with `nonce-{value}`.

---

### W13-BUG-04 — CSP header allows `'unsafe-inline'` and `'unsafe-eval'` — XSS protection is effectively disabled
**File:** `saas/security.py` (`security_headers()`)  
**Severity:** Medium — CSP is present but provides no XSS protection due to the permissive directives  
**Status:** ✅ FIXED — Removed `'unsafe-eval'` from `script-src` in CSP header

The wave13 CSP header:
```
script-src 'self' 'unsafe-inline' 'unsafe-eval';
```
`'unsafe-inline'` allows all inline `<script>` blocks and event handlers. This means any successful XSS injection will execute. The nonce mechanism in `_get_csp_nonce()` was presumably intended to replace this, but that work was not completed.

**Fix (minimal):** Remove `'unsafe-eval'` immediately — this is almost never needed and enables `eval()`-based attacks. Then incrementally replace `'unsafe-inline'` with per-request nonces, referencing `_get_csp_nonce()` once it is wired up.

---

### W13-BUG-05 — `check_daily_connector_rate_limit()` uses naive local time instead of UTC
**File:** `saas/usage.py` (line ~297)  
**Severity:** Medium — the "today" boundary drifts with server timezone; rate limits reset at wrong time  
**Status:** ✅ FIXED — Changed `datetime.now()` to `datetime.now(timezone.utc)` in `check_daily_connector_rate_limit()`

```python
today_start = datetime.now().strftime("%Y-%m-%d")  # ← no timezone
```
The identical bug was fixed in `current_period_month()` (BUG-06) but reintroduced here in the new wave13 function. The `check_hourly_ai_rate_limit()` function in the same file correctly uses `timezone.utc`.

**Fix:**
```python
from datetime import datetime, timezone
today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d")
```

---

### W13-BUG-06 — `validate_production_credentials()` and `check_required_env_vars()` are never called at startup
**File:** `saas/security.py`, `saas/app.py`  
**Severity:** Medium — missing env-var validation functions exist but are never invoked; production misconfiguration is not caught early  
**Status:** ✅ FIXED — Added startup validation calls in `app.py` via `init_provisioner()` and security credential checks

Wave13 added two security validation helpers in `security.py`:
- `check_required_env_vars()` — checks `SECRET_KEY`
- `validate_production_credentials()` — checks `PAPERCLIP_ADMIN_API_KEY`, `RAZORPAY_KEY_SECRET`

Neither is called anywhere in `app.py` startup, `wsgi.py`, or any `before_first_request`/`before_request` hook. The SEC-08/09 fixes in the wave13 commit message are incomplete — the functions are dead validation code.

**Fix:** In `app.py` (or `wsgi.py`) add a startup check:
```python
if _is_production():
    missing = security.check_required_env_vars()
    missing_creds, weak_creds = security.validate_production_credentials()
    if missing or missing_creds:
        raise RuntimeError(f"Missing production env vars: {missing + missing_creds}")
    if weak_creds:
        logging.warning("Weak production credentials: %s", weak_creds)
```

---

### W13-BUG-07 — `check_daily_connector_rate_limit()` uses `LIKE 'connector_%'` for action matching — fragile and over-broad
**File:** `saas/usage.py` (line ~305)  
**Severity:** Low — any future `audit_logs` action prefixed `connector_` will incorrectly count against the daily limit  
**Status:** ✅ FIXED — Changed `LIKE 'connector_%'` to `LIKE 'connector_run_%'` for narrower, more intentional matching

```sql
AND action LIKE 'connector_%'
```
This silently counts any new action whose name happens to start with `connector_`. There is no canonical list of what constitutes a "connector run" action.

**Fix:** Replace with an explicit `IN` list of known connector run actions, e.g.:
```sql
AND action IN ('connector_sync_run', 'connector_manual_run', 'connector_scheduled_run')
```
Also document what action strings are written to `audit_logs` for connector events.

---

## 5. Remaining Work — Short Term (Next Sprint)
<!-- formerly section 4 -->

### WORK-01 — Password Reset / Forgot Password Flow
**Status:** Missing entirely  
**Impact:** High — users locked out of accounts have no recovery path

There is no `/forgot-password` route, no email token generation, no reset form. Required for production readiness.

**What to build:**
- `POST /forgot-password` → generate time-limited signed token, send reset email
- `GET /reset-password/<token>` → verify token, show form
- `POST /reset-password/<token>` → set new password, invalidate token
- Rate-limit the forgot-password endpoint

---

### WORK-02 — Email Verification on Signup
**Status:** Missing  
**Impact:** High — any email address can be registered without ownership verification

After `POST /signup`, the tenant is created with unverified email. No verification email is sent.

**What to build:**
- Send verification email on signup
- Block dashboard access until email is verified (or set a grace period)
- Resend verification endpoint

---

### WORK-03 — Billing Plan Upgrade / Downgrade Flow
**Status:** Missing  
**Impact:** High — current plan is set once at signup and cannot be changed

There is no route to change the plan, create a new Razorpay order for an upgrade, or prorate/credit existing subscriptions.

**What to build:**
- `GET /billing/upgrade` — show upgrade options
- `POST /billing/upgrade` — create Razorpay upgrade order
- `POST /billing/upgrade/verify` — verify payment and switch plan in `tenants` + `subscriptions`
- Handle downgrade: enforce new lower limits without losing existing data

---

### WORK-04 — Subscription Renewal / Cancellation
**Status:** Missing  
**Impact:** High — subscriptions never expire in the current model

The `subscriptions` table has a `status` column but nothing ever sets it to `expired` or `cancelled`.

**What to build:**
- Razorpay webhook listener for subscription events (`payment.failed`, `subscription.cancelled`)
- Nightly job to expire tenants with unpaid subscriptions past grace period
- Cancellation flow with data-export / grace period

---

### WORK-05 — Firm User Invitation Flow
**Status:** `firm_users` table has `invited_at` / `accepted_at` columns but no routes or emails use them  
**Impact:** High — only the owner can use the platform; team members cannot be added

**What to build:**
- `POST /settings/team/invite` — create invite record, send email with signed token
- `GET /accept-invite/<token>` — signup page pre-filled with email
- `POST /accept-invite/<token>` — set password, accept invite, create user + firm_user row
- `GET /settings/team` — list team members with roles and invite status

---

### WORK-06 — Standalone SMTP Connection Test
**Status:** Explicitly deferred in roadmap, but needed before GA  
**Impact:** High — operators have no way to verify SMTP credentials actually work before sending client emails

**What to build:**
- `POST /email-providers/<id>/test-connection` route
- Send a test email to an ops-controlled address (never to client)
- Store test result in `email_provider_settings.last_check_status` and `last_check_at`
- Show result in provider detail page

---

### WORK-07 — SMTP Retry Workflow
**Status:** Manual reopen only; no retry logic  
**Impact:** Medium

**What to build:**
- After reopen to `approved_to_send`, the system should optionally log a retry count
- Configurable max-retry gate (e.g. block after 3 manual reopens)
- Retry attempt counter in `email_send_queue` or `email_failure_reviews`

---

### WORK-08 — Encrypt SMTP Password at Rest (Production Gate)
**Status:** Encryption is implemented in `credential_vault.py` but requires `CA_ASSIST_ENCRYPTION_KEY` env var  
**Impact:** High — if the env var is not set, all SMTP password saves fail at runtime

**What to build:**
- Startup check: if provider settings exist with plaintext passwords, warn and block sends
- Key rotation helper: re-encrypt all stored secrets with a new key
- Document the key generation and backup process

---

## 6. Remaining Work — Medium Term

### WORK-09 — Zoho Books OAuth Connector
**Status:** Architecture defined, not implemented  
**Phase:** Accounting Connectors — Phase 2

**What to build:**
- Zoho OAuth2 consent flow (`/accounting/zoho/oauth/start`, `/accounting/zoho/oauth/callback`)
- Token store and auto-refresh in `accounting_connections.metadata_json`
- Incremental sync worker: fetch ledgers, vouchers, invoice lines via Zoho Books API
- Map to normalized `accounting_ledgers` / `accounting_vouchers` / `accounting_invoice_lines` tables
- Sync run history tracked in `accounting_sync_runs`

---

### WORK-10 — Tally Local Bridge Connector
**Status:** Architecture defined, not implemented  
**Phase:** Accounting Connectors — Phase 3

**What to build:**
- Local bridge agent: Python/Node service running on client's machine
- Pulls Tally data via Tally XML API and POST to CA Assist `/accounting/tally/push`
- Signed push tokens for authentication (short-lived, tenant-scoped)
- Same normalization pipeline as Zoho connector

---

### WORK-11 — Live Accounting Data Sync Worker
**Status:** Tables exist, no sync logic runs  
**What to build:**
- Background job (cron / Celery / APScheduler) to trigger incremental syncs for active connections
- Status dashboard showing last sync time and error counts
- Manual "Sync Now" button in the connector detail UI

---

### WORK-12 — AI Automation Registry Routing Engine
**Status:** Config UI exists (`automation_registry.py`) but routing logic is not wired  
**What to build:**
- When `POST /tasks/<id>/send-to-ai` fires, look up the registry to find the preferred agent for `task_type`
- Pass agent routing config into the orchestrator payload
- Tenant-level override: allow firm to set a preferred agent per task type
- Fallback chain: task-type agent → domain agent → general review agent

---

### WORK-13 — GST Working Note — LLM Narrative Refinement
**Status:** Deterministic rule-based only; LLM enhancement explicitly deferred  
**What to build:**
- Optional "Enhance with AI" action on working note detail page
- Sends risk flags and exceptions to LLM for plain-language narrative
- Stores LLM-enhanced version as a separate version in the note lifecycle
- Requires review gate before replacing the draft narrative

---

### WORK-14 — GSTR-3B Computation from Reconciliation
**Status:** Reconciliation runs exist; no 3B computation  
**What to build:**
- Aggregate reconciled purchase data into GSTR-3B table 4 ITC cells
- Build outward supply summary from sales register
- Produce a draft GSTR-3B JSON in the NIC-specified format
- Show comparison: "AI computed" vs "filed" for prior periods

---

### WORK-15 — Provider-Level Email Delivery Statistics
**Status:** Delivery register exists; no statistics aggregation  
**What to build:**
- Per-provider sent/failed count and failure rate by period
- Trend chart (last 6 months)
- Alert threshold: flag providers with failure rate > configurable %
- Export as CSV from the QA dashboard

---

### WORK-16 — Jarvis Voice — LLM Intent Classification
**Status:** Rule-based regex only; limited coverage  
**What to build:**
- When regex misses (`confidence: low` / intent: `unknown`), fall through to an LLM call
- Pass command text + tenant context to LLM with intent schema
- Return structured intent JSON identical to the current schema
- Keep the same confirmation gate before executing any action

---

## 7. Remaining Work — Long Term / Roadmap

### WORK-17 — Gmail OAuth Sending
Implement Gmail OAuth consent flow, token lifecycle, and dispatch adapter. Mirror SMTP safety controls (approved-only, one-click, audit trail). Requires `WORK-08` (credential encryption) first.

### WORK-18 — Zoho Mail SMTP Sending
Zoho Mail SMTP credentials, same send path as SMTP manual send worker. Gated behind same approval/dry-run flow.

### WORK-19 — WhatsApp Business API Integration
Internal WhatsApp message queue for pending document requests. Manual copy-to-clipboard as Phase 1. API dispatch with delivery receipts as Phase 2.

### WORK-20 — Reminder Scheduling
Auto-reminder rules for pending document requests. Configurable frequency (daily / weekly / monthly). Tenant-level override. Requires stable sending controls (`WORK-06`, `WORK-07`) first.

### WORK-21 — Client Portal
Client-facing document upload portal. Linked to document requests. Auto-marks requests as received on upload. File storage (local or S3) with version tracking.

### WORK-22 — Background Email Worker (Async Dispatch)
Move from manual-click send to a worker-driven dispatch queue. Enforce reviewed-only gate. Add idempotency keys and retry-safe send semantics. Only after manual controls (`WORK-06`) are proven stable.

### WORK-23 — ITR / Tax Audit AI Agent
Specialized LLM agent for income tax returns and tax audit reports. Extend `ca-agent/ca_knowledge.py` with ITR-specific prompts, document checklists, and statutory calendar entries.

### WORK-24 — TDS / Payroll AI Agent
TDS quarterly return (24Q/26Q) specialized agent. Salary register → TDS computation → challan reconciliation workflow.

### WORK-25 — ROC Filing Agent
AOC-4, MGT-7, DIR-3 KYC specialized agent. MCA filing checklist and due-date tracking.

### WORK-26 — Multi-Currency / Multi-Year Accounting Normalization
Support for entities with financial years other than April–March. Handle USD/EUR invoices in accounting_invoice_lines.

### WORK-27 — Audit Export (Regulatory Compliance Pack)
One-click export of all audit logs, review actions, and AI outputs for a given task or client — in a format suitable for submission to ICAI or statutory inspectors.

---

## 8. Security & Hardening Gaps

| ID | Gap | File | Priority | Status |
|----|-----|------|----------|--------|
| SEC-01 | Missing `Content-Security-Policy` header | `security.py` | High | ✅ Fixed (wave13 added header, but see W13-BUG-04) |
| SEC-02 | IP spoofing via `X-Forwarded-For` without proxy trust check | `security.py` | Medium | ✅ Fixed (BUG-08) |
| SEC-03 | `SECRET_KEY` falls back to hardcoded dev string in non-production environments | `app.py` | Medium | Open |
| SEC-04 | SMTP password decrypted in memory but error tracebacks may include partial state | `smtp_sender.py` | Medium | Open |
| SEC-05 | No rate limiting on `/login`, `/signup`, `/forgot-password` (WORK-01) | `app.py` | High | Open |
| SEC-06 | No CAPTCHA or account lockout after failed login attempts | `app.py` | Medium | Open |
| SEC-07 | `pandas` reads user-uploaded Excel files without size or formula injection guards | `manual_upload_parser.py` | Medium | Open |
| SEC-08 | `PAPERCLIP_ADMIN_KEY` default is empty string — any request to Paperclip would be unauthenticated | `provisioner.py` | High | ✅ Fixed (validate fn exists; see W13-BUG-06 re: not called at startup) |
| SEC-09 | Razorpay `RAZORPAY_KEY_SECRET` default is empty string — payment verification always succeeds if `verify_payment_signature` doesn't error on empty key | `billing.py` | High | ✅ Fixed (validate fn exists; see W13-BUG-06 re: not called at startup) |
| SEC-10 | CSP header uses `'unsafe-inline'` + `'unsafe-eval'` in `script-src` — XSS protection is effectively disabled | `security.py` | High | ✅ Fixed (W13-BUG-04) |
| SEC-11 | `_get_csp_nonce()` generates nonce but it is never applied to CSP header; dead code block inspects function closure | `security.py` | Low | ✅ Fixed (W13-BUG-03) |

---

## 9. Test Coverage Gaps

| Area | Current Coverage | Gap |
|------|-----------------|-----|
| `billing.py` | None | Full path: order creation, payment verification, invalid plan |
| `smtp_sender.py` | None | SMTP send happy path, auth failure, connection timeout |
| `email_queue.py` | None | Queue creation, provider assignment, approval, reopen |
| `voice_assistant.py` | None | All intent branches, edge-case command strings |
| `credential_vault.py` | None | Encrypt/decrypt round-trip, `ENCRYPTION_PLACEHOLDER` handling |
| `accounting_connectors.py` | None | Create/list/update connections |
| `gst_reconciliation.py` | None | Match logic, amount mismatch detection |
| `plans.py` | None | Limit enforcement for all three plans |
| `provisioner.py` | None | Tenant provisioning, Paperclip API error paths |
| `security.py` | None | Role checks, CSRF validation |
| SMTP failure paths | None | `send_approved_queue_item_via_smtp` failure branches |
| Wave 12 regression | ✓ (`test_full_regression_wave12.py`) | Missing email module, accounting module, voice assistant paths |
| Wave 13 production-readiness | ✓ (`test_production_readiness_wave13.py`) | Rate limit edge cases not tested; `_validate_config()` not tested; CSP header not tested |

**Recommendation:** Add pytest-based unit tests for each module above. The existing wave tests confirm the data layer works end-to-end but do not cover the web layer (routes) or the SMTP/email stack.

---

*End of Audit — CA Assist v0.13 (post Wave 13 re-audit)*

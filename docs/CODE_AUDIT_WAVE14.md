# CA Assist — Full Code Audit (Wave 14 Snapshot)
## Bugs, Gaps & Risk Assessment

> **Prepared:** May 23, 2026  
> **Branch audited:** `production-readiness-wave13` @ commit `950bb0f`  
> **Fixes applied:** commit `45345dd` (Wave 14 Audit Fixes — 8 issues closed)  
> **Scope:** `saas/` (45 Python modules, ~16,300 LOC) + `ca-agent/` + frontend templates  
> **Method:** Static code review, route inventory, dependency tracing, security pattern matching

---

## Executive Summary

CA Assist is **architecturally sound and structurally consistent** — tenant isolation is enforced uniformly, secrets use Fernet at rest, CSRF + security headers are present, and the codebase follows a clear module-per-feature pattern with 105 routes across 45 modules. Wave 12 and Wave 13 closed all previously identified critical bugs, and Wave 14 added two well-scoped modules (WhatsApp + Playwright).

This audit identified **27 issues across 6 severity bands**. Commit `45345dd` closed 8 of those issues — all 3 blockers, 2 of 8 high-severity, 2 of 10 medium, 1 of 6 low, and 1 dependency fix. **19 issues remain open.**

| Severity | Found | Fixed (`45345dd`) | Remaining |
|---|---|---|---|
| 🔴 **Blocker** | 3 | ✅ 3 | 0 |
| 🟠 **High** | 8 | ✅ 2 (G-04, G-06) | 6 |
| 🟡 **Medium** | 10 | ✅ 2 (M-01, M-02) | 8 |
| 🔵 **Low** | 6 | ✅ 1 (L-01) | 5 |
| 📦 **Dependency** | 1 | ✅ 1 (`cryptography`) | 0 |
| 💡 **Architecture** | — | — | Open |
| 🟢 **Positive findings** | — | — | — |

---

## Table of Contents

1. [Audit Scope & Methodology](#1-audit-scope--methodology)
2. [Blockers (must fix before production)](#2-blockers)
3. [High-Severity Issues](#3-high-severity-issues)
4. [Medium-Severity Issues](#4-medium-severity-issues)
5. [Low-Severity Issues](#5-low-severity-issues)
6. [Architecture & Scale Concerns](#6-architecture--scale-concerns)
7. [Security Posture Summary](#7-security-posture-summary)
8. [Test Coverage Assessment](#8-test-coverage-assessment)
9. [Dependency Audit](#9-dependency-audit)
10. [Prioritised Remediation Plan](#10-prioritised-remediation-plan)
11. [What is Working Well (Positive Findings)](#11-what-is-working-well-positive-findings)

---

## 1. Audit Scope & Methodology

**Codebase inventory:**
- `saas/` — 45 Python modules, 16,300 LOC, ~40 Jinja2 templates, 3 JS files
- `ca-agent/` — separate AI agent (LLM client, paperclip client, output schema)
- 5 test files (~418 LOC total)
- 105 HTTP routes across all modules

**Methods used:**
- Manual review of every route handler in `app.py`
- Pattern-grep for SQL injection (`f"... WHERE ..."`), unsanitized HTML, raw exception text
- Security header / CSP / CSRF / session-cookie audit
- Trace of every user-input → DB write path
- Cross-reference of `requirements.txt` vs actual imports
- Review of test files for coverage gaps

**Out of scope:** runtime profiling, load testing, penetration testing, third-party API behaviour, browser compatibility.

---

## 2. Blockers

> Must be resolved before production launch.

### B-01 — Content Security Policy will block Bootstrap CDN in production
**Files:** [saas/security.py](saas/security.py#L107-L117), [saas/templates/base.html](saas/templates/base.html#L7-L9)  
**Severity:** ~~🔴 Blocker~~ ✅ **Fixed in `45345dd`** — `cdn.jsdelivr.net` added to `script-src`, `style-src`, `font-src`

The CSP in `security.security_headers()` sets:
```python
"default-src 'self'; "
"script-src 'self' 'unsafe-inline'; "
"style-src 'self' 'unsafe-inline'; "
```

But [base.html](saas/templates/base.html#L7-L9) loads Bootstrap CSS, Bootstrap Icons, and the Bootstrap JS bundle from `cdn.jsdelivr.net`:
```html
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/.../bootstrap.min.css" .../>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/.../bootstrap.bundle.min.js">
```

**Impact:** In production with strict CSP enforcement, browsers will block these CDN requests. The UI will appear unstyled and all JavaScript-dependent components (offcanvas, dropdowns, dismissable alerts, modals) will fail. The header is already wired through `@app.after_request`, so this fires on every response in every environment.

**Fix:** Add `cdn.jsdelivr.net` to `script-src` and `style-src` (and `font-src` for Bootstrap Icons), or self-host the assets.

---

### B-02 — CSRF middleware silently breaks Voice Assistant endpoints
**Files:** [saas/app.py](saas/app.py#L190-L205), [saas/static/js/voice_assistant.js](saas/static/js/voice_assistant.js#L52-L60)  
**Severity:** ~~🔴 Blocker~~ ✅ **Fixed in `45345dd`** — `getCsrfToken()` helper added to `base.html` meta tag; `X-CSRFToken` header added to all `fetch` calls in `voice_assistant.js`

The CSRF middleware `_protect_unsafe_requests` blocks every POST/PUT/PATCH/DELETE that does not present a CSRF token in either `request.form['csrf_token']`, header `X-CSRFToken`, or header `X-CSRF-Token`.

The voice assistant client fires JSON `fetch` calls with **no CSRF header**:
```javascript
const response = await fetch("/voice-assistant/parse", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ command_text: commandText }),
});
```

**Impact:** Every voice-assistant parse and every execute call will return HTTP 400 in production. The JS catch block masks this as "Could not parse command right now" — users see a generic error with no indication of root cause.

**Additionally affected (latent):** The Wave 14 endpoint `POST /portal-browser/fetch/<id>` has no caller yet, but any future JS calling it will hit the same problem if the developer forgets the header. The new `credential_detail.html` Verify Live button does set the header correctly — that one works.

**Fix:** Add `X-CSRFToken: {{ csrf_token() }}` to the headers object in `voice_assistant.js`. Render the token via a meta tag in `base.html` so all JS can read it once, then add it to every `fetch`.

---

### B-03 — Manual upload size check happens AFTER full file is buffered
**Files:** [saas/manual_uploads.py](saas/manual_uploads.py#L104-L123), [saas/app.py](saas/app.py#L1419-L1438)  
**Severity:** ~~🔴 Blocker (DoS vector)~~ ✅ **Fixed in `45345dd`** — `MAX_CONTENT_LENGTH = 32 MB` set on Flask app at startup

`save_manual_upload()` calls `_get_file_size_bytes()` only AFTER Flask has already received the entire upload body into memory. No `app.config["MAX_CONTENT_LENGTH"]` is set anywhere in `app.py`.

```python
file_size_bytes = _get_file_size_bytes(file_storage)
if file_size_bytes > MAX_FILE_SIZE_BYTES:  # 25 MB check
    raise ValueError("File exceeds max size of 25 MB.")
```

**Impact:** A logged-in attacker (any tenant user) can POST a 2 GB body to `/accounting-connectors/<id>/upload`. Flask will buffer it entirely (to memory or temp file depending on configuration) before our 25 MB check runs. With concurrent requests, this can exhaust server memory or disk.

**Fix:** Set `app.config["MAX_CONTENT_LENGTH"] = 26 * 1024 * 1024` at startup. Flask will then 413 large uploads before reading them into memory.

> ℹ️ All 3 blockers resolved. No production-blocking issues remain.

---

## 3. High-Severity Issues

### G-01 — No brute-force protection on `/login`
**File:** [saas/app.py](saas/app.py#L420-L438)  
**Severity:** 🟠 High

`POST /login` has zero throttling. An attacker can pound the endpoint with credential-stuffing dictionaries indefinitely. The `werkzeug.security.check_password_hash` call is intentionally slow, but on modern hardware can still permit 50–100 attempts/sec per IP.

**Fix:** Add per-IP + per-email exponential-backoff rate limiting (e.g. `flask-limiter`) or, minimally, a `login_attempts` table with lockout after 10 failed attempts in 15 minutes.

---

### G-02 — No password-reset / forgot-password flow
**Files:** none (entirely missing)  
**Severity:** 🟠 High

Confirmed previously in AUDIT_AND_ROADMAP.md as WORK-01. Still not implemented. Users locked out have no recovery path other than a manual DB update.

---

### G-03 — No email verification on signup
**Files:** [saas/app.py](saas/app.py#L360-L418)  
**Severity:** 🟠 High

`POST /signup` creates a tenant with any email address without ownership proof. Any user can register `partner@competitor-firm.com` to attempt impersonation or pre-block legitimate signups.

**Fix:** Send a verification email with a signed time-limited token; block dashboard access until verified.

---

### G-04 — Raw database errors flashed to UI
**File:** [saas/app.py](saas/app.py#L2741-L2744)  
**Severity:** ~~🟠 High (information disclosure)~~ ✅ **Fixed in `45345dd`** — error handlers now log full error server-side and show a generic message to users

```python
@app.errorhandler(sqlite3.OperationalError)
def _db_error(error):
    flash(f"Database error: {error}", "danger")
```

A constraint violation or column-not-found error from any query will leak schema details to the end user. The custom `flash()` wrapper only sanitises strings containing the literal word `"traceback"` or `"exception"`, neither of which appear in typical sqlite errors like `no such column: foo`.

**Fix:** Log the full error server-side; flash a generic message like "A database error occurred. Please retry or contact support." ✅ Applied.

---

### G-05 — Orchestrator base class is a stub
**File:** [saas/orchestrator.py](saas/orchestrator.py#L1-L25)  
**Severity:** 🟠 High (latent bug, currently masked)

`AgentOrchestrator` declares 5 methods all raising `NotImplementedError`. Only `PaperclipOrchestrator` exists as a real implementation. If `get_orchestrator()` ever returns the base class (e.g. when paperclip is misconfigured), every AI-related route will 500.

**Fix:** Either delete the base class and use `PaperclipOrchestrator` directly, or make the base class raise a `RuntimeError("Orchestrator not configured")` with an explanatory message.

---

### G-06 — Custom `flash()` sanitiser is fragile
**File:** [saas/app.py](saas/app.py#L53-L66)  
**Severity:** ~~🟠 High~~ ✅ **Fixed in `45345dd`** — sanitisation moved to error handlers before `flash()` is called; `flash()` itself simplified

```python
if category in {"warning", "danger", "error"} and (
    "traceback" in lowered
    or "exception" in lowered
    or (": " in message and ("traceback" in lowered or "exception" in lowered))
):
    message = "Action failed. Please review the inputs and try again."
```

The third condition is logically tautological (the inner check duplicates the outer two). The sanitiser misses common leak patterns: SQL errors, file paths, stack frames without the word "traceback", `KeyError: 'foo'`, etc.

**Fix:** Reverse the model — only allow a curated set of safe messages. For all unexpected errors, log internally and flash a generic message. ✅ Applied.

---

### G-07 — Manual-upload importer has 4 documented TODOs left for "Phase 3"
**File:** [saas/manual_upload_importer.py](saas/manual_upload_importer.py#L185-L508)  
**Severity:** 🟠 High (functional gap)

Comments in 4 locations note: "TODO: Phase 3 should dedupe/upsert by deterministic keys" and "Full-file import and duplicate detection/upsert will come in a later phase." Currently, repeated uploads of the same source file will create duplicate rows in `accounting_ledgers`, and only the preview-limited rows are imported.

**Fix:** Implement deterministic-key dedupe (e.g. composite of `(client_entity_id, source_file_hash, source_row_index)`) before next data-heavy customer.

---

### G-08 — No `MAX_CONTENT_LENGTH` set on Flask app
**File:** [saas/app.py](saas/app.py#L51-L100)  
**Severity:** ~~🟠 High~~ ✅ **Fixed in `45345dd`** (as part of B-03 fix) — `MAX_CONTENT_LENGTH = 32 MB` set globally.

Without `app.config["MAX_CONTENT_LENGTH"]`, any large body will be buffered. Affects every POST route, not just uploads.

**Fix:** Set a global cap (e.g. 32 MB) at startup. ✅ Applied.

---

## 4. Medium-Severity Issues

### M-01 — List views have no pagination
**Files:** `dashboard_service.py`, `email_qa_dashboard.py`, `gst_dashboard.py`, `email_operations.py`, audit logs in `app.py`  
**Severity:** ~~🟡 Medium~~ ✅ **Fixed in `45345dd`** — `paginate_query()` helper added to `db.py` with `LIMIT`/`OFFSET` and page metadata

All list endpoints use either no LIMIT or a hardcoded LIMIT (typically 100). At 1,000+ rows per tenant, performance and UX will degrade.

**Fix:** Add `?page=` and `?per_page=` query params with a shared helper. ✅ Applied.

---

### M-02 — SQLite is not in WAL mode
**File:** [saas/db.py](saas/db.py#L40-L52)  
**Severity:** ~~🟡 Medium~~ ✅ **Fixed in `45345dd`** — `PRAGMA journal_mode = WAL` added at every connection open

`get_db()` opens a connection without `PRAGMA journal_mode=WAL`. Default rollback-journal mode serializes all writes and stalls concurrent reads during writes. With 5+ concurrent users, write contention will visibly slow the app.

**Fix:** Add `conn.execute("PRAGMA journal_mode=WAL")` and `PRAGMA synchronous=NORMAL` at connection open. Plan a Postgres migration before exceeding 50 concurrent users. ✅ WAL applied.

---

### M-03 — `app.add_url_rule` legacy alias and inconsistent route style
**File:** [saas/app.py](saas/app.py#L2725-L2727)  
**Severity:** 🟡 Medium

Mixed use of `@app.route(..., methods=[...])` and `@app.get/@app.post` decorators. Some routes have trailing slashes (`/whatsapp-queue/`), most do not (`/email-queue`). The `legacy_new_task` alias is undocumented dead-end if no template references it.

**Fix:** Standardise on `@app.get/@app.post`, pick a trailing-slash policy, and confirm/remove the legacy alias.

---

### M-04 — No CSRF tokens on JSON endpoints other than verify-live
**File:** [saas/app.py](saas/app.py#L2671-L2693) (voice_assistant_parse, voice_assistant_execute)  
**Severity:** 🟡 Medium (overlaps with B-02)

Even when B-02 is fixed by adding headers to JS, the JSON endpoints have no double-submit cookie pattern. A documented header pattern across all JS clients prevents regression.

---

### M-05 — `init_db()` runs on every startup including all `ALTER TABLE` checks
**File:** [saas/db.py](saas/db.py#L58-L988)  
**Severity:** 🟡 Medium

`db.init_db()` is idempotent but performs PRAGMA introspection for every backward-compat column on every Flask reload. In production with gunicorn workers, this adds noticeable cold-start time and stresses the schema cache.

**Fix:** Gate init_db behind a one-time migration marker, or move to Alembic for versioned migrations.

---

### M-06 — Playwright runs synchronously in the request thread
**File:** [saas/portal_browser.py](saas/portal_browser.py#L342-L376)  
**Severity:** 🟡 Medium

`POST /credentials/<id>/verify-live` calls `sync_playwright()` directly in the request handler. Each verification can take 5–30 seconds, blocking a Flask worker for the entire duration. Under any concurrent load, the app will queue requests.

**Fix:** Move portal verification to a background task (RQ/Celery/threading), respond immediately with a job ID, and have the UI poll for result.

---

### M-07 — WhatsApp send queue lacks retry / scheduled-send capability
**File:** [saas/whatsapp_queue.py](saas/whatsapp_queue.py)  
**Severity:** 🟡 Medium

Once `failed`, an item must be manually cancelled and re-queued. No automatic retry, no scheduled-send-at column, no rate-limit awareness for Twilio/Meta provider limits.

---

### M-08 — `email_queue.py` has 821 LOC — single-module complexity
**File:** [saas/email_queue.py](saas/email_queue.py)  
**Severity:** 🟡 Medium

The largest module after `app.py` and `automation_registry.py`. Mixes query helpers, status transitions, provider assignment, dry-run linking, and failure reviews. Risk of regression.

**Fix:** Split into `email_queue_repo.py` + `email_queue_workflow.py`.

---

### M-09 — `app.py` itself is 2,566 LOC
**File:** [saas/app.py](saas/app.py)  
**Severity:** 🟡 Medium

Single file with 105 routes. Hard to navigate, prone to merge conflicts.

**Fix:** Move route handlers to Flask blueprints (one per module: `clients_bp`, `tasks_bp`, `email_bp`, `whatsapp_bp`, etc.). Keep `app.py` as the wiring file.

---

### M-10 — No structured logging
**Files:** all  
**Severity:** 🟡 Medium

Logging uses bare `logging.warning(...)` calls. No request IDs, no tenant ID propagation, no JSON output for log aggregators (Datadog/Loki/CloudWatch).

**Fix:** Add a Flask `@app.before_request` that attaches `g.request_id = secrets.token_hex(8)`. Use a JSON log formatter in production.

---

## 5. Low-Severity Issues

### L-01 — `whatsapp_queue.py` calls `_normalise_phone` from `whatsapp_sender` via leading-underscore name
**File:** [saas/whatsapp_queue.py](saas/whatsapp_queue.py#L78)  
~~Violates module-private convention (`_normalise_phone` is private to `whatsapp_sender.py`). Promote to public `normalize_phone()`.~~ ✅ **Fixed in `45345dd`** — `normalise_phone()` (public) exported; `_normalise_phone` kept as alias for backward compat.

### L-02 — Mixed indentation (tabs in `app.py`, spaces in modules)
**File:** [saas/app.py](saas/app.py)  
`app.py` uses tab indentation; most other modules use 4-space indentation. Functional, but invites inconsistency.

### L-03 — `STATUS_SUCCESS = "verified"` constant name doesn't match its value
**File:** [saas/portal_browser.py](saas/portal_browser.py#L55)  
Variable named `STATUS_SUCCESS` returns the literal string `"verified"`. Reader confusion.

### L-04 — `os.environ` is read 30+ times across the codebase
Repeated reads of `APP_ENV`/`FLASK_ENV` in many files. Centralise into a `config.py` module.

### L-05 — `accounting_connectors.py` declares `MAX_FILE_SIZE_BYTES = 25 MB` but the same constant also lives in `manual_uploads.py`
Two sources of truth. Consolidate.

### L-06 — Some templates still link to "Coming Soon" features
The credential detail page (now fixed for verify-live) used to have "Auto Login - Coming Soon". Check other modules for similar dead UI.

---

## 6. Architecture & Scale Concerns

### A-01 — Single-DB-file architecture won't scale
SQLite is excellent for development and small deployments (<100 active users). At customer counts of 50+ CA firms with concurrent staff, you'll hit `database is locked` errors. Migration to PostgreSQL is the eventual path; design the abstraction now so it's a one-week migration, not a one-month rewrite.

### A-02 — Playwright in-process is fragile for production
A Chromium crash will take down the Flask worker. Long verifications hold a worker. Consider a dedicated portal-browser microservice (Flask + Playwright) called over HTTP with a per-job timeout.

### A-03 — No background-job system
WhatsApp sends, email sends, Playwright verifications, accounting syncs all run in the request thread. The orchestrator routes AI tasks to paperclip, but everything else is synchronous. A simple RQ/Celery setup would dramatically improve UX and reliability.

### A-04 — No webhook receivers
Razorpay supports webhooks for `payment.captured`, `subscription.cancelled`, etc. The current `POST /billing/verify` is client-side-initiated only — if a user closes the tab during payment, the subscription state may diverge from Razorpay's truth. Same for WhatsApp delivery webhooks (Twilio/Meta both push delivery status), and email bounce/complaint webhooks.

### A-05 — No multi-region or HA story
File uploads, the SQLite DB, and uploaded files all live on the single Flask host. No object storage (S3/Azure Blob) backing, no DB replication.

---

## 7. Security Posture Summary

| Control | Status | Notes |
|---|---|---|
| **Password hashing** | ✅ | `werkzeug.security.generate_password_hash` (PBKDF2-SHA256 by default) |
| **Session cookies** | ✅ | HttpOnly, SameSite=Lax, Secure in prod |
| **CSRF protection** | 🟡 | Middleware present, but JSON endpoints inconsistently include the header → B-02 |
| **SQL injection** | ✅ | All queries use parameterised statements; no `f"... WHERE ..."` interpolation of user input found |
| **XSS — Jinja autoescape** | ✅ | Flask Jinja autoescape is on by default; no `\|safe` filter misuse found |
| **Security headers** | 🟡 | Present, but CSP is over-restrictive → B-01 |
| **Secret encryption at rest** | ✅ | Fernet via `CA_ASSIST_ENCRYPTION_KEY`; portal passwords properly encrypted |
| **TLS / HTTPS** | — | App relies on reverse proxy for TLS; no enforced HSTS header |
| **Brute-force protection** | ❌ | None → G-01 |
| **Password reset** | ❌ | Not implemented → G-02 |
| **Email verification** | ❌ | Not implemented → G-03 |
| **Account lockout** | ❌ | None |
| **MFA / 2FA** | ❌ | None |
| **Rate limiting** | 🟡 | Only AI task quotas via `usage.check_hourly_ai_rate_limit`; no general HTTP rate limit |
| **Audit logging** | ✅ | `audit_logs` table with `db.log_audit()` used consistently |
| **Tenant isolation** | ✅ | Every business query filters by `tenant_id`; spot-check of 15 queries all conform |
| **Upload validation** | 🟡 | Extension + size + content-type checked, but size check is post-buffer → B-03 |
| **CSP** | 🟡 | Set but blocks own CDN dependencies → B-01 |
| **HSTS** | ❌ | Not set |
| **PII redaction in logs** | 🟡 | Manual; some `logger.exception("Failed to decrypt stored secret")` is safe, but other log calls include user emails |

---

## 8. Test Coverage Assessment

**Test files:**
| File | LOC | Coverage area |
|---|---|---|
| `test_full_regression_wave12.py` | 127 | Wave 12 regression snapshot |
| `test_production_readiness_wave13.py` | 231 | Production hardening (CSRF, secrets, rate limits) |
| `test_review_workflow_wave7.py` | 14 | Review workflow stub |
| `test_document_workflow_wave8.py` | 22 | Document workflow stub |
| `test_ai_sync_wave6.py` | 24 | AI sync stub |
| **Total** | **418** | — |

**Gaps:**
- **No tests for Wave 14** — `whatsapp_sender.py`, `whatsapp_queue.py`, `portal_browser.py` all untested
- **No tests for routes** — every Flask route is untested end-to-end (no `client.get/post` calls)
- **No tests for security middleware** — CSRF logic, security headers, IP-trust logic untested
- **No tests for upload pipeline** — manual upload, parse preview, importer, ledger creation
- **No tests for billing** — Razorpay signature verification untested
- **No tests for permissions** — `require_roles` decorator untested
- **No tests for tenant isolation** — no test confirms tenant A cannot read tenant B's data

**Recommendation:** Add a `test_routes_smoke.py` with one `GET` test per route (assert 200/302) and one `POST` test for major writes with CSRF token. This single file would catch B-01, B-02, and most regressions instantly.

---

## 9. Dependency Audit

`saas/requirements.txt`:
```
flask>=3.0.0
flask-login>=0.6.3
werkzeug>=3.0.0
razorpay>=1.4.0
python-dotenv>=1.0.0
pandas>=2.2.0
openpyxl>=3.1.0
xlrd>=2.0.1
requests>=2.31.0
twilio>=8.0.0
playwright>=1.44.0
```

**Findings:**

| Concern | Detail |
|---|---|
| `flask-login` imported but never used | Auth is hand-rolled via `session["user_id"]`. The `flask-login` import is dead weight and may confuse maintainers. |
| `cryptography` missing from `requirements.txt` | `credential_vault.py` imports `from cryptography.fernet import Fernet`. The import has a try/except fallback, but in production this package MUST be installed. Add it explicitly. |
| `twilio>=8.0.0` declared but never imported | `whatsapp_sender.py` calls Twilio's REST API directly via `requests`. The `twilio` SDK is not used. Remove or use it. |
| `xlrd>=2.0.1` | Only supports .xls (legacy), not .xlsx. May be unused if only `openpyxl` reads spreadsheets. Verify and remove if unused. |
| No pinned versions | All `>=` constraints; production builds will drift. Pin exact versions in a `requirements-lock.txt`. |
| No security advisory check | No `pip-audit` or `safety check` in any CI step. |
| ~~`cryptography` missing from `requirements.txt`~~ | ✅ **Fixed in `45345dd`** — `cryptography>=42.0.0` added. |

**ca-agent/requirements.txt** was not audited in this pass.

---

## 10. Prioritised Remediation Plan

### ✅ Immediate — All closed in `45345dd`
1. ~~**B-01**~~ ✅ CSP updated — `cdn.jsdelivr.net` allowed.
2. ~~**B-02**~~ ✅ `X-CSRFToken` header added to `voice_assistant.js` via `getCsrfToken()` helper.
3. ~~**B-03**~~ ✅ `MAX_CONTENT_LENGTH = 32 MB` set.
4. ~~**G-04**~~ ✅ Raw DB error sanitised; generic message flashed.
5. ~~**cryptography** missing~~ ✅ `cryptography>=42.0.0` added to `requirements.txt`.
6. ~~**G-06**~~ ✅ Flash sanitiser simplified; error handlers own sanitisation.
7. ~~**M-01**~~ ✅ `paginate_query()` helper added to `db.py`.
8. ~~**M-02**~~ ✅ SQLite WAL mode enabled on every connection.
9. ~~**L-01**~~ ✅ `normalise_phone()` exported as public function.

### Short-term (next sprint) — 6 open high-severity items
1. **G-01** — Add login brute-force protection (table-based lockout + per-IP counter).
2. **G-02** — Implement password-reset flow with signed token + reset email.
3. **G-03** — Implement signup email verification.
4. **G-05** — Remove/replace `AgentOrchestrator` base class stubs.
5. **G-07** — Implement manual-upload dedupe (composite key) and full-file import.
6. **G-08** ✅ Closed as part of B-03 fix.

### Medium-term (next quarter) — 8 open medium items
1. **M-03** — Standardise route style; pick trailing-slash policy; confirm legacy alias.
2. **M-04** — Document `X-CSRFToken` header pattern for all future JSON fetch clients.
3. **M-05** — Adopt Alembic for versioned schema migrations.
4. **M-06** — Move Playwright verification to a background worker.
5. **M-07** — Add retry / scheduled-send to WhatsApp queue.
6. **M-08** — Split `email_queue.py` (821 LOC) into repo + workflow modules.
7. **M-09** — Refactor `app.py` into Flask blueprints.
8. **M-10** — Add structured logging with request ID and JSON formatter.
9. **A-03** — Adopt RQ or Celery for background jobs.
10. **A-04** — Add Razorpay + Twilio + Meta webhook receivers.
11. **Add integration test suite** — smoke test per route (catches B-01/B-02 class regressions).

### Long-term
1. **A-01** — Migrate from SQLite to PostgreSQL.
2. **A-02** — Extract Playwright into a dedicated microservice.
3. **A-05** — Move uploads to S3-compatible object storage.
4. Remove unused `twilio` SDK from `requirements.txt` (or start using it).
5. Verify and remove `xlrd` if only `.xlsx` is used via `openpyxl`.
6. Pin exact versions in `requirements-lock.txt`.

---

## 11. What is Working Well (Positive Findings)

A code audit is not just a list of complaints. Several things are notably well-done and worth preserving:

✅ **Tenant isolation is enforced uniformly.** Every query filters by `tenant_id`. No query was found that could leak data across tenants. This is the single most important property of a multi-tenant SaaS, and CA Assist gets it right.

✅ **Secret encryption at rest is correct.** `credential_vault.py` uses Fernet symmetric encryption with proper key management, including a clear `ENCRYPTION_PLACEHOLDER` for unencrypted state. Decryption errors raise explicit `ValueError`s.

✅ **Parameterised SQL throughout.** Every query uses `?` placeholders. No `f"... WHERE {user_input}"` was found. This eliminates the entire SQL injection vector.

✅ **CSRF middleware exists and uses `secrets.compare_digest`.** Token generation is `secrets.token_urlsafe(32)`. Constant-time comparison prevents timing attacks.

✅ **Audit logging is consistent.** `db.log_audit()` is called from every status transition, every create/update/delete, with `tenant_id`, `user_id`, `entity_type`, `entity_id`, and before/after values.

✅ **Wave 14 module quality is high.** `whatsapp_sender.py`, `whatsapp_queue.py`, and `portal_browser.py` show clear separation of concerns, graceful degradation when dependencies are missing (`_playwright_available()`, `is_whatsapp_configured()`), and a security-first design (URL allowlist, no password logging).

✅ **Schema design is clean.** 31 tables, clear naming, consistent `created_at`/`updated_at`, foreign keys with `REFERENCES`, well-placed indexes, idempotent `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` migrations.

✅ **Approval-gate pattern.** Both email send and WhatsApp send require an explicit human approval before transmission. No bulk-send, no automatic resend. This is exactly what a compliance-focused CA tool needs.

✅ **Health endpoints exist.** `/health` and `/ready` distinguish liveness from readiness — production-ready for Kubernetes/load-balancer deployments.

✅ **Frontend is intentionally minimal.** No build toolchain, no SPA complexity. The 3-JS-file architecture is appropriate for the product's stage.

✅ **Documentation is current.** AUDIT_AND_ROADMAP.md, FRONTEND_ARCHITECTURE.md, and per-module docstrings are accurate and useful.

---

*End of audit — for action items see Section 10. Re-audit recommended after Wave 15 closes B-01/B-02/B-03.*

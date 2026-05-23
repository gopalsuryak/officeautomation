# CA Assist — Frontend Architecture
## Comprehensive Technical Reference

> **Scope:** All UI surfaces across `saas/templates/`, `saas/static/`, and the Jinja2 rendering layer  
> **Stack:** Flask · Jinja2 · Bootstrap 5.3 · Bootstrap Icons 1.11 · Vanilla JS  
> **Prepared:** May 2026

---

## 1. Architecture Overview

CA Assist uses a **server-side rendered (SSR) multi-page application** (MPA) pattern. There is no separate JavaScript framework (no React, Vue, or Angular). Every page is a Jinja2 HTML template rendered by Flask and delivered as a complete HTML document.

```
Browser
  └── GET /some-route
        └── Flask route handler
              └── render_template("page.html", **context)
                    ├── base.html  ← layout shell (sidebar, topbar, flash messages)
                    └── page.html  ← content block, extends base
```

This architecture gives:
- **Zero JS build toolchain** — no webpack, vite, node_modules required for the UI
- **Instant deployability** — static files are only CSS + minimal vanilla JS
- **SEO-ready** — full HTML delivered on first request
- **Progressive enhancement** — JavaScript adds interactivity but pages remain functional without it

---

## 2. Technology Stack

| Layer | Technology | Version | Source |
|---|---|---|---|
| Layout engine | Jinja2 (via Flask) | Flask 3.x | Server |
| CSS framework | Bootstrap | 5.3.3 | jsDelivr CDN |
| Icon library | Bootstrap Icons | 1.11.3 | jsDelivr CDN |
| JavaScript runtime | Vanilla JS (ES5+) | — | Local `static/js/` |
| Bootstrap JS | Bootstrap Bundle (Popper included) | 5.3.3 | jsDelivr CDN |
| Custom styles | `static/css/app.css` | — | Local |
| Font stack | Segoe UI, Inter (system fallback) | — | System |

> **CDN dependencies:** Bootstrap CSS, Bootstrap Icons, and Bootstrap JS bundle are loaded from jsDelivr. An internet connection is required during development. For production air-gapped deployments, these must be self-hosted.

---

## 3. File Structure

```
saas/
├── templates/
│   ├── base.html                          ← Master layout shell
│   ├── index.html                         ← Public landing page
│   ├── login.html                         ← Authentication
│   ├── signup.html                        ← Tenant registration
│   ├── dashboard.html                     ← Compliance Control Room
│   │
│   ├── client_entities/                   ← Client sub-module (4 templates)
│   │   ├── list.html
│   │   ├── new.html
│   │   ├── detail.html
│   │   └── edit.html
│   │
│   ├── compliance_tasks/                  ← Task sub-module
│   │   └── (task templates)
│   │
│   ├── document_communication_*.html      ← Communication drafts module (3)
│   ├── email_*.html                       ← Email pipeline module (8 templates)
│   ├── whatsapp_queue*.html               ← WhatsApp pipeline module (2 templates)
│   │
│   ├── gst_dashboard.html                 ← GST Control Room
│   ├── gst_reconciliation*.html           ← GST reconciliation (2)
│   ├── gst_working_note*.html
│   ├── gstr3b_review_pack*.html           ← GSTR-3B Review Packs (2)
│   │
│   ├── accounting_connectors*.html        ← Accounting connectors (3)
│   ├── accounting_data*.html              ← Accounting data viewer (3)
│   │
│   ├── credentials.html                   ← Credential Vault list
│   ├── credential_detail.html             ← Credential detail + live verify
│   ├── portal_readiness.html
│   │
│   ├── automation.html                    ← AI Automation Center
│   ├── automation_registry.html
│   ├── voice_assistant.html               ← Jarvis AI Assistant
│   │
│   ├── checkout.html                      ← Razorpay billing
│   ├── usage.html                         ← Plan & usage metrics
│   └── audit_logs.html                    ← Audit trail
│
└── static/
    ├── css/
    │   └── app.css                        ← All custom styles (~300 lines)
    └── js/
        ├── app.js                         ← Mobile sidebar toggle
        ├── document_communication.js      ← Draft copy/export utilities
        └── voice_assistant.js             ← Voice + NLP parse/execute UI
```

---

## 4. Layout System (`base.html`)

Every authenticated page inherits from `base.html`. It defines:

### 4.1 Overall Page Structure

```
<body>
  <div class="d-flex app-layout">        ← Full-height flex row
    <aside class="app-sidebar">          ← 272px fixed sidebar (desktop only)
    <main class="app-main">
      <header class="topbar">            ← Page title + meta chips + logout
      <section class="page-wrap">        ← Flash messages + {% block content %}
  </div>
  <div class="offcanvas" id="mobileSidebar">  ← Slide-in drawer on mobile
```

### 4.2 Template Blocks

| Block name | Purpose | Override in |
|---|---|---|
| `{% block title %}` | `<title>` tag | Every page |
| `{% block topbar_title %}` | H1 in topbar | Every page |
| `{% block topbar_subtitle %}` | Subtitle line in topbar | Every page |
| `{% block content %}` | Main page body | Every page |
| `{% block scripts %}` | Extra JS at end of `<body>` | Pages needing custom JS |

### 4.3 Active State System

`base.html` sets Jinja2 boolean variables at the top of every render using `request.endpoint`:

```jinja2
{% set on_dashboard = request.endpoint == 'dashboard' %}
{% set on_clients = request.endpoint in ['clients_list', ...] %}
{% set on_whatsapp_queue = request.endpoint in ['whatsapp_queue_list', ...] %}
```

These variables drive the `active` CSS class on sidebar links:
```jinja2
<a href="..." class="sidebar-link {{ 'active' if on_dashboard else '' }}">
```

This is the **only active-state mechanism** — no JavaScript required.

### 4.4 Role-Gated Navigation

The sidebar's AI section is hidden for `staff` role users:
```jinja2
{% if not current_role or current_role in ['owner', 'partner', 'manager'] %}
  ... AI, Credentials, Connectors, GST sections ...
{% endif %}
```

Audit Logs are further restricted:
```jinja2
{% if current_role in ['owner', 'partner', 'manager'] %}
```

---

## 5. Design System (`app.css`)

### 5.1 CSS Custom Properties (Design Tokens)

```css
:root {
  --bg:         #f3f5f9;   /* Page background — light blue-grey */
  --surface:    #ffffff;   /* Card / panel background */
  --surface-alt:#f8fafc;   /* Secondary surface, table headers */
  --border:     #dbe3ef;   /* All borders */
  --text:       #111827;   /* Primary text */
  --muted:      #64748b;   /* Secondary / label text */
  --primary:    #1d4ed8;   /* Blue — CTA, active states */
  --success:    #15803d;   /* Green */
  --warning:    #b45309;   /* Amber */
  --danger:     #b91c1c;   /* Red */
  --info:       #0f766e;   /* Teal */
}
```

**Key principle:** All semantic colors reference these tokens. Bootstrap's utility classes (`text-danger`, `bg-success`) are used alongside these tokens — they are complementary, not duplicated.

### 5.2 Sidebar

```css
.app-sidebar        { width: 272px; background: #0f172a; }  /* Slate-900 */
.sidebar-brand      { color: #f8fafc; font-weight: 700; }
.sidebar-link       { border-radius: 10px; color: #cbd5e1; }
.sidebar-link.active { background: rgba(29,78,216,0.25); border-color: #1d4ed8; }
```

The sidebar uses a **dark navy background** (`#0f172a`) intentionally contrasting with the light page body — standard SaaS dashboard pattern.

### 5.3 Cards

```css
.card { border-radius: 14px; box-shadow: 0 4px 16px rgba(15,23,42,0.04); }
```

Bootstrap's `.card` is overridden to use the design system's border and subtle elevation shadow.

### 5.4 KPI Cards

A dedicated `.kpi-card` component exists for dashboard metric displays:

```css
.kpi-card   { border-radius: 14px; padding: 0.95rem 1rem; }
.kpi-label  { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; }
.kpi-value  { font-size: 1.55rem; font-weight: 700; }
```

Used in: Dashboard, GST Dashboard, Email QA Dashboard, WhatsApp Queue, usage page.

### 5.5 Tables

Table headers are sticky (`position: sticky; top: 0`) to support long lists. Column headers use uppercase small caps for the data-dense compliance context:

```css
.table thead th {
  position: sticky; top: 0; z-index: 1;
  background: #f8fafc;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
```

Row hover highlight uses a very light blue: `background: #f8fbff`.

### 5.6 Status Badge System

A complete set of semantic CSS classes maps task/workflow states to pill badges:

| Class | State | Color |
|---|---|---|
| `.status-draft` | Draft | Indigo |
| `.status-pending_documents` | Awaiting documents | Amber |
| `.status-ready_for_ai` | Queued for AI | Cyan |
| `.status-ai_queued` | AI queue | Yellow |
| `.status-ai_processing` | AI working | Blue |
| `.status-ai_draft_ready` | AI complete | Green |
| `.status-under_review` | Under review | Teal |
| `.status-changes_required` | Needs changes | Orange |
| `.status-approved` | Approved | Green |
| `.status-filed` | Filed | Green |
| `.status-closed` | Closed | Slate |
| `.status-cancelled` | Cancelled | Light slate |
| `.status-ai_failed` | AI failed | Red |

Usage: `<span class="status-badge status-{{ task.status }}">{{ task.status }}</span>`

### 5.7 Row Highlight System

For queue-style tables, semantic row backgrounds communicate status at-a-glance:

```css
.row-highlight-failed  { background: #fff1f2; }  /* Red tint */
.row-highlight-queued  { background: #fffaf0; }  /* Amber tint */
.row-highlight-ready   { background: #f3fff5; }  /* Green tint */
```

### 5.8 Responsive Breakpoints

- **Desktop (≥ 992px):** Full sidebar visible; `d-lg-flex` on `.app-sidebar`
- **Mobile/Tablet (< 992px):** Sidebar hidden; hamburger button triggers Bootstrap Offcanvas
- `app.css` has a single `@media (max-width: 991.98px)` block reducing padding

---

## 6. JavaScript Architecture

The app is intentionally **minimal-JS**. The three JS files serve distinct purposes:

### 6.1 `app.js` — Mobile Sidebar Toggle

```javascript
// Initialises Bootstrap Offcanvas on the mobile sidebar hamburger button
const sidebar = new window.bootstrap.Offcanvas(offcanvasEl);
collapseBtn.addEventListener("click", () => sidebar.toggle());
```

Single responsibility: connects the hamburger button (`#sidebarToggleBtn`) to the mobile offcanvas sidebar (`#mobileSidebar`). ~10 lines total.

### 6.2 `document_communication.js` — Draft Utilities

Handles copy-to-clipboard for communication draft bodies:
- `copyToClipboard(text, btn)` — tries modern `navigator.clipboard.writeText`, falls back to `document.execCommand('copy')` for older browsers
- `showFeedback(btn, msg, type)` — temporarily changes button text/colour for 2 seconds after copy
- `initCopyButtons()` — wires up all `[data-copy-target]` buttons on page load

Scoped in an IIFE `(function() { 'use strict'; ... })()` — no global pollution.

### 6.3 `voice_assistant.js` — Jarvis AI Interface

The most complex JS file. Handles:
- **Web Speech API integration** — `SpeechRecognition` for microphone input with `onresult` / `onerror` / `onend` handlers
- **Parse fetch** — `POST /voice-assistant/parse` with command text → renders intent preview (intent name, confidence, parameters, matched client)
- **Execute fetch** — `POST /voice-assistant/execute` with confirmed parsed intent → shows execution result
- **Preview panel** — live-updates `#previewIntent`, `#previewConfidence`, `#previewParameters`, `#previewClientInfo` DOM elements
- **Confirm/Cancel gate** — Confirm button disabled until a parseable intent is returned; prevents blind execution

### 6.4 Inline `{% block scripts %}` JS

Several templates use `{% block scripts %}` for page-specific JavaScript that does not warrant a separate file:

| Template | Inline JS purpose |
|---|---|
| `credential_detail.html` | Async `POST /credentials/<id>/verify-live` → shows Playwright result inline |
| `checkout.html` | Razorpay payment SDK integration |
| `voice_assistant.html` | (deferred to `voice_assistant.js`) |

Pattern used for credential verify:
```javascript
fetch('/credentials/' + credId + '/verify-live', { method: 'POST', ... })
  .then(r => r.json())
  .then(data => { /* update #liveVerifyResult div */ });
```

No page reload required — result shown inline as a Bootstrap alert.

---

## 7. Page Inventory

### 7.1 Public Pages (unauthenticated)

| Template | Route | Purpose |
|---|---|---|
| `index.html` | `/` | Landing page |
| `login.html` | `/login` | Firm login |
| `signup.html` | `/signup` | New tenant registration |
| `checkout.html` | `/checkout/<plan>` | Razorpay subscription payment |

### 7.2 Core Operations

| Template | Route | Purpose |
|---|---|---|
| `dashboard.html` | `/dashboard` | Compliance Control Room — KPI cards, task table, overdue alerts |
| `client_entities/list.html` | `/clients/` | Client list with search/filter |
| `client_entities/new.html` | `/clients/new` | Add client |
| `client_entities/detail.html` | `/clients/<id>` | Client profile + tasks + credentials |
| `client_entities/edit.html` | `/clients/<id>/edit` | Edit client |
| `compliance_tasks/` | `/tasks/...` | Task lifecycle (new, detail, edit, review) |
| `new_task.html` | `/tasks/new` | Quick task creation form |
| `task_detail.html` | `/tasks/<id>` | Full task detail — AI draft, review, documents |
| `document_requests.html` | `/document-requests/` | Pending document requests |

### 7.3 AI & Automation

| Template | Route | Purpose |
|---|---|---|
| `automation.html` | `/automation/` | AI Automation Center — queued / processing tasks |
| `automation_registry.html` | `/automation-registry/` | All registered automation rules |
| `voice_assistant.html` | `/voice-assistant/` | Jarvis — voice + text NLP command interface |
| `credentials.html` | `/credentials/` | Credential Vault list |
| `credential_detail.html` | `/credentials/<id>` | Detail + live Playwright verification |
| `portal_readiness.html` | `/portal-readiness/` | Portal readiness status across all clients |
| `accounting_connectors.html` | `/accounting-connectors/` | Connector list |
| `accounting_connection_detail.html` | `/accounting-connectors/<id>` | Connector detail + sync history |
| `accounting_data.html` | `/accounting-data/` | Ledger/journal data viewer |
| `accounting_ledger_detail.html` | `/accounting-data/<id>` | Single ledger detail |
| `accounting_upload_preview.html` | `/accounting-upload-preview/` | Manual upload parse preview |

### 7.4 GST Module

| Template | Route | Purpose |
|---|---|---|
| `gst_dashboard.html` | `/gst-dashboard/` | GST Control Room — return status across all clients |
| `gst_reconciliation.html` | `/gst-reconciliation/` | Reconciliation jobs list |
| `gst_reconciliation_detail.html` | `/gst-reconciliation/<id>` | Diff table — books vs GSTN |
| `gst_working_note.html` | `/gst-working-note/` | GST working notes |
| `gstr3b_review_packs.html` | `/gstr3b-review-packs/` | Review pack list |
| `gstr3b_review_pack_detail.html` | `/gstr3b-review-packs/<id>` | Full GSTR-3B review pack detail |

### 7.5 Communications Pipeline

| Template | Route | Purpose |
|---|---|---|
| `document_communication_register.html` | `/comms/` | Communication drafts register |
| `document_communication_draft_detail.html` | `/comms/<id>` | Draft detail + queue email/WhatsApp |
| `document_communication_print.html` | `/comms/<id>/print` | Print-optimised draft view |
| `email_queue.html` | `/email-queue/` | Email queue — KPI cards + filter + table |
| `email_queue_detail.html` | `/email-queue/<id>` | Email queue item — approve/send/cancel |
| `email_delivery_logs.html` | `/email-delivery-logs/` | All email send events |
| `email_provider_settings.html` | `/email-providers/` | SMTP/API provider list |
| `email_provider_detail.html` | `/email-providers/<id>` | Provider config + health check |
| `email_dry_run_detail.html` | `/email-dry-run/<id>` | Dry run result detail |
| `email_operations.html` | `/email-operations/` | Bulk operations, resend, retry |
| `email_qa_dashboard.html` | `/email-qa/` | QA dashboard — delivery KPIs, error rates |
| `email_readiness.html` | `/email-readiness/` | Provider readiness checker |
| `whatsapp_queue.html` | `/whatsapp-queue/` | WhatsApp queue — KPI cards + table |
| `whatsapp_queue_detail.html` | `/whatsapp-queue/<id>` | Detail — approve/send/cancel |

### 7.6 Operations & Compliance

| Template | Route | Purpose |
|---|---|---|
| `usage.html` | `/usage/` | Plan tier, API usage, quota bars |
| `audit_logs.html` | `/audit-logs/` | Full audit trail with filters |

---

## 8. Navigation & UX Patterns

### 8.1 Sidebar Structure

The sidebar is divided into four labelled sections:

```
CA Assist (brand)
├── Main
│   ├── Dashboard
│   ├── Clients
│   └── Tasks
├── AI  (owners/partners/managers only)
│   ├── AI Automation
│   ├── Automation Registry
│   ├── Credential Vault
│   ├── Portal Readiness
│   ├── Accounting Connectors
│   ├── Accounting Data
│   ├── GST Reconciliation
│   ├── GST Control Room
│   ├── GSTR-3B Review Packs
│   └── Jarvis Assistant
├── Communications
│   ├── Communication Drafts
│   ├── Email Queue
│   ├── WhatsApp Queue
│   ├── Email Delivery Logs
│   ├── Email Providers
│   ├── Email Operations
│   ├── Email QA Dashboard
│   └── Email Readiness
└── Operations
    ├── Plan & Usage
    └── Audit Logs (owners/managers only)
```

### 8.2 Topbar Meta Chips

Every page header shows contextual metadata as pill badges:

```html
<span class="meta-chip">Role: Manager</span>
<span class="meta-chip">Tenant #42</span>
```

These use `.meta-chip` — a small rounded pill with the design system's border and surface-alt background.

### 8.3 Flash Message System

Flask's `flash()` is the sole mechanism for user feedback after form submissions. `base.html` renders all pending flashes automatically above `{% block content %}` as Bootstrap dismissible alerts:

```jinja2
{% for cat, msg in messages %}
  <div class="alert alert-{{ cat }} alert-dismissible fade show rounded-3">
    {{ msg }}
    <button class="btn-close" data-bs-dismiss="alert"></button>
  </div>
{% endfor %}
```

Categories used: `success`, `danger`, `warning`, `info`.

### 8.4 KPI Card Pattern

All module dashboards open with a row of KPI summary cards:

```html
<div class="row g-3 mb-4">
  <div class="col-lg-2 col-md-3 col-6">
    <div class="kpi-card">
      <div class="kpi-label">Open Tasks</div>
      <div class="kpi-value" style="color: var(--info);">{{ summary.open_tasks }}</div>
    </div>
  </div>
  ...
</div>
```

KPI value colours use CSS variables directly for semantic meaning.

### 8.5 Filter Bar Pattern

List views expose a filter form above the table. All filters are GET parameters:

```html
<form method="get" class="row g-2 mb-3">
  <select name="status">...</select>
  <input name="client" type="text" ...>
  <button type="submit">Filter</button>
  <a href="{{ url_for('...') }}">Clear</a>
</form>
```

The selected values are preserved by the Jinja2 `{{ 'selected' if filters.x == 'value' }}` pattern.

### 8.6 Empty State Pattern

Empty tables always show a styled empty state rather than a blank screen:

```html
<div class="empty-state">
  <i class="bi bi-inbox fs-2 text-muted"></i>
  <p class="text-muted mt-2">No items found.</p>
</div>
```

---

## 9. Form Patterns

All forms follow a consistent pattern:

### 9.1 Inline CSRF Protection

Every POST form includes a CSRF token hidden field:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>
```

### 9.2 Standard Form Submit

Default: synchronous POST, Flask processes, redirects with flash message. No AJAX.

### 9.3 Async Forms (exceptions)

Two cases use `fetch()` instead of a form submit:
1. **Credential live verify** (`credential_detail.html`) — POST to `/credentials/<id>/verify-live`, result shown inline without page reload
2. **Voice command parse** (`voice_assistant.html`) — POST to `/voice-assistant/parse` → preview shown inline; separate POST to `/voice-assistant/execute` on confirmation

### 9.4 Confirmation Pattern

Destructive actions (cancel, delete) use inline confirmation forms with an optional reason input rather than `window.confirm()` — keeping UX in the server-rendered paradigm.

---

## 10. Responsive Design

| Breakpoint | Behaviour |
|---|---|
| ≥ 992px (lg) | Full sidebar visible; content gets full `calc(100vw - 272px)` |
| < 992px | Sidebar hidden; hamburger button appears in topbar; sidebar opens as Bootstrap Offcanvas drawer |
| Mobile tables | `.table-responsive` wrapper enables horizontal scroll |
| KPI cards | `col-lg-2 col-md-3 col-6` — 6 per row on desktop, 4 on tablet, 2 on mobile |

The offcanvas mobile sidebar duplicates all sidebar navigation as a `list-group` (separate from the desktop `<aside>`). Both are rendered server-side; the mobile one is just hidden via CSS until the hamburger button is tapped.

---

## 11. Inter-Module Integration Points

### 11.1 Communication Drafts → Email Queue → WhatsApp Queue

```
document_communication_draft_detail.html
  └── if draft_type == 'email' and status == 'reviewed'
        └── Queue Email Form → POST /email-queue/from-draft/<id>
              └── email_queue_detail.html → Approve → Send
  └── if draft_type == 'whatsapp' and status == 'reviewed'
        └── Queue WhatsApp Form → POST /whatsapp-queue/from-draft/<id>
              └── whatsapp_queue_detail.html → Approve → Send
```

### 11.2 Credentials → Portal Browser

```
credential_detail.html
  └── "Verify Live via Browser" button
        └── JS: POST /credentials/<id>/verify-live
              └── Playwright result rendered inline as Bootstrap alert
```

### 11.3 Task Detail → AI Draft → Review Workflow

```
task_detail.html
  └── "Send to AI" → POST /tasks/<id>/send-to-ai
        └── automation.html (shows processing)
              └── review_workflow → task_detail.html (draft ready)
```

---

## 12. Current Frontend Gaps & Upgrade Recommendations

### 12.1 Known Gaps

| Gap | Location | Impact |
|---|---|---|
| Mobile sidebar does not include WhatsApp Queue link | `base.html` offcanvas | Low — WhatsApp Queue is missing from mobile nav |
| No loading spinner on standard form submits | All POST forms | Medium — no feedback during slow DB writes |
| No client-side form validation | All forms | Low — server validates and flashes errors |
| Tables have no pagination | Most list views | Medium — will degrade with large datasets (500+ rows) |
| No dark mode | `app.css` | Low — CSS tokens are in place, just needs a `[data-theme=dark]` override block |
| `document_communication.js` export functions incomplete | `document_communication.js` | Low — copy works; PDF/Word export not wired |

### 12.2 Short-Term Recommended Improvements

1. **Pagination** — add `LIMIT/OFFSET` at the Flask level + a Bootstrap pagination component to all list templates. No JS required.
2. **Skeleton loading states** — add `<div class="placeholder-glow">` on the KPI cards while the page is rendering for perceived performance.
3. **Table column sort** — a single `sort_by` + `sort_dir` GET param pattern, handled server-side; no JS library needed.
4. **Mobile offcanvas WhatsApp link** — add the WhatsApp Queue `<a>` to the offcanvas list in `base.html`.

### 12.3 Medium-Term Recommended Improvements

1. **Partial page updates (HTMX)** — the existing server-side rendering makes HTMX a natural fit. Queue status badges, KPI cards, and task status updates could refresh without full page reloads using `hx-get` / `hx-swap`.
2. **Toast notifications** — replace flash message alerts with Bootstrap Toast component for non-blocking feedback.
3. **Data tables (DataTables.js)** — add client-side column sort, search, and pagination to long lists (clients, tasks, audit logs).
4. **PWA manifest** — add a `manifest.json` and service worker for mobile "Add to Home Screen" support — beneficial for CA staff on mobile.

### 12.4 Long-Term Architecture Consideration

If the product expands significantly (real-time updates, collaborative editing, complex interactive forms), consider migrating to a **hybrid architecture**:
- Keep server-rendered pages for all read views (SEO, simplicity)
- Add a lightweight reactive layer (Alpine.js or HTMX) for interactive components
- Avoid a full React/Vue rewrite unless a dedicated frontend team is available

---

## 13. Development Workflow

### Running the App

```powershell
cd "C:\agents\office automation"
.venv\Scripts\Activate.ps1
cd saas
python app.py
```

Access at `http://localhost:5000`.

### Adding a New Page

1. Create `saas/templates/your_page.html` extending `base.html`
2. Add route handler in `saas/app.py` with `render_template("your_page.html", **ctx)`
3. Add `{% set on_your_module = ... %}` variable in `base.html`
4. Add sidebar link in `base.html` (both desktop `<aside>` and mobile offcanvas list)

### Adding Module-Specific CSS

All styles live in `static/css/app.css`. Add new component styles after the existing blocks. Do not create per-module CSS files — the single-file approach keeps the load simple.

### Adding Module-Specific JS

For complex interactions requiring > ~30 lines of JS, create `static/js/your_module.js` and load it via `{% block scripts %}` in the relevant template:

```jinja2
{% block scripts %}
<script src="{{ url_for('static', filename='js/your_module.js') }}"></script>
{% endblock %}
```

For simple page interactions (< 30 lines), use an inline `<script>` inside `{% block scripts %}`.

---

*End of document — CA Assist Frontend Architecture Reference*

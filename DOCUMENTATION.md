# CA Assist — Full Technical Documentation

> AI-powered compliance assistant for Indian CA firms.
> Built on top of [Paperclip](https://github.com/paperclipai/paperclip) (open-source AI agent orchestration).

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Structure](#2-repository-structure)
3. [How It All Works — End to End](#3-how-it-all-works--end-to-end)
4. [Component Reference](#4-component-reference)
   - 4.1 [ca-agent/agent.py](#41-ca-agentagentpy)
   - 4.2 [ca-agent/paperclip_client.py](#42-ca-agentpaperclip_clientpy)
   - 4.3 [ca-agent/ca_knowledge.py](#43-ca-agentca_knowledgepy)
   - 4.4 [ca-agent/llm_client.py](#44-ca-agentllm_clientpy)
   - 4.5 [ca-agent/skills/ca-agent/SKILL.md](#45-ca-agentskillsca-agentskillmd)
   - 4.6 [saas/app.py](#46-saasapppy)
   - 4.7 [saas/db.py](#47-saasdbpy)
   - 4.8 [saas/provisioner.py](#48-saasprovisionerpy)
   - 4.9 [saas/billing.py](#49-saasbillingpy)
   - 4.10 [saas/templates/](#410-saastemplates)
5. [Data Flow Diagrams](#5-data-flow-diagrams)
6. [Environment Variables](#6-environment-variables)
7. [Local Development Setup](#7-local-development-setup)
8. [Production Deployment](#8-production-deployment)
9. [Statutory Calendar Reference](#9-statutory-calendar-reference)
10. [SaaS Pricing & Plan Limits](#10-saas-pricing--plan-limits)
11. [Extending the System](#11-extending-the-system)

---

## 1. System Overview

CA Assist is a two-layer product:

```
┌─────────────────────────────────────────────────────────┐
│  LAYER 2 — SaaS Portal  (saas/)                         │
│                                                         │
│  Flask web app — signup, billing (Razorpay), dashboard  │
│  Designed for non-tech CAs: visual task selection,      │
│  plain-language labels, auto-refreshing results.        │
└──────────────────────────┬──────────────────────────────┘
                           │ provisions company + hires agent
                           │ REST API calls to Paperclip
                           ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 1 — Paperclip Orchestration                      │
│                                                         │
│  Open-source agent platform (npx paperclipai onboard)  │
│  Manages: companies, agents, issues, heartbeats,        │
│  activity logs, cost tracking, governance, secrets.     │
│                                                         │
│  Each CA firm = one Paperclip "company" (isolated).     │
└──────────────────────────┬──────────────────────────────┘
                           │ CLI spawn on heartbeat
                           │ PAPERCLIP_* env vars injected
                           ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 0 — CA Agent  (ca-agent/)                        │
│                                                         │
│  Python CLI adapter — the actual intelligence.          │
│  Fetches open issues → classifies domain →              │
│  calls Claude/GPT with CA-specific prompt →             │
│  posts answer → marks done or blocked.                  │
└─────────────────────────────────────────────────────────┘
```

**Key design principle:** Paperclip handles *orchestration* (scheduling, multi-tenancy, audit trail, secrets). The Python agent handles *domain expertise* (GST/ITR/TDS/ROC knowledge, LLM prompting, statutory calendar). The SaaS portal handles *customer lifecycle* (onboarding, billing, a simple UI for non-tech users).

---

## 2. Repository Structure

```
office automation/
│
├── ca-agent/                          ← Agent code (Paperclip CLI adapter)
│   ├── agent.py                       Main entrypoint — spawned by Paperclip
│   ├── paperclip_client.py            REST API wrapper
│   ├── ca_knowledge.py                Domain knowledge: calendar, classifier, prompt
│   ├── llm_client.py                  LLM abstraction (Anthropic / OpenAI)
│   ├── requirements.txt               anthropic>=0.40.0
│   ├── README.md                      Agent-specific setup guide
│   └── skills/
│       └── ca-agent/
│           └── SKILL.md               Paperclip skill definition (injected at runtime)
│
└── saas/                              ← SaaS web portal
    ├── app.py                         Flask routes (auth, billing, tasks)
    ├── db.py                          SQLite models (users, tenants, subscriptions)
    ├── provisioner.py                 Auto-provisions Paperclip on payment
    ├── billing.py                     Razorpay order + verification
    ├── requirements.txt               flask, razorpay, werkzeug, python-dotenv
    ├── .env.example                   Environment variable template
    └── templates/
        ├── base.html                  Bootstrap 5 layout, navbar
        ├── index.html                 Landing page + pricing cards
        ├── signup.html                Registration form (6 fields)
        ├── login.html
        ├── checkout.html              Razorpay payment button
        ├── dashboard.html             Task list with status badges
        ├── new_task.html              Visual task-type selection + details form
        └── task_detail.html           Agent answer view, auto-refresh
```

---

## 3. How It All Works — End to End

### A new CA firm signs up

1. CA visits `yourproduct.com`, clicks **Get Started**, selects a plan.
2. `signup.html` collects: name, firm name, email, phone, GSTIN, password.
3. `app.py → /signup` creates a `users` row and a `tenants` row (status = `pending_payment`).
4. User is redirected to `checkout.html` — a Razorpay payment button.
5. Razorpay SDK handles the payment UI client-side.
6. On success, browser POSTs to `/billing/verify` with the Razorpay signature.
7. `billing.py → verify_payment()` validates the HMAC signature.
8. If valid: a `subscriptions` row is inserted, then `provisioner.py → provision_tenant()` is called.
9. `provision_tenant()` makes two Paperclip REST API calls:
   - `POST /api/companies` — creates an isolated company for the firm.
   - `POST /api/companies/{id}/agents` — hires the CA Compliance Agent as a CLI adapter.
   - Sets `ANTHROPIC_API_KEY` and `TENANT_PLAN` as Paperclip secrets on the agent.
10. `tenants` row is updated: `paperclip_company_id`, `paperclip_agent_id`, status = `active`.
11. User lands on `dashboard.html`.

### A CA creates a task

1. CA clicks **New Task** on the dashboard.
2. `new_task.html` shows big visual cards: GSTR-1, GSTR-3B, TDS Return, ITR, ROC, etc.
3. CA selects a task type, enters client name and period, adds optional notes.
4. `app.py → _build_task()` converts the selection into a well-formed title + description using templates (e.g. *"File GSTR-3B for ABC Pvt Ltd — May 2026"*).
5. `provisioner.py → create_task()` calls `POST /api/companies/{id}/issues`.
6. CA is redirected to `task_detail.html`, which auto-refreshes every 20 seconds.

### The agent processes the task

1. Paperclip runs `python agent.py` on the configured heartbeat schedule (e.g. every 30 min), injecting `PAPERCLIP_*` env vars including `PAPERCLIP_TASK_ID`.
2. `agent.py → run()`:
   - Reads `PAPERCLIP_TASK_ID` or calls `list_my_issues()` to find the next open issue.
   - Calls `checkout_issue()` — locks the issue to this agent run.
   - Fetches the issue and its comment history.
   - Detects if it is a **compliance calendar** request → calls `handle_compliance_calendar()` which builds a markdown table of upcoming due dates from `ca_knowledge.upcoming_due_dates(45)`.
   - For all other tasks: calls `build_task_prompt()` to construct the user message (title + description + last 10 comments + domain tags).
   - Calls `llm_client.complete(system_prompt, user_message, history)`.
   - Parses the LLM response: if it contains phrases like *"please provide"* or *"need the following"* → marks the issue `blocked`; otherwise marks it `done`.
   - Posts the LLM response as a Paperclip comment.

### The CA sees the result

1. `task_detail.html` polls every 20 seconds.
2. Once the issue status changes to `done` or `blocked`, the page shows the agent's comment in a green card.
3. If `blocked`, a yellow banner tells the CA what information is needed.

---

## 4. Component Reference

### 4.1 `ca-agent/agent.py`

**Purpose:** CLI entrypoint that Paperclip spawns on every heartbeat.

**Key functions:**

| Function | Description |
|---|---|
| `run()` | Main loop: resolve issue → checkout → classify → prompt LLM → post comment → update status |
| `build_task_prompt(issue, comments, client)` | Assembles the LLM user message from issue fields, domain classification, and comment history |
| `handle_compliance_calendar(client, issue)` | Special handler for calendar requests — builds a markdown table of deadlines within 45 days |
| `log(msg)` | Prefixes `[ca-agent]` and flushes to stdout (captured by Paperclip's run log) |

**Issue status logic:**

```
LLM response contains "please provide" / "need the following"
  / "kindly share" / "required documents" / "missing information"
    → mark issue BLOCKED
Otherwise
    → mark issue DONE
```

---

### 4.2 `ca-agent/paperclip_client.py`

**Purpose:** Typed wrapper around Paperclip's REST API. Reads all auth context from `PAPERCLIP_*` env vars injected by Paperclip at runtime.

**Constructor reads:**

| Env var | Used for |
|---|---|
| `PAPERCLIP_API_URL` | Base URL (default: `http://localhost:3100`) |
| `PAPERCLIP_API_KEY` | Bearer token |
| `PAPERCLIP_AGENT_ID` | Agent identity |
| `PAPERCLIP_COMPANY_ID` | Tenant isolation |
| `PAPERCLIP_RUN_ID` | Current heartbeat run ID (added to all mutating requests) |
| `PAPERCLIP_TASK_ID` | Specific issue to work on (optional) |
| `PAPERCLIP_WAKE_REASON` | Why the heartbeat fired |

**Methods:**

| Method | HTTP | Purpose |
|---|---|---|
| `get_me()` | GET `/api/me` | Verify agent identity |
| `get_issue(id)` | GET `/api/issues/{id}` | Fetch issue details |
| `checkout_issue(id)` | POST `/api/issues/{id}/checkout` | Lock issue to this run |
| `update_issue(id, status, …)` | PATCH `/api/issues/{id}` | Set done / blocked / priority |
| `post_comment(id, body)` | POST `/api/issues/{id}/comments` | Post agent reply |
| `get_comments(id)` | GET `/api/issues/{id}/comments` | Fetch thread |
| `create_child_issue(parent_id, …)` | POST `/api/companies/{cid}/issues` | Create sub-task |
| `list_my_issues()` | GET `/api/companies/{cid}/issues?assigneeId={aid}&status=open` | Scan for work |

---

### 4.3 `ca-agent/ca_knowledge.py`

**Purpose:** All India-specific CA domain knowledge. No external dependencies.

**`STATUTORY_CALENDAR`** — 23 entries covering:
- GST: GSTR-1 (monthly + QRMP quarterly), GSTR-3B (monthly + QRMP), GSTR-9, GSTR-9C
- TDS/TCS: monthly deposit (7th), quarterly returns 24Q/26Q, Form 16/16A
- Income Tax: all 4 advance tax instalments, ITR non-audit (31 Jul), ITR audit (31 Oct), Tax Audit Report (30 Sep), Transfer Pricing (31 Oct)
- ROC/MCA: AOC-4 (29 Nov), MGT-7/7A (28 Nov), DIR-3 KYC (30 Sep)
- Payroll: PF deposit (15th), ESI deposit (15th), PT Return Maharashtra (31st)

**`upcoming_due_dates(within_days=30)`** — Returns deadlines falling within the next N days, sorted by date. Used both by the agent and the dashboard banner.

**`classify_task(title, description)`** — Keyword-based classifier returning a list of domain tags from `["gst", "income_tax", "roc", "payroll", "audit", "advisory", "compliance_calendar"]`. Used to hint the LLM and route to `handle_compliance_calendar`.

**`build_system_prompt()`** — Returns the full LLM system prompt including:
- Role: Senior CA with 20+ years Indian practice
- FY 2025-26 rates: Income tax slabs (new + old regime), GST rates, TDS sections, PF/ESI rates
- Injected upcoming due dates (next 45 days)
- Output formatting rules (markdown, show workings, cite sections)

---

### 4.4 `ca-agent/llm_client.py`

**Purpose:** Single-function LLM abstraction. Supports Anthropic Claude and OpenAI GPT.

**`complete(system, user_message, history=[])`** — Dispatches to the configured provider. History is a list of `{"role": "user"|"assistant", "content": "…"}` dicts for multi-turn context.

**Provider selection:**

| `LLM_PROVIDER` | SDK / method |
|---|---|
| `anthropic` (default) | `anthropic` Python SDK → `messages.create` |
| `openai` | `urllib.request` → `POST https://api.openai.com/v1/chat/completions` |

No third-party HTTP library required — OpenAI calls use the standard library only.

---

### 4.5 `ca-agent/skills/ca-agent/SKILL.md`

**Purpose:** Paperclip skill definition file. Paperclip injects this as context when the agent wakes up (if the skill is attached to the company).

Covers: how the agent operates inside Paperclip, valid status transitions, when to create sub-tasks, domain expertise boundaries, expected response format.

---

### 4.6 `saas/app.py`

**Purpose:** Flask backend — all HTTP routes.

**Routes:**

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Landing page with pricing |
| `/signup` | GET / POST | Registration → redirects to checkout |
| `/login` | GET / POST | Email + password auth |
| `/logout` | GET | Clears session |
| `/checkout/<plan>` | GET | Creates Razorpay order, shows payment page |
| `/billing/verify` | POST (JSON) | Verifies Razorpay signature, provisions tenant |
| `/dashboard` | GET | Task list for logged-in CA |
| `/tasks/new` | GET / POST | Task creation form + template builder |
| `/tasks/<issue_id>` | GET | Task detail + agent response |

**`_build_task(task_type, client_name, period, extra)`** — Converts the simple form inputs into a full title + description string using `TASK_TEMPLATES`. This means the CA never has to write a prompt — they just fill in the blanks.

Supported task types: `gstr1`, `gstr3b`, `gstr9`, `tds_return`, `tds_cert`, `itr`, `advance_tax`, `aoc4`, `mgt7`, `dir3kyc`, `pf_esi`, `due_dates`, `query`.

---

### 4.7 `saas/db.py`

**Purpose:** SQLite database with three tables. Uses a context-manager `get_db()` that always commits or rolls back.

**Schema:**

```sql
users (
    id, email, password_hash, name, firm_name, gstin, phone, created_at
)

tenants (
    id, user_id, paperclip_company_id, paperclip_agent_id,
    plan, status, created_at
    -- status: pending_payment | active | provisioning_failed
)

subscriptions (
    id, tenant_id, razorpay_payment_id, razorpay_order_id,
    plan, status, created_at
)
```

**`init_db()`** — Called once at startup (or manually) to create tables if they do not exist.

---

### 4.8 `saas/provisioner.py`

**Purpose:** Calls Paperclip's REST API to set up a new tenant. Also provides helper functions for task management used by `app.py`.

**`provision_tenant(firm_name, user_email, plan)`**

1. `POST /api/companies` — creates the company (one per CA firm).
2. `POST /api/companies/{id}/agents` — hires the CA agent as a CLI adapter:
   - Command: `AGENT_COMMAND` env var (default: `python agent.py`)
   - Working directory: `AGENT_WORKING_DIR` env var
   - Secrets: `ANTHROPIC_API_KEY`, `TENANT_PLAN`
   - Heartbeat schedule: `*/30 * * * *` (every 30 minutes)
3. Returns `{"company_id": …, "agent_id": …}`.

**Task helpers:**

| Function | Paperclip endpoint |
|---|---|
| `create_task(company_id, title, description)` | POST `/api/companies/{id}/issues` |
| `get_task(issue_id)` | GET `/api/issues/{id}` |
| `list_tasks(company_id)` | GET `/api/companies/{id}/issues` |
| `get_task_comments(issue_id)` | GET `/api/issues/{id}/comments` |

---

### 4.9 `saas/billing.py`

**Purpose:** Razorpay integration for INR billing.

**Plans:**

| Key | Name | Price | Clients |
|---|---|---|---|
| `starter` | Starter | ₹2,999/mo | 1 firm |
| `pro` | Pro | ₹7,999/mo | 5 firms |
| `agency` | Agency | ₹1,999/mo per firm | Unlimited |

**`create_order(plan)`** — Creates a Razorpay order (amount in paise). Returns the order object including `id` passed to the checkout JS.

**`verify_payment(order_id, payment_id, signature)`** — Uses `razorpay.Client.utility.verify_payment_signature()` to validate the HMAC-SHA256 signature. Returns `True` / `False`. Never trusts the payment without this check.

---

### 4.10 `saas/templates/`

All templates extend `base.html` which provides Bootstrap 5, navbar, and flash message display.

| Template | Purpose | Key UX decision |
|---|---|---|
| `index.html` | Landing + pricing | 3-step "how it works" + feature icons |
| `signup.html` | Registration | Large inputs, GSTIN optional, plain labels |
| `login.html` | Auth | Minimal — email + password only |
| `checkout.html` | Payment | Single button → Razorpay popup |
| `dashboard.html` | Task list | Colour-coded status pills (green Done / yellow Working / red Need Info) |
| `new_task.html` | Create task | Visual card grid grouped by category, JS hides irrelevant fields per type |
| `task_detail.html` | View result | Agent reply in green card; auto-refreshes every 20s while pending |

---

## 5. Data Flow Diagrams

### Signup + provisioning

```
Browser                    Flask (saas/)          Paperclip API       SQLite
   │                            │                       │                │
   │── POST /signup ───────────►│                       │                │
   │                            │── INSERT users ──────────────────────►│
   │                            │── INSERT tenants ────────────────────►│
   │◄── 302 /checkout/pro ──────│                       │                │
   │                            │                       │                │
   │── Razorpay payment ────────│                       │                │
   │◄── payment success ────────│                       │                │
   │                            │                       │                │
   │── POST /billing/verify ───►│                       │                │
   │                            │── verify signature    │                │
   │                            │── INSERT subscription────────────────►│
   │                            │── POST /api/companies────────────────►│
   │                            │◄── company_id ────────│                │
   │                            │── POST /api/companies/{id}/agents ───►│
   │                            │◄── agent_id ──────────│                │
   │                            │── UPDATE tenants (active) ───────────►│
   │◄── 200 {redirect:/dash} ───│                       │                │
```

### Agent heartbeat

```
Paperclip Scheduler         agent.py              Paperclip API       LLM API
       │                        │                       │                │
       │── spawn python ───────►│                       │                │
       │   (PAPERCLIP_* vars)   │                       │                │
       │                        │── GET /api/issues/{id}───────────────►│
       │                        │◄── issue + description│                │
       │                        │── POST /checkout ─────────────────────►│
       │                        │── GET /comments ──────────────────────►│
       │                        │                       │                │
       │                        │── build prompt ───────────────────────►│ (LLM)
       │                        │◄── LLM response ──────────────────────│
       │                        │                       │                │
       │                        │── POST /comments ─────────────────────►│
       │                        │── PATCH /issues (done/blocked) ───────►│
       │◄── process exit 0 ─────│                       │                │
```

---

## 6. Environment Variables

### Agent (`ca-agent/`)

| Variable | Default | Required | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | — | If using Anthropic | Claude API key |
| `OPENAI_API_KEY` | — | If using OpenAI | OpenAI API key |
| `LLM_PROVIDER` | `anthropic` | No | `anthropic` or `openai` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5` | No | Model name |
| `OPENAI_MODEL` | `gpt-4o` | No | Model name |
| `LLM_MAX_TOKENS` | `4096` | No | Max response tokens |
| `PAPERCLIP_API_URL` | `http://localhost:3100` | Injected | Set to hosted URL in production |
| `PAPERCLIP_API_KEY` | — | Injected | Bearer token |
| `PAPERCLIP_AGENT_ID` | — | Injected | This agent's ID |
| `PAPERCLIP_COMPANY_ID` | — | Injected | Tenant company ID |
| `PAPERCLIP_RUN_ID` | — | Injected | Current heartbeat run |
| `PAPERCLIP_TASK_ID` | — | Injected | Specific issue to work on |
| `PAPERCLIP_WAKE_REASON` | — | Injected | Why heartbeat fired |
| `TENANT_PLAN` | — | Injected | `starter` / `pro` / `agency` |

### SaaS portal (`saas/`)

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Flask session signing key (random 32-byte hex) |
| `PAPERCLIP_API_URL` | Yes (prod) | Hosted Paperclip URL |
| `PAPERCLIP_ADMIN_API_KEY` | Yes | Admin API key for provisioning |
| `AGENT_COMMAND` | No | Default: `python agent.py` |
| `AGENT_WORKING_DIR` | Yes | Absolute path to `ca-agent/` folder |
| `ANTHROPIC_API_KEY` | Yes | Injected into each provisioned agent |
| `RAZORPAY_KEY_ID` | Yes (prod) | Razorpay publishable key (`rzp_live_…`) |
| `RAZORPAY_KEY_SECRET` | Yes (prod) | Razorpay secret key |
| `FLASK_DEBUG` | No | Set to `1` for development only |
| `DB_PATH` | No | Default: `ca_saas.db` |

---

## 7. Local Development Setup

### Prerequisites

- Python 3.10+
- Node.js 18+ (for Paperclip)
- A Razorpay test account (free at dashboard.razorpay.com)
- An Anthropic API key (or OpenAI)

### Step 1 — Start Paperclip

```powershell
npx paperclipai onboard --yes
# Paperclip is now running at http://localhost:3100
```

### Step 2 — Set up the agent

```powershell
cd "c:\agents\office automation\ca-agent"
pip install -r requirements.txt
```

Test it standalone (outside Paperclip):
```powershell
$env:PAPERCLIP_API_URL    = "http://localhost:3100"
$env:PAPERCLIP_API_KEY    = "your-key-from-paperclip-ui"
$env:PAPERCLIP_COMPANY_ID = "your-company-id"
$env:PAPERCLIP_AGENT_ID   = "your-agent-id"
$env:ANTHROPIC_API_KEY    = "sk-ant-..."
python agent.py
```

### Step 3 — Set up the SaaS portal

```powershell
cd "c:\agents\office automation\saas"
pip install -r requirements.txt
copy .env.example .env
# Edit .env and fill in all values
python app.py
# Portal is now at http://localhost:5000
```

### Step 4 — Register the agent in Paperclip UI

1. Open `http://localhost:3100`
2. Create a company → Agents → **Hire Agent**
3. Fill in:
   - Name: `CA Compliance Agent`
   - Adapter: `cli`
   - Command: `python agent.py`
   - Working directory: `C:\agents\office automation\ca-agent`
   - Secret: `ANTHROPIC_API_KEY = sk-ant-...`
4. Set heartbeat schedule: every 30 minutes

### Step 5 — Test the full flow

1. Open `http://localhost:5000` → Sign up (use Razorpay test keys)
2. Create a task: *GSTR-3B for Test Pvt Ltd — May 2026*
3. Watch `task_detail.html` auto-refresh every 20 seconds
4. After the next heartbeat, the agent's answer appears

---

## 8. Production Deployment

### Recommended stack (single VPS, India-friendly)

```
DigitalOcean / Hetzner VPS (Ubuntu 22.04, 4 GB RAM)
├── Nginx (reverse proxy)
│   ├── yourproduct.com         → Flask portal (gunicorn :5000)
│   └── paperclip.yourproduct.com → Paperclip server (:3100)
├── Paperclip (pm2 or systemd)
├── Flask portal (gunicorn + systemd)
└── ca-agent/ (same machine — spawned by Paperclip heartbeats)
```

### Environment variables in production

Set `PAPERCLIP_API_URL=https://paperclip.yourproduct.com` in:
- The `.env` file for the SaaS portal
- Paperclip's agent secrets for the CA agent (Paperclip will inject this automatically if you set it there)

### Gunicorn command

```bash
gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
```

### Systemd service example

```ini
[Unit]
Description=CA Assist SaaS Portal
After=network.target

[Service]
WorkingDirectory=/opt/ca-assist/saas
EnvironmentFile=/opt/ca-assist/saas/.env
ExecStart=/usr/local/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

### SSL

Use Certbot (free Let's Encrypt):
```bash
certbot --nginx -d yourproduct.com -d paperclip.yourproduct.com
```

---

## 9. Statutory Calendar Reference

All 23 deadlines tracked by `ca_knowledge.STATUTORY_CALENDAR` (FY 2025-26):

| Form | Frequency | Due | Description |
|---|---|---|---|
| GSTR-1 | Monthly | 11th | Outward supplies (> ₹5 Cr turnover) |
| GSTR-1 (QRMP) | Quarterly | 13th of Apr/Jul/Oct/Jan | Outward supplies — QRMP scheme |
| GSTR-3B | Monthly | 20th | Summary return & tax payment |
| GSTR-3B (QRMP) | Quarterly | 22nd of Apr/Jul/Oct/Jan | Summary — QRMP scheme |
| GSTR-9 | Annual | 31 Dec | Annual GST return |
| GSTR-9C | Annual | 31 Dec | Reconciliation statement (> ₹5 Cr) |
| TDS Deposit | Monthly | 7th | Deposit TDS/TCS for previous month |
| TDS Return 24Q | Quarterly | 31st of Jul/Oct/Jan/May | Salary TDS return |
| TDS Return 26Q | Quarterly | 31st of Jul/Oct/Jan/May | Non-salary TDS return |
| Form 16 / 16A | Annual | 15 Jun | TDS certificates to deductees |
| Advance Tax Q1 | Annual | 15 Jun | 15% of annual liability |
| Advance Tax Q2 | Annual | 15 Sep | 45% cumulative |
| Advance Tax Q3 | Annual | 15 Dec | 75% cumulative |
| Advance Tax Q4 | Annual | 15 Mar | 100% cumulative |
| ITR (non-audit) | Annual | 31 Jul | Individuals/HUF not requiring audit |
| ITR (audit) | Annual | 31 Oct | Tax audit cases |
| Tax Audit Report | Annual | 30 Sep | Form 3CA/3CB/3CD |
| Transfer Pricing | Annual | 31 Oct | Form 3CEB |
| AOC-4 | Annual | 29 Nov | Filing of financial statements |
| MGT-7 / 7A | Annual | 28 Nov | Annual return (ROC) |
| DIR-3 KYC | Annual | 30 Sep | Director KYC renewal |
| PF Deposit | Monthly | 15th | Provident Fund contribution |
| ESI Deposit | Monthly | 15th | ESIC contribution |
| PT Return (MH) | Monthly | 31st | Maharashtra Professional Tax |

---

## 10. SaaS Pricing & Plan Limits

| Plan | Price | `TENANT_PLAN` value | Intended use |
|---|---|---|---|
| Starter | ₹2,999/month | `starter` | Solo CA — 1 client firm, 50 tasks/month |
| Pro | ₹7,999/month | `pro` | Small firm — up to 5 client firms |
| Agency | ₹1,999/month per firm | `agency` | Large firm / reseller — unlimited clients |

The `TENANT_PLAN` secret is injected per agent in Paperclip. `agent.py` can read it via `os.environ.get("TENANT_PLAN")` to gate features (e.g. limit ROC filing to Pro+).

LLM cost is tracked per company by Paperclip's built-in budget system. Set a monthly budget cap in the Paperclip UI per company to enforce plan limits.

---

## 11. Extending the System

### Add a new task type

1. Add an entry to `TASK_TEMPLATES` in `saas/app.py`.
2. Add a card in `saas/templates/new_task.html`.
3. Add a keyword to `classify_task()` in `ca-agent/ca_knowledge.py` if needed.
4. Add the form to `STATUTORY_CALENDAR` in `ca_knowledge.py` if it has a fixed due date.

### Add a new statutory form

In `ca_knowledge.py`, append to `STATUTORY_CALENDAR`:
```python
{"form": "Form XYZ", "freq": "annual", "months": [3], "due_day": 31, "desc": "Description"},
```
Supported `freq` values: `monthly`, `quarterly`, `annual`.

### Use a different LLM

In `saas/.env` set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, and set `LLM_PROVIDER` to `anthropic` or `openai` in the Paperclip agent secrets.

### Add WhatsApp notifications

Use the Twilio or Meta WhatsApp Business API. After `agent.py` posts a comment and marks the issue done, add a call to a `notify_whatsapp(phone, message)` helper using the tenant's phone number (stored in the `users` table).

### Multi-region / sharding

Each Paperclip instance can serve one region. Deploy separate Paperclip servers for North/South/West India and route new signups to the nearest one based on the firm's state code (from GSTIN characters 1–2).

# CA Firm Agent for Paperclip

A Python CLI agent built **on top of Paperclip** for Indian CA offices.
Paperclip orchestrates it; this agent does the actual CA work.

## Architecture

```
Paperclip Server (localhost:3100)
    │
    │  heartbeat (CLI spawn)
    ▼
agent.py  ─── paperclip_client.py  ──►  Paperclip REST API
    │
    ├── ca_knowledge.py   (GST/ITR/TDS/ROC due dates, prompts)
    └── llm_client.py     (Anthropic Claude / OpenAI)
```

Paperclip runs `python agent.py` on each heartbeat.
The agent reads `PAPERCLIP_*` env vars, fetches the assigned issue,
calls an LLM with CA-specific context, and posts the result back.

## Setup

### 1. Install Python dependencies

```bash
cd "c:\agents\office automation\ca-agent"
pip install -r requirements.txt
```

### 2. Set your LLM API key

```powershell
# Anthropic (default)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# OR OpenAI
$env:LLM_PROVIDER = "openai"
$env:OPENAI_API_KEY = "sk-..."
```

### 3. Make sure Paperclip is running

```powershell
npx paperclipai onboard --yes
# Open http://localhost:3100
```

### 4. Register the agent in Paperclip

In the Paperclip UI (`http://localhost:3100`):

1. Open your company → **Agents** → **Hire Agent**
2. Fill in:

| Field | Value |
|-------|-------|
| Name | `CA Compliance Agent` |
| Adapter | `cli` |
| Command | `python agent.py` |
| Working directory | `C:\agents\office automation\ca-agent` |
| Environment | `ANTHROPIC_API_KEY=sk-ant-...` |

3. Set a **heartbeat schedule** (e.g., every 30 minutes, or on task assignment).
4. Optionally add the `ca-agent` skill (point to `skills/ca-agent/`).

### 5. Create your first task

In Paperclip, create an issue like:
- "Prepare GSTR-3B for ABC Pvt Ltd — May 2026"
- "Reconcile ITC for XYZ Ltd Q4 FY25-26"
- "Show upcoming compliance due dates for June 2026"
- "Calculate advance tax for Rajesh Kumar FY 2025-26 — Net income ₹18L"

Assign it to the CA Compliance Agent. The agent will pick it up on the next heartbeat.

## Multi-agent setup (recommended for larger firms)

Create one agent per specialisation:

| Agent | Command | Domain |
|-------|---------|--------|
| GST Agent | `python agent.py` | GST returns, ITC recon |
| IT Agent | `python agent.py` | ITR, advance tax, TDS |
| ROC Agent | `python agent.py` | MCA filings, company law |
| Payroll Agent | `python agent.py` | PF, ESI, PT, salary |
| Audit Agent | `python agent.py` | Statutory/internal audit |

Each agent is the same code — the CA domain knowledge and task classifier route
the LLM prompt to the right domain. For full separation, set different
`LLM_PROVIDER` / model per agent via Paperclip secrets.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required if using Anthropic |
| `OPENAI_API_KEY` | — | Required if using OpenAI |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5` | Anthropic model |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model |
| `LLM_MAX_TOKENS` | `4096` | Max response tokens |
| `PAPERCLIP_API_URL` | `http://localhost:3100` | Injected by Paperclip |
| `PAPERCLIP_API_KEY` | — | Injected by Paperclip |
| `PAPERCLIP_AGENT_ID` | — | Injected by Paperclip |
| `PAPERCLIP_COMPANY_ID` | — | Injected by Paperclip |
| `PAPERCLIP_RUN_ID` | — | Injected by Paperclip |
| `PAPERCLIP_TASK_ID` | — | Injected by Paperclip (issue to work on) |

## File structure

```
ca-agent/
├── agent.py              # Main entrypoint — Paperclip CLI adapter
├── paperclip_client.py   # Paperclip REST API wrapper
├── ca_knowledge.py       # CA domain: due dates, classifier, LLM prompt
├── llm_client.py         # LLM abstraction (Anthropic / OpenAI)
├── requirements.txt
├── README.md
└── skills/
    └── ca-agent/
        └── SKILL.md      # Paperclip skill definition
```

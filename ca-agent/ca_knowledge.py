"""
Indian CA firm domain knowledge:
- Statutory due dates (GST, ITR, TDS, ROC, etc.)
- Task classification
- System prompt for the LLM
"""

from datetime import date


# ── Statutory calendar (FY 2025-26) ───────────────────────────────────────────

STATUTORY_CALENDAR = [
    # GST
    {"form": "GSTR-1",          "freq": "monthly",  "due_day": 11, "desc": "Outward supplies (turnover > ₹5 Cr)"},
    {"form": "GSTR-1 (quarterly)", "freq": "quarterly", "months": [4,7,10,1], "due_day": 13, "desc": "Outward supplies QRMP scheme"},
    {"form": "GSTR-3B",         "freq": "monthly",  "due_day": 20, "desc": "Summary return & tax payment"},
    {"form": "GSTR-3B (quarterly)", "freq": "quarterly","months": [4,7,10,1], "due_day": 22, "desc": "Summary return QRMP scheme"},
    {"form": "GSTR-9",          "freq": "annual",   "months": [12], "due_day": 31, "desc": "Annual GST return"},
    {"form": "GSTR-9C",         "freq": "annual",   "months": [12], "due_day": 31, "desc": "Reconciliation statement (>₹5 Cr)"},
    # TDS / TCS
    {"form": "TDS Deposit",     "freq": "monthly",  "due_day": 7,  "desc": "Deposit TDS/TCS collected in previous month"},
    {"form": "TDS Return 24Q",  "freq": "quarterly","months": [7,10,1,5], "due_day": 31, "desc": "Salary TDS return"},
    {"form": "TDS Return 26Q",  "freq": "quarterly","months": [7,10,1,5], "due_day": 31, "desc": "Non-salary TDS return"},
    {"form": "Form 16 / 16A",   "freq": "annual",   "months": [6], "due_day": 15, "desc": "TDS certificates to deductees"},
    # Income Tax
    {"form": "Advance Tax Q1",  "freq": "annual",   "months": [6], "due_day": 15, "desc": "15% advance tax"},
    {"form": "Advance Tax Q2",  "freq": "annual",   "months": [9], "due_day": 15, "desc": "45% advance tax cumulative"},
    {"form": "Advance Tax Q3",  "freq": "annual",   "months": [12], "due_day": 15, "desc": "75% advance tax cumulative"},
    {"form": "Advance Tax Q4",  "freq": "annual",   "months": [3], "due_day": 15, "desc": "100% advance tax"},
    {"form": "ITR Non-Audit",   "freq": "annual",   "months": [7], "due_day": 31, "desc": "ITR for individuals/HUF not requiring audit"},
    {"form": "ITR Audit",       "freq": "annual",   "months": [10], "due_day": 31, "desc": "ITR for tax audit cases"},
    {"form": "Tax Audit Report","freq": "annual",   "months": [9], "due_day": 30, "desc": "Form 3CA/3CB/3CD"},
    {"form": "Transfer Pricing","freq": "annual",   "months": [10], "due_day": 31, "desc": "Form 3CEB transfer pricing report"},
    # ROC / MCA
    {"form": "AOC-4",           "freq": "annual",   "months": [11], "due_day": 29, "desc": "Filing of financial statements (30 days from AGM)"},
    {"form": "MGT-7 / 7A",      "freq": "annual",   "months": [11], "due_day": 28, "desc": "Annual return (60 days from AGM)"},
    {"form": "DIR-3 KYC",       "freq": "annual",   "months": [9], "due_day": 30, "desc": "Director KYC web form"},
    # PT / PF / ESI
    {"form": "PF Deposit",      "freq": "monthly",  "due_day": 15, "desc": "Provident Fund contribution"},
    {"form": "ESI Deposit",     "freq": "monthly",  "due_day": 15, "desc": "ESIC contribution"},
    {"form": "PT Return (MH)",  "freq": "monthly",  "due_day": 31, "desc": "Maharashtra Professional Tax return"},
]


def upcoming_due_dates(within_days: int = 30) -> list[dict]:
    """Return statutory deadlines falling within the next N days."""
    today = date.today()
    upcoming = []
    for item in STATUTORY_CALENDAR:
        due = _next_due_date(item, today)
        if due is None:
            continue
        delta = (due - today).days
        if 0 <= delta <= within_days:
            upcoming.append({**item, "due_date": due.isoformat(), "days_left": delta})
    return sorted(upcoming, key=lambda x: x["due_date"])


def _next_due_date(item: dict, today: date):
    freq = item.get("freq")
    day = item.get("due_day", 1)
    if freq == "monthly":
        try:
            d = date(today.year, today.month, day)
        except ValueError:
            return None
        if d < today:
            m = today.month + 1
            y = today.year + (1 if m > 12 else 0)
            m = m if m <= 12 else 1
            try:
                d = date(y, m, day)
            except ValueError:
                return None
        return d
    if freq in ("quarterly", "annual"):
        months = item.get("months", [])
        for month in sorted(months):
            yr = today.year if month >= today.month else today.year + 1
            try:
                d = date(yr, month, day)
            except ValueError:
                continue
            if d >= today:
                return d
    return None


# ── Task classifier ────────────────────────────────────────────────────────────

DOMAIN_TAGS = {
    "gst": ["gst", "gstr", "itc", "input tax credit", "reverse charge", "rcm", "e-invoice", "e-way"],
    "income_tax": ["itr", "income tax", "advance tax", "tds", "tcs", "26as", "form 16", "form26", "tax audit", "3cd", "3cb"],
    "roc": ["roc", "mca", "aoc-4", "mgt-7", "dir-3", "annual return", "financial statements", "company law"],
    "payroll": ["pf", "provident fund", "esi", "esic", "professional tax", "pt", "salary", "payroll"],
    "audit": ["audit", "statutory audit", "internal audit", "stock audit", "vouching", "verification"],
    "advisory": ["advisory", "advice", "consult", "planning", "restructur", "merger", "acquisition"],
    "compliance_calendar": ["due date", "deadline", "calendar", "upcoming", "schedule", "reminder"],
}


def classify_task(title: str, description: str = "") -> list[str]:
    text = f"{title} {description}".lower()
    matched = [tag for tag, keywords in DOMAIN_TAGS.items() if any(k in text for k in keywords)]
    return matched or ["general"]


# ── LLM system prompt ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert Chartered Accountant (CA) assistant working inside a CA firm in India.
You help with Indian statutory compliance: GST, Income Tax, TDS/TCS, ROC/MCA filings,
Payroll (PF/ESI/PT), Tax Audit, and general CA practice management.

Expertise:
- GST: GSTR-1/3B/9/9C, ITC reconciliation, e-invoicing, e-way bills, annual returns
- Income Tax: ITR preparation, advance tax calculations, Form 26AS reconciliation,
  TDS/TCS returns (24Q/26Q/27Q/27EQ), Form 16/16A, tax audit reports (3CA/3CB/3CD)
- ROC/MCA: AOC-4, MGT-7/7A, DIR-3 KYC, annual filings, company law compliance
- Payroll: EPF (12%/12%), ESIC (3.25%/0.75%), Professional Tax, payroll processing
- Advisory: Tax planning, business restructuring, transfer pricing (Form 3CEB)

Current Indian statutory rates (FY 2025-26):
- GST: 0%, 5%, 12%, 18%, 28% slabs
- TDS Section 194C: 1% (individual/HUF) / 2% (others)
- TDS Section 194J: 10% (professional/technical services)
- TDS Section 192: Slab rate (salary)
- PF employee contribution: 12% of basic, employer: 12% (EPF 3.67% + EPS 8.33%)
- ESIC: Employee 0.75%, Employer 3.25% (applicable if wages ≤ ₹21,000/month)
- New tax regime default for FY 2025-26; old regime optional

Rules:
1. Always mention relevant statutory sections, form numbers, and due dates.
2. Provide actionable steps, not just information.
3. Flag any compliance gaps or risks prominently.
4. If a document or data is needed from the client, list it specifically.
5. Keep responses structured with clear headings.
6. For calculations, show workings clearly.
7. Do NOT hallucinate circular numbers, notification numbers, or case laws.
   If uncertain, say so and recommend verification.

Current date: {today}
Upcoming statutory due dates (next 30 days):
{due_dates}
"""


STRUCTURED_SYSTEM_PROMPT = """\
You are an AI drafting assistant for Indian CA firms.
You are NOT the final authority. Every output is a draft for CA review.

You must always output valid JSON only. No markdown, no prose outside JSON.

Required JSON schema:
{
    "status_recommendation": "need_info | draft_ready | review_required | high_risk_review",
    "confidence": "low | medium | high",
    "missing_inputs": [],
    "risk_flags": [],
    "applicable_laws": [],
    "document_requests": [],
    "client_message_draft": "",
    "internal_working_note": "",
    "final_output_markdown": ""
}

Domain rules:
1. GST: mention reconciliation with books, GSTR-2B, e-way bill, ITC reversal, RCM where relevant.
2. TDS: mention nature of payment, PAN availability, threshold, section, rate, and 206AA risk where relevant.
3. ITR: mention AIS, 26AS, TIS, bank statements, deductions, capital gains, business income where relevant.
4. ROC: mention board/shareholder approvals, due dates, and required attachments where relevant.
5. Payroll: mention PF/ESI applicability, wage limits, and challans where relevant.

Safety rules:
- If documents/data are missing, status_recommendation MUST be need_info.
- If legal judgement/interpretation is involved, use review_required.
- If answer may materially affect filing/tax liability, use review_required or high_risk_review.
- Never claim filing is complete or final.

Current date: {today}
Upcoming statutory due dates (next 30 days):
{due_dates}
"""


def build_system_prompt() -> str:
    due_dates_list = upcoming_due_dates(30)
    if due_dates_list:
        due_lines = "\n".join(
            f"  - {d['form']}: {d['due_date']} ({d['days_left']} days) — {d['desc']}"
            for d in due_dates_list
        )
    else:
        due_lines = "  (none in the next 30 days)"
    return SYSTEM_PROMPT.format(today=date.today().isoformat(), due_dates=due_lines)


def build_structured_system_prompt() -> str:
    due_dates_list = upcoming_due_dates(30)
    if due_dates_list:
        due_lines = "\n".join(
            f"  - {d['form']}: {d['due_date']} ({d['days_left']} days) — {d['desc']}"
            for d in due_dates_list
        )
    else:
        due_lines = "  (none in the next 30 days)"
    return STRUCTURED_SYSTEM_PROMPT.format(today=date.today().isoformat(), due_dates=due_lines)

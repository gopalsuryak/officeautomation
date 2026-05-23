# UI/UX Redesign — Human + Agent Collaborative Workspace

> **Prepared:** May 23, 2026  
> **Status:** Proposal / blueprint — no code yet  
> **Scope:** Full app shell, IA, task lifecycle, agent assignment, review authorization, design tokens, component library, motion, accessibility, rollout plan  
> **Audience:** Product, design, engineering, founder

---

## 0. Executive Summary

The current UI is a competent but conventional practice-management UI: sidebar nav, list pages, detail pages, status badges, audit logs. The product, however, is not conventional — **agents do real work alongside humans**. The UX must stop treating AI as a "feature" tucked into corners (a *Send to AI* button, a separate *Automation Centre*) and start treating agents as **first-class teammates** that appear everywhere a human appears: assignee dropdowns, avatars, mentions, activity feeds, review queues, calendars.

At the same time, the firm is regulated. **Every agent action must pass a human authorization gate before it has legal/financial effect.** That gate must be visible, fast, defensible, auditable, and never feel like bureaucracy.

This document defines the redesigned product:

1. A new **mental model**: Work = a request + a *team* (humans + agents) + an *outcome*. The team can be mixed; the outcome can only be released by a human.
2. A **new app shell** built around an Inbox + Workspaces + Command Bar — not a sidebar tree of modules.
3. A **unified Work object** that replaces "task / draft / review pack / queue item / dry-run" surface fragmentation.
4. A **two-track assignment model** — `doer` (human or agent) + `authorizer` (always human, with role-based escalation).
5. A new **Review Surface** ("Approve & Release") with diff view, citations, risk flags, one-click rollback, reason capture, and SLA timers.
6. A new **design system** ("Studio") — calmer typography, fewer pages, denser tables when wanted, an agent visual language distinct from but peer to humans.
7. A **migration path** from the current code without throwing away the 45 backend modules.

The headline brand promise:
> *"Your firm + a stable of agents. You stay in charge of every signature."*

---

## 1. Design Principles

These are non-negotiable. Every screen, copy decision, and API shape must defer to them.

| # | Principle | What it means | What it kills |
|---|-----------|--------------|--------------|
| 1 | **Agents are teammates, not buttons** | Agents have avatars, names, profiles, skills, "online" states, working hours (24/7), and rate limits. They show up in every assignee picker, mention, and calendar. | The standalone *AI Center*, the *Send to AI* CTA buried in task detail, the segregated *ai_outputs* table appearing as a separate concept in UI. |
| 2 | **No agent output goes live without a human "Release"** | Every agent-produced artefact (email, journal entry, GST return, reply to client, portal action) is a **proposal** until a human with the right role clicks **Release**. The proposal can be edited, partially accepted, rejected with reason, or rolled back within an SLA window. | The current `email_send_approvals` table being optional and inconsistently applied across email vs WhatsApp vs document workflows. |
| 3 | **One Work object** | A `Work` is anything with an owner, a state, a deadline, collaborators, attachments, conversation, and an outcome. Tasks, drafts, review packs, queue items, document requests all become *kinds* of Work. | The current six different "things you can be assigned" — each with its own list page, detail page, status semantics, and assignment column. |
| 4 | **Inbox, not modules** | The default landing is *Today* — what needs me, in order of urgency. Modules (GST, Communication, Audit) become **filters and tools**, not destinations. | The 12-entry sidebar that forces the user to remember which module a piece of work lives in. |
| 5 | **Authorization is contextual, not configured** | The system decides who can release based on the work's *kind*, *amount*, *client risk band*, and *agent confidence*. The user sees a single "Release" button — or a clear "Needs Partner" status. | Manual role gates on routes that block users without explaining why. |
| 6 | **Conversation over forms** | Every Work has a thread: human comments, agent reasoning, system events. Decisions happen inline (`@Aarya draft GSTR-1`, `/release`, `/reject vendor mismatch`). Forms are for new entities only. | Modal dialogs everywhere. |
| 7 | **Calm density** | High information density when looking at a list (clients, returns, tasks); calm whitespace when in focus mode (review, draft, conversation). Never both at once. | The current dashboard's "wall of cards" that mixes KPIs with action lists. |
| 8 | **Auditable by default** | Every state change, every release, every reason is part of the visible thread (not buried in a separate audit log). The audit log becomes a *view* of the thread. | The separate *Audit Logs* page nobody opens. |

---

## 2. Mental Model

### 2.1 Entities

```
Firm ── has many ─▶ Workspaces (one per Client, plus internal)
Workspace ── has many ─▶ Works
Work ── has ─▶ Team (1+ doers, 1+ authorizers)
Team member ── is either ─▶ Person  OR  Agent
Work ── has ─▶ Thread (events, comments, proposals, releases)
Work ── produces ─▶ Outcomes (Email sent, Return filed, Journal posted, …)
```

Every legal action (a sent email, a filed return, a posted journal, a portal click) is an **Outcome**. Outcomes are immutable once released. They link back to the Work, the proposing party (human or agent), and the releasing human.

### 2.2 The Work State Machine (Unified)

The current product has five different state machines (`compliance_tasks`, `email_queue`, `document_communication_drafts`, `gstr3b_review_pack`, `document_requests`). All collapse to one:

```
   New ─▶ In Progress ─▶ Proposed ─▶ In Review ─▶ Released
                              │           │            │
                              ▼           ▼            ▼
                          Rejected    Changes        Filed
                                     Requested
```

- **New** — work item exists, no doer yet.
- **In Progress** — assigned to a doer (human or agent); work is happening.
- **Proposed** — doer has produced a draft outcome. If doer = human, the human can choose to skip review (depends on policy). If doer = agent, this state is **mandatory**.
- **In Review** — an authorizer is examining the proposal.
- **Changes Requested** — bounced back with comments; goes back to *In Progress*.
- **Released** — outcome is committed (email queued for SMTP, return filed, etc.).
- **Filed** — terminal; archived; appears in compliance ledger.
- **Rejected** — terminal; reason captured; counts against agent's quality score if agent-proposed.

Optional **Rollback Window** (default 30 min, configurable per outcome kind): a released outcome can be revoked by the authorizer or any partner within the window. After the window, rollback requires a corrective Work item.

### 2.3 Two-Track Assignment

A Work has exactly:

- **One Doer** at any time (`doer_kind: 'person' | 'agent'`, `doer_id: int`). Reassignable.
- **One Primary Authorizer** (`authorizer_user_id`), always a person. Determined by policy when Work moves to *Proposed*; reassignable.
- Zero or more **Watchers** (notification-only).
- Zero or more **Co-doers** (optional helpers — e.g. junior collects docs, agent drafts return).

Policy decides the authorizer:

| Trigger | Authorizer |
|---|---|
| Email to client, agent-drafted | The relationship owner (manager+) |
| GST return filing | The signing partner for that client |
| Journal entry > ₹50,000 | Partner of the workspace |
| Portal login action (Playwright) | Partner who owns the credential |
| Vendor master change | Manager (any) |
| WhatsApp template send | Senior+ |

Policies are configurable in **Settings → Authorization Matrix**, not hard-coded.

---

## 3. Information Architecture

### 3.1 Old IA (current)

```
Dashboard
Clients
Tasks
─────────────── AI ───────────────
AI Automation Center
Automation Registry
Credential Vault
Portal Readiness
Accounting Connectors
Accounting Data
GST Reconciliation
GST Control Room
GSTR-3B Review Packs
Jarvis Assistant
───────── Communications ─────────
Communication Drafts
Email Queue / WhatsApp Queue
Email Delivery Logs / Provider Settings
Email Operations / QA Dashboard / Readiness
─────────── Admin ────────────
Audit Logs / Usage / Billing
```

12 categories, 30+ destinations. Cognitive load is enormous.

### 3.2 New IA (proposed)

```
[ ⌘K Command Bar — always available ]

INBOX                               (default landing)
  ▸ For Me
    ─ Releases waiting (authorizer queue)
    ─ Assigned to me (doer queue)
    ─ Mentions & comments
    ─ Following
  ▸ For My Firm
    ─ Unassigned
    ─ Overdue
    ─ Stuck (no movement > N hours)

WORK
  ▸ All Work (filterable: client, kind, state, doer, authorizer, due)
  ▸ Calendar (deadlines)
  ▸ Boards (kanban per workspace)

CLIENTS                             (= Workspaces)
  ▸ List
  ▸ [Client] → Overview, Compliance, Books, Communication, Documents, Portal, People, Settings

LEDGER                              (outcomes — the "what got done" view)
  ▸ Filings (GST, TDS, ROC, ITR)
  ▸ Communications sent
  ▸ Journal entries posted
  ▸ Portal actions
  ▸ Rollback window

TEAM
  ▸ People (firm staff)
  ▸ Agents  ← new top-level
    ─ Roster (all available agents, capability matrix)
    ─ Performance (acceptance rate, rework rate, avg cost)
    ─ Hire (enable / disable agents per firm)
  ▸ Authorization Matrix
  ▸ Working hours & on-call

SETTINGS
  ▸ Firm, Plan, Billing
  ▸ Credentials (was Vault)
  ▸ Connectors (Email, Accounting, WhatsApp, Portals)
  ▸ Audit Trail (becomes a *view* of the global thread)
```

7 top-level destinations. Everything else is a filter, a tool inside a Workspace, or summoned via the Command Bar.

### 3.3 Command Bar (`⌘K` / `Ctrl+K`)

A single text input that becomes the primary navigation and action surface. Examples:

```
> gstr1 acme march            → jumps to the Work, or creates one
> @aarya draft reply to vendor → drafts an email via agent Aarya inside current Work
> release                     → releases the current Proposed outcome
> reject vendor mismatch      → rejects with reason
> assign rahul                → reassigns doer
> add reviewer partner        → adds an authorizer
> upcoming gst                → opens Calendar filtered to GST
> who has acme tds            → shows the Work + team
> hire credit-note-agent      → opens the Agent hiring modal
```

Slash commands inside threads mirror the same vocabulary.

---

## 4. The Three Hero Screens

### 4.1 Inbox — "For Me" (default landing)

A two-column layout:

- **Left rail (320 px)** — vertical list of items grouped by *Releases waiting* → *Assigned to me* → *Mentions* → *Following*. Each item is a single line: avatar of doer (human or agent), Work title, client chip, due chip, last-event timestamp.
- **Right pane (rest)** — the selected item opens fully (see §4.3 Work Detail).

Keyboard-driven: `j/k` to move, `Enter` to release, `e` to edit doer, `c` to comment, `g i` to go back to Inbox.

**Why this matters**: today the only way to find "what needs me" is to scan task lists across multiple modules and notice a yellow status pill. Authorizers complain (in beta) that they miss reviews. Inbox makes "needs me" the default view.

### 4.2 Workspace Overview (per Client)

Replaces the current `client_entities` and per-client pages. One screen, four bands:

1. **Header** — client name, GSTIN, PAN, plan, relationship owner, agent roster assigned to this client, "active risks" (e.g. pending portal verification, GSTR-3B due in 3 days, unreconciled ledger).
2. **Compliance band** — calendar strip of upcoming statutory items (GSTR-1, GSTR-3B, TDS, advance tax, ITR, ROC). Each tile shows the Work, current doer (human or agent), days to due, current state. Tiles are draggable to reassign doer.
3. **Books band** — connector status, last sync, unreconciled count, AR/AP snapshot, recent journal entries pending release.
4. **Communication band** — recent threads with client (email + WhatsApp), in one feed. Open one to see proposed replies (agent-drafted) waiting for release.

No tabs. Everything visible. Compresses the current 8-route client detail down to one scrollable canvas.

### 4.3 Work Detail (the heart of the product)

Three-pane layout:

```
┌──────────────────────┬─────────────────────────────┬────────────────────┐
│  Context (left)      │  Conversation + Proposal    │  Actions (right)   │
│                      │  (center)                   │                    │
│  - Title             │                             │  ┌──────────────┐  │
│  - Client            │  [System] Created by Rahul  │  │ Release ▼    │  │
│  - Kind & subkind    │  [Agent] Aarya started      │  └──────────────┘  │
│  - Due date          │  [Agent] Aarya finished →   │  Release options:  │
│  - Doer (avatar)     │     Proposed Email          │  • Release & send  │
│  - Authorizer (av.)  │     Confidence 0.86         │  • Release & hold  │
│  - Watchers          │     Citations: 3 docs       │  • Release partial │
│  - Linked Works      │     Risk flags: none        │                    │
│  - Attachments       │     [Diff view]             │  Other:            │
│  - Outcomes (so far) │     [Open full draft]       │  • Request changes │
│                      │                             │  • Reject (reason) │
│                      │  [Rahul] Looks good but the │  • Reassign        │
│                      │     PAN is wrong on line 4. │  • Add reviewer    │
│                      │                             │  • Snooze          │
│                      │  [/release after fix]       │                    │
│                      │                             │  ─────────────     │
│                      │  ┌─────────────────────┐    │  Authorization     │
│                      │  │ Compose reply...    │    │  Required: Partner │
│                      │  │ /command            │    │  (auto-routed to   │
│                      │  └─────────────────────┘    │   Suresh)          │
│                      │                             │                    │
└──────────────────────┴─────────────────────────────┴────────────────────┘
```

The **center column** is the unified Thread. It contains:

- **System events** (state changes, assignments) rendered as muted single-line entries.
- **Comments** from people (white card, full markdown, can `@mention` people or `@agents`, can attach files).
- **Agent runs** rendered as a "proposal card": title, what was produced, confidence, citations, risk flags, links to a diff view (against last version or against a template), and quick-actions (Release / Request changes / Reject).
- **Outcome events** (when something was actually sent/filed) — large green confirmation card with rollback timer.

The **right column** is the authorization surface. It is the only place where the **Release** button exists. Disabled if the current user is not authorized; in that case shows "Routed to *Suresh (Partner)*", and the current user can `Nudge`, `Reassign authorizer`, or `Escalate`.

---

## 5. Agents as First-Class Teammates

### 5.1 Agent Profile

Every agent has a profile page (`/team/agents/aarya-gst`) with:

- **Avatar + name** (e.g., *Aarya*, *Kiran*, *Vir* — first names, not "GST Agent v2"). Avatar is a distinct shape language (rounded square vs human circular avatars) so the eye instantly distinguishes agent from person.
- **Skills** — typed capabilities (`can: draft_gstr1, reconcile_2b, draft_client_email_en, draft_client_email_hi`). Skills drive who shows up in assignee dropdowns.
- **Authorization required** — for each skill, the default authorizer role (`gstr1: partner`, `client_email: manager`).
- **Confidence policy** — minimum confidence below which the agent must auto-attach a "low confidence" warning.
- **Working hours** — agents say "always on" but rate-limited (e.g., max 20 Works in parallel; configurable to control LLM cost).
- **Cost** — per-Work cost (LLM tokens + tool calls). Visible per Work in the proposal card.
- **Performance** — last 90 days: acceptance rate, rework rate, rejection reasons cloud, avg time-to-proposal, total cost.
- **Recent Works** — last 20 Works the agent touched.
- **Disable/Enable** — instantly stop the agent firm-wide (kill switch). Required for incident response.

### 5.2 Hiring & Firing Agents

A firm picks which agents are available (the **Roster**). Owners can browse the **Catalogue** — a list of available agent classes (GST, TDS, ROC, Email, WhatsApp, Reconciliation, Voice). Clicking *Hire* enables the agent firm-wide. Each agent shows what it does, sample output, cost band, required API key (OpenAI, Anthropic, Gemini), and required permissions (e.g., GST portal credentials).

Owners can also **Fire** — disable the agent (works gracefully; in-flight Works complete then no new assignment).

This screen is the new home for what is today *Automation Registry* — but framed as people-hiring, not configuration management.

### 5.3 Assigning Work to Agents

In any assignee picker:

```
Assign doer
┌────────────────────────────┐
│ 🔍 Search people & agents…  │
├────────────────────────────┤
│ People                     │
│ ● Rahul (Sr. Associate)    │
│ ● Suresh (Partner)         │
│ ● Pooja (Manager)          │
├────────────────────────────┤
│ Agents — Skilled           │
│ ◆ Aarya     GSTR-1 drafts  │
│ ◆ Kiran     Vendor email   │
├────────────────────────────┤
│ Agents — Unskilled         │
│ ◇ Vir       (no skill match)│
└────────────────────────────┘
```

Humans = filled circle, agents = filled diamond — instantly distinguishable. Agents whose declared skills don't match the Work's kind are shown but greyed; selecting an unskilled agent is allowed but shows a warning.

### 5.4 Mentioning Agents in a Thread

In any comment box:

```
@aarya please redraft using the revised vendor list
```

→ Posts the comment, assigns Aarya as co-doer if not already, kicks off a new agent run, posts the result as a Proposal card in the same thread. No leaving the screen.

### 5.5 Agent "Online" State

Agents have three states surfaced as a dot on the avatar:

- 🟢 **Available** — healthy, under rate limit, API key valid.
- 🟡 **Busy** — at rate limit; new Works queue but won't start immediately.
- 🔴 **Offline** — kill-switched, credentials missing, or upstream API failing. Hover for reason. Assigning to an offline agent is blocked.

---

## 6. The Review / Authorization Surface

### 6.1 What a Reviewer Sees

Opening a Work in *Proposed* state lands on the **Proposal card** with the **Release** panel on the right. The Proposal card is the single most-designed component:

```
┌──────────────────────────────────────────────────────┐
│ ◆ Aarya proposed an email reply                      │
│   to Acme Industries · 2 minutes ago · Cost ₹0.42    │
│                                                      │
│   Subject:  Re: GST notice 2025-26                   │
│   ┌────────────────────────────────────────────┐     │
│   │ Dear Mr. Sharma,                          ▼│     │
│   │                                             │     │
│   │ Thank you for your message regarding ...    │     │
│   │  …                                          │     │
│   └────────────────────────────────────────────┘     │
│   [View full] [Diff vs template] [Open editor]      │
│                                                      │
│   Confidence: 0.86 ████████░░  (above threshold)    │
│   Citations:                                        │
│     • GST Notice 12-Apr-2026.pdf (uploaded by Pooja) │
│     • Statutory ref: Sec 73(1) CGST                  │
│     • Prior reply on 2 Feb 2026                      │
│   Risk flags: none                                   │
│   Tool calls: 3 (search docs · render template · …)  │
└──────────────────────────────────────────────────────┘
```

Every element is clickable and explains itself. Citations open the source document. Tool calls expand to show what the agent actually did (auditability).

### 6.2 Release Options

| Action | What happens |
|---|---|
| **Release** | Commits the outcome. If outcome = email, the email is queued to SMTP (still subject to provider/queue). If outcome = filing, the portal action runs (Playwright). If outcome = journal entry, the connector posts it. Visible in Ledger immediately. |
| **Release & Hold** | Records the human approval, but holds the outcome for a future moment (e.g. "release at 9 AM tomorrow"). |
| **Release Partial** | Available when proposal has multiple parts (e.g., 12 reconciliation entries). User ticks which to accept; rest go back to *In Progress* with a comment. |
| **Request Changes** | Returns to *In Progress* with a comment thread. Agent re-runs automatically; human doer is notified. |
| **Reject** | Terminal; requires a reason from a fixed list (`incorrect_facts`, `wrong_tone`, `policy_violation`, `low_quality`, `out_of_scope`, `other`). Reason feeds agent performance metrics. |
| **Reassign authorizer** | Routes to another eligible authorizer. Logged. |
| **Snooze** | Hides from Inbox until a chosen time. |

### 6.3 Authorization Routing

When a Work moves to *Proposed*, the system computes the required authorizer using the **Authorization Matrix**:

```
kind=email, channel=client          → manager+
kind=email, channel=internal_govt   → partner
kind=filing, statute=gstr1          → partner (signing)
kind=filing, statute=gstr3b         → partner (signing)
kind=journal, amount<=50000         → manager+
kind=journal, amount>50000          → partner
kind=portal_action                  → partner who owns credential
kind=whatsapp, template=marketing   → senior+
kind=whatsapp, template=ack         → assistant+
```

The matrix is editable in **Settings → Authorization Matrix** (owner-only). Routing picks the *named* authorizer for that client when known (e.g., signing partner), else the firm-wide on-call partner, else round-robins eligible authorizers, else lands in *Firm → Unassigned authorizer*.

### 6.4 Escalation & SLAs

Every Proposed Work has an **SLA timer** (default by kind: emails 2 hours business time, filings 1 business day, journal entries 4 hours). The Inbox shows time-to-SLA-breach. On breach, the system:

1. Re-notifies the authorizer (push + email).
2. CCs the firm owner after 1× SLA.
3. Auto-reassigns to on-call partner after 2× SLA.
4. Logs the escalation in the thread.

### 6.5 Rollback

Released outcomes show a **Rollback** action for a configurable window (default 30 minutes for emails, 0 for filings, 24h for journals). Rollback for emails attempts SMTP cancel if not yet handed off; otherwise sends a "please disregard" follow-up auto-drafted by an agent and queued for re-release. Rollback for journals reverses the entry with a linked reversal entry. Rollback for filings is not allowed (regulatory) — instead, the system creates a *correction Work*.

### 6.6 Bulk Authorization

For high-volume use (e.g., 200 agent-drafted vendor reminder emails on the 28th), the Inbox supports **bulk select**:

- Select multiple Proposed Works of the same kind.
- See aggregate stats (total recipients, total amount, lowest confidence, any risk flags).
- *Release All* requires the authorizer to type a reason and confirm count. Each individual release is still logged in its own Work; bulk is a UX shortcut, not a semantic change.

---

## 7. The New Design System: "Studio"

### 7.1 Identity

Brand promise: *competent, calm, modern, defensible*. Visual cues: precise typography, restrained colour, generous whitespace at decision points, dense tables at scanning points. **Never** the playful gradients of consumer AI; **never** the busy dashboards of legacy practice software.

### 7.2 Tokens

```
/* Colour — neutral-first */
--bg                  #f7f8fa
--surface             #ffffff
--surface-raised      #ffffff
--surface-sunken      #f0f2f6
--border              #e3e6ec
--border-strong       #c7cdd6
--text                #0e1320
--text-muted          #5d6675
--text-faint          #8a93a3

/* Accent — used sparingly */
--primary             #1f3bb3   /* CTA, links */
--primary-soft        #eaeefc

/* Semantic */
--success             #176d3a
--success-soft        #e6f3ec
--warning             #8a5a00
--warning-soft        #fff4d9
--danger              #a01818
--danger-soft         #fce7e7
--info                #0c5b6e
--info-soft           #e1f1f5

/* Agent visual language */
--agent               #5b3fb5   /* deep purple — distinct from primary blue */
--agent-soft          #ede8fb
--agent-border        #c8bce8

/* Risk bands */
--risk-low            #176d3a
--risk-med            #8a5a00
--risk-high           #a01818

/* Type */
--font-sans           "Inter", "Segoe UI Variable", system-ui, sans-serif
--font-mono           "JetBrains Mono", ui-monospace, monospace
--fs-12               12px
--fs-13               13px   /* base */
--fs-14               14px
--fs-16               16px
--fs-20               20px
--fs-24               24px
--fs-32               32px

/* Spacing — 4 px grid */
--s-1 4 --s-2 8 --s-3 12 --s-4 16 --s-5 24 --s-6 32 --s-7 48 --s-8 64

/* Radius */
--r-1 4 --r-2 6 --r-3 10 --r-4 14 --r-5 20

/* Elevation */
--e-0 none
--e-1 0 1px 1px rgba(14,19,32,.04), 0 1px 2px rgba(14,19,32,.06)
--e-2 0 4px 12px rgba(14,19,32,.08)
--e-3 0 12px 32px rgba(14,19,32,.12)
```

The current `app.css` has a similar but louder palette (primary `#1d4ed8`, warmer warning `#b45309`). The Studio palette is intentionally less saturated to age well and to make semantic colour (success/warning/danger) and agent purple **mean** something.

### 7.3 Human vs Agent — Visual Distinction

| Element | Person | Agent |
|---|---|---|
| Avatar shape | Circle | Rounded square |
| Border | none | 1 px `--agent-border` |
| Default fill | initials on muted | initials on `--agent-soft` |
| Mention chip | `@Rahul`, blue bg | `◆ @Aarya`, purple bg |
| Assignee dropdown row | filled circle • | filled diamond ◆ |
| Activity entry icon | round | square |
| In-thread card border | left border 2 px primary | left border 2 px agent |

This is the single most important visual choice in the system. Every screen, every list, every tooltip: you can tell at a glance which actors are human and which are not.

### 7.4 Components

A short catalogue (each gets its own page in the component library):

- **Work card** (compact, comfortable, expanded variants)
- **Assignee picker** (people + agents, skill-aware)
- **Proposal card** (the single most-designed component; see §6.1)
- **Release panel** (right rail of Work detail)
- **Thread entry** (system, comment, proposal, outcome, rollback)
- **Mention chip**, **Slash-command popover**, **Command bar**
- **Risk badge** (low/med/high, with hover-explain)
- **Confidence bar** (0–1 with threshold tick)
- **SLA timer chip** (green / amber / red with countdown)
- **State pill** (the six Work states with iconography)
- **Diff view** (text, table, JSON; side-by-side or inline; line numbers)
- **Citation chip** (filename or URL, hover preview, click to open)
- **Authorization badge** (who can release, why)
- **Workspace header**, **calendar strip**, **kanban column**, **inbox row**
- **Empty states** (illustrated, action-prompting)
- **Toast** (subtle, top-right, never blocks)
- **Form primitives** — text, select, combobox, multiselect, date, time, money, GSTIN, PAN, phone, email — each with format-on-blur and contextual validation.

### 7.5 Motion

Motion is functional, never decorative:

- **150 ms** ease-out for hover/press.
- **220 ms** ease-out for panel open.
- **400 ms** ease-out for new Proposal card flying in (subtle slide + fade).
- **0 ms** for state pill changes (instant) but pulse highlight for 600 ms on the row that changed.

No bouncing, no parallax, no scroll-jacking.

### 7.6 Density Modes

A user setting: **Comfortable** (default), **Compact**, **Spacious**. Inbox row heights and table padding switch. Stored per-user.

### 7.7 Dark Mode

Day-one requirement. CAs work late. Tokens inverted; agent purple slightly desaturated; semantic colours retuned for legibility.

### 7.8 Accessibility

- WCAG 2.2 AA contrast (verified for both modes).
- Every action available via keyboard.
- Visible focus rings always (`2 px --primary` outer, `2 px white` inner).
- Screen-reader labels on icon-only buttons.
- Agent avatars include text alternative: "Aarya (agent)".
- `prefers-reduced-motion` disables non-essential transitions.

---

## 8. Notifications & Activity

### 8.1 Channels

1. **In-app** — Inbox dot + browser title badge. Always.
2. **Email** — digest by default (3×/day at 9, 13, 17), or instant for SLA breaches and rollback windows.
3. **WhatsApp** — opt-in per user, for SLA breaches only.
4. **Browser push** — opt-in, for *Release waiting* events on user's authorizer queue.

### 8.2 Subscription Rules

- You are auto-subscribed to Works where you are doer, authorizer, watcher, or commenter.
- You can follow a client (subscribe to all Works in a workspace) or a kind (e.g., all GSTR-3B firm-wide).
- You can mute a Work without leaving it.

### 8.3 Activity Feed = Audit Log

The global activity feed is the audit log, rendered humanely. Filter by actor (person or agent), action, entity, client, date range. Export to CSV. Tamper-proof flag (hash-chained — see §10).

---

## 9. Page-by-Page Mapping (Old → New)

| Today | Tomorrow |
|---|---|
| Dashboard | Inbox › For Me |
| Clients (list) | Clients (list, same shape, denser) |
| Client detail (8 routes) | Workspace overview (single scrollable) |
| Tasks | Work › All Work |
| Compliance Tasks status pages | Filters on Work; Calendar view for due dates |
| AI Automation Center | Removed. Surfaces inside Inbox + Team › Agents |
| Automation Registry | Team › Agents › Roster + Catalogue |
| Credential Vault | Settings › Credentials |
| Portal Readiness | Workspace › Portal tab |
| Accounting Connectors | Settings › Connectors + Workspace › Books |
| Accounting Data Viewer | Workspace › Books |
| GST Reconciliation | Workspace › Compliance › GST + Work detail with diff view |
| GST Control Room (dashboard) | A saved filter inside Work |
| GSTR-3B Review Pack | A Work of kind `filing.gstr3b` with full review surface (Proposal card) |
| Communication Drafts | Removed. Drafts are Proposed Works of kind `communication.*` |
| Email Queue | Ledger › Communications + per-Work outcome card |
| WhatsApp Queue | Ledger › Communications |
| Email Delivery Logs | Ledger › Communications, with sub-filter `delivered/bounced/opened` |
| Email Provider Settings | Settings › Connectors › Email |
| Email Operations | Disappears — operations happen inside Work detail or via Command Bar |
| Email QA Dashboard | Team › Agents › Performance + Risk view in Work detail |
| Email Readiness | Settings › Connectors › Email (readiness check inline) |
| Jarvis Assistant | A floating button on every page → opens Command Bar in voice mode |
| Audit Logs | Settings › Audit Trail (= activity feed filtered) |
| Usage | Settings › Billing |
| Billing / Checkout | Settings › Billing |
| Manual Uploads | Inline action inside Workspace › Documents (drop zone) |

Net: **30+ routes → 7 destinations + Command Bar**. No backend code is deleted; the URLs become views.

---

## 10. Authorization, Trust, and Audit

### 10.1 Why "Human-in-the-loop" Must Be Designed, Not Bolted On

A CA firm carries personal regulatory liability for filings and client communications. The UX must:

1. Make it impossible to send/file without a clear human action.
2. Make that action effortful enough that the human reads it, but fast enough that they don't bypass it.
3. Capture intent (reason on reject; optional comment on release).
4. Be defensible later — show *who* released *what*, *when*, *why*, *based on which evidence (citations)*, and *how it was generated*.

### 10.2 The Authorization Matrix

Editable two-dimensional table (kind × role) with cell values = the minimum role required. Cells can be overridden per-client (e.g., Acme always needs partner; small clients allow manager). Changes to the matrix are themselves audited and require owner role.

### 10.3 The Audit Trail

Every state transition, comment, and outcome is written to a single append-only `events` table. Each row contains:

```
event_id, tenant_id, work_id, actor_kind (person|agent|system),
actor_id, action, payload_json, prev_hash, hash, created_at
```

`hash = SHA256(prev_hash || canonical(payload_json) || created_at)`.

The chain hash is exposed in the UI as a *Trust seal* on the bottom of every Work detail page: "🛡️ 247 events, chain verified". Clicking opens the integrity check.

### 10.4 Permissions

Six roles (`owner`, `partner`, `manager`, `senior`, `assistant`, `viewer`) with these high-level grants:

| Capability | owner | partner | manager | senior | assistant | viewer |
|---|---|---|---|---|---|---|
| View any Work in firm | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (read-only) |
| Create Work | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| Assign doer | ✓ | ✓ | ✓ | ✓ | own only | — |
| Release filings | ✓ | ✓ | — | — | — | — |
| Release client email | ✓ | ✓ | ✓ | — | — | — |
| Release journal ≤ 50k | ✓ | ✓ | ✓ | — | — | — |
| Release journal > 50k | ✓ | ✓ | — | — | — | — |
| Hire/fire agents | ✓ | — | — | — | — | — |
| Edit Authorization Matrix | ✓ | — | — | — | — | — |
| View Audit Trail | ✓ | ✓ | ✓ | ✓ | — | — |
| Manage credentials | ✓ | ✓ | — | — | — | — |
| Manage billing | ✓ | — | — | — | — | — |

Roles `senior` and `assistant` do not exist yet in the codebase — adding them is a prerequisite for this matrix.

---

## 11. Voice, Mobile, and Offline

### 11.1 Voice

Today *Jarvis Assistant* is a page. In the redesign, voice is a **mode of the Command Bar**, summoned by a floating mic button (bottom-right of every screen) or `⌘\`. Voice can:

- Search Work, clients, agents.
- Issue commands ("release this", "assign to Rahul", "draft a polite reply").
- Dictate comments.
- Read aloud the current Proposal (useful while driving — partner reviewing on the move).

### 11.2 Mobile

A responsive web shell, not a separate app. Inbox is the entire mobile experience. Releasing on mobile shows a condensed Proposal card optimized for thumb scrolling; large Release button at the bottom, like a payment app. Heavy actions (Workspace overview, Diff view) gracefully degrade or prompt to open on desktop.

### 11.3 Offline

A Service Worker caches the Inbox shell and the user's last 50 Works for read-only review (no Release while offline). Releases attempted offline are queued with a clear "Will release when online" indicator, and require re-confirmation on reconnect.

---

## 12. Empty States, First Run, and Onboarding

A typical CA firm signing up has zero clients, zero agents hired, zero credentials connected. The current UI dumps them on an empty Dashboard. The redesign uses a **Setup Inbox**:

1. Add your first client (or import CSV).
2. Connect at least one email provider.
3. Hire your first agent (recommends *Aarya* for GST).
4. Add your team (invite by email; assign roles).
5. Configure the Authorization Matrix (defaults shown, editable).
6. Run a sample Work end-to-end (a tutorial Workspace with fake data, including a fake Proposal to release).

Each setup item is itself a Work assigned to the firm owner — eats its own dog food.

---

## 13. Copywriting & Tone

- Verbs, not nouns: "Release", not "Submission for approval".
- Active voice: "Aarya drafted the reply", not "A draft was created by AI".
- No jargon for end-users: "filing" not "compliance task", "reply" not "communication artefact".
- Reason language for reject is *bounded* (fixed list) to keep agent metrics meaningful; free-text comments live in the thread.
- Agent names are short, neutral, gender-mixed (Aarya, Kiran, Vir, Tara, Daksh). Never "AI Agent #3".
- Error messages explain the cause and the fix in one sentence. ("This release needs a Partner. Routed to Suresh — nudge him?")

---

## 14. Telemetry & Quality Metrics

Per Work:

- Time-to-first-proposal
- Number of revision cycles before release
- Reject reason
- Authorizer release latency
- Rollback rate

Per agent:

- Acceptance rate (released / proposed)
- Rework rate (request-changes / proposed)
- Average confidence vs actual acceptance
- Cost per accepted Work
- Mean time-to-proposal

Per human:

- Median release latency
- Reject reason mix (to spot agent issues vs human preferences)
- SLA breach count

Per firm:

- Volume by kind
- % of releases requiring escalation
- Audit-trail integrity (chain verification)

All telemetry is tenant-scoped, exportable, and visible to the firm owner.

---

## 15. Migration Plan (UX-Only; No Backend Rewrite)

The product can ship this UX without changing 80% of the backend. Strategy: **add a presentation layer (Inbox, Work, Proposal) that reads existing tables**, then slowly fold legacy tables into the unified Work concept.

### Phase 1 — Foundations (Wave 15)
- Add `senior`, `assistant` roles.
- Build the Studio design system (tokens + 8 base components).
- Build the Command Bar shell (search + navigation only, no commands yet).
- Build the new app shell (top nav + Inbox skeleton) gated by a feature flag `studio_ui=true`.
- Introduce **Agent profile pages** reading `automation_registry`.

### Phase 2 — Work + Inbox (Wave 16)
- Introduce a `works` view that UNIONs `compliance_tasks`, `gstr3b_review_pack`, `email_send_approvals`, `document_communication_drafts`, `document_requests`.
- Build Inbox (For Me / For My Firm) reading the view.
- Build Work Detail (three-pane) — comments and state changes wired to existing tables.
- Build the Proposal card for one kind first (`email`) end-to-end.

### Phase 3 — Authorization & Release (Wave 17)
- Authorization Matrix (table + UI).
- Release / Request Changes / Reject actions wired to existing per-kind execution code.
- SLA timers + escalation jobs.
- Hash-chained `events` log.

### Phase 4 — Agents as Assignees (Wave 18)
- Add `doer_kind`/`doer_id` to `works` view.
- Replace human-only assignee pickers with mixed picker everywhere.
- Mention-to-summon (`@aarya draft …`) in threads.
- Agent rate limits, kill switch, online/offline status.

### Phase 5 — Workspace + Ledger (Wave 19)
- Workspace overview (per client) replacing the 8 client routes.
- Ledger (outcomes view) replacing Email Queue / WhatsApp Queue / Delivery Logs surface fragmentation.
- Calendar view.

### Phase 6 — Polish (Wave 20)
- Dark mode.
- Mobile.
- Voice command mode.
- Bulk authorization.
- Rollback flows for all kinds.
- Decommission legacy pages (URL redirects).

Each phase is independently shippable and reversible via the `studio_ui` flag.

---

## 16. What This Document Deliberately Does Not Decide

- **Specific agent names** (Aarya, Kiran, Vir are placeholders).
- **Exact font** (Inter is the default candidate; SF Pro / Söhne if budget allows).
- **Exact pricing** for agent runs surfaced to users (depends on contract with LLM providers).
- **WhatsApp/Voice provider** UI specifics (these are infrastructure).
- **Backend schema changes** beyond what §15 implies (separate engineering RFC).
- **Marketing site** redesign (out of scope).

---

## 17. Risks and Open Questions

| Risk | Mitigation |
|---|---|
| Authorizer fatigue (too many releases per day) | Bulk authorize; group by kind; smart batching of low-risk items; agent confidence threshold to auto-route low-confidence work to senior review only. |
| Rejected agent work becomes invisible | Reject reasons feed Agent › Performance; weekly digest to firm owner. |
| Authorization matrix complexity | Ship strong defaults; only owners can edit; per-client overrides are a power-user feature. |
| Migration parallel UIs (old + new) confuses users | Flag-gated rollout per firm; in-app banner to switch back; sunset old after Wave 20. |
| Voice command misinterpretation releases something wrong | Voice never releases without an explicit verbal confirmation ("Confirm release"). |
| Agents acting in offline mode | Agents do not act offline; they only propose. Release requires online + human. |
| Audit chain corruption | Daily background verify; alert on mismatch; signed snapshots to object storage. |
| Mobile thumb-reach on Release button | Bottom anchored, with confirm-by-hold (300 ms press) to avoid accidental release. |

---

## 18. Success Criteria (12 weeks post-launch)

1. ≥ 70 % of authorizers find new releases via Inbox (not via Dashboard or email).
2. Median release latency ↓ 40 % vs current baseline.
3. ≥ 85 % of Works produced by agents are released without rework on first review.
4. Zero un-authorized agent outcomes reach external recipients (verified via audit chain).
5. NPS from CAs ≥ 40; key qualitative line: *"feels like a teammate, not a tool"*.
6. Onboarding completion rate (Setup Inbox finished) ≥ 80 % for new firms within 7 days of signup.

---

## 19. Appendix A — Example User Stories

1. *Pooja (Manager)* opens the app at 9 AM. Inbox shows **3 Releases waiting**. She picks the first — an agent-drafted reply to Acme's GST notice. She reads the Proposal card, opens one citation to confirm a section reference, hits **Release**. The email queues. She moves to the next with `j`.

2. *Suresh (Partner)* gets a browser push at 5 PM: *"GSTR-3B for Bharat Foods is ready for your release"*. He opens the link on his phone, lands on the mobile Proposal view, taps **Release**. Confirm-by-hold (300 ms). Released.

3. *Rahul (Senior)* opens a Work and types in the thread: `@aarya re-reconcile this with the corrected vendor list`. Aarya posts a new Proposal in 90 seconds. Rahul makes one inline edit, then clicks Release — but the right-rail says *"Routed to Suresh"*. He clicks **Nudge**. Suresh approves from his Inbox 6 minutes later.

4. *Anita (Owner)* wants to disable the WhatsApp marketing agent because of a customer complaint. She opens **Team › Agents › Kiran**, clicks **Disable**. In-flight Works finish; no new ones start. The activity feed records the change.

5. *Vikram (new Assistant)* joins the firm. He sees only Works assigned to him in the Inbox. Trying to release a client email shows *"Needs Manager"* with a button **Hand off to Pooja**. He clicks; Pooja gets it.

---

## 20. Appendix B — Glossary

- **Work** — Any unit of work in the system; replaces task / draft / queue item / review pack.
- **Workspace** — A client (or the firm's internal workspace).
- **Doer** — The single party currently working on a Work; can be a Person or an Agent.
- **Authorizer** — The human empowered to release the proposed outcome.
- **Proposal** — A draft outcome produced by the doer, waiting for authorization.
- **Outcome** — A committed, real-world effect (email sent, return filed, journal posted).
- **Release** — The human act of converting a Proposal into an Outcome.
- **Rollback Window** — Time after release in which the outcome can still be revoked.
- **Authorization Matrix** — The table mapping Work kinds to minimum authorizer roles.
- **Agent Roster** — The set of agents hired by a firm.
- **Studio** — The codename for the new design system.

---

*End of document. Open for review.*

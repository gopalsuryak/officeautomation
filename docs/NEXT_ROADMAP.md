## SMTP Manual Send Worker (Live Phase)

- Approved-to-send queue items can be sent via SMTP with explicit final confirmation
- SMTP connection using STARTTLS (port 587) or SMTP_SSL (port 465)
- One email per explicit click; no bulk sending, no background worker, no auto-send
- Sent and failed status tracking with audit logs and task comments
- No Gmail, Zoho, WhatsApp, or background automation in this phase

## Email Module Final QA Dashboard (Live Phase)

- Read-only final QA dashboard for readiness KPIs and safety checklist status
- Before client usage, operations should run this dashboard and complete an internal SMTP test
- No send, retry, or bulk actions from this dashboard

## Email Readiness Checklist + Production Readiness Lock (Live Phase)

- Before client usage, operations must complete the internal readiness checklist and internal SMTP test
- Required checks must be completed before marking the module ready for controlled client testing
- Checklist is tracking-only and does not trigger sending

## Email Delivery Logs Register (Live Phase)

- Read-only register for approved_to_send, ready_to_send, sent, and failed queue records
- Filterable by client, task, provider, status, date range, and text search
- Cross-links to queue item, task, draft, and provider detail pages
- No send, retry, bulk action, or background operation from this register

## Future Phases: Document Communication

### SMTP Retry Workflow
- Auto-retry logic for failed SMTP sends (configurable retry count and backoff)
- Separate queue status for retry-pending items
- Operator-driven retry action with updated failure tracking

### SMTP Delivery Receipt and Status Logs
- Capture SMTP server response codes and delivery confirmations
- Create send receipt ledger for audit compliance
- Track bounce handling and undeliverable notifications

### Provider-Level Delivery Statistics
- Provider-wise sent/failed trend charts and failure-rate KPIs
- Provider uptime and authentication error breakdown
- Time-windowed stats to compare SMTP provider quality

### Provider Analytics Expansion
- Add deeper provider analytics beyond baseline failure-rate table
- Include latency/error-class distributions and trend deltas by period
- Add governance-friendly export views for internal QA audits

### SMTP Retry History Analytics
- Track reopen-after-failure timelines and operator notes
- Failure root-cause categorization and trend reporting
- Distinguish manual reopen events from future retry workflows

### Provider Failure Rate Dashboard
- Live provider failure-rate KPIs by period and tenant
- Alerting thresholds for repeated SMTP auth/connectivity failures
- Correlate failure rates with provider readiness checks

### Retry Policy (Future, Controlled)
- Introduce retry policy only after approval and reopen controls prove stable
- Require explicit governance for max attempts, cooldown, and auditability
- Keep manual SEND confirmation as a hard guardrail for sensitive cases

### Controlled Retry Policy Rollout
- Define explicit policy toggles and approval gates before any retry activation
- Preserve manual-only fallback path for sensitive communications
- Add retry audit trails with policy version tagging

### SMTP Provider Setup
- SMTP configuration per tenant
- Credentials vault for SMTP servers
- Test send functionality
- Email delivery status tracking

### Gmail OAuth Sending
- Gmail OAuth consent and token lifecycle
- Send reviewed email drafts via Gmail API
- Delivery tracking and bounce handling
- Audit log for each Gmail send action

### Gmail Integration (Future)
- Keep Gmail outside current live phase; implement only after QA/safety sign-off
- Require parity with SMTP safety controls (approval gate, auditability, non-bulk defaults)

### Zoho Mail SMTP Sending
- Zoho Mail SMTP credential configuration
- Send reviewed email drafts via Zoho SMTP
- Delivery and bounce tracking
- Audit log integration

### Zoho Integration (Future)
- Keep Zoho outside current live phase; implement only after QA/safety sign-off
- Mirror the same manual governance controls used in SMTP path

### Email Sending Worker
- Background job processor for queued emails (only after manual controls are proven stable)
- Scheduled send vs. immediate send
- Retry logic for failed sends
- Delivery log and audit trail

### Provider Credential Encryption
- Encrypt provider secrets at rest before any real send rollout
- Add controlled runtime decryption boundaries and audited access
- Add secret rotation support for SMTP/app-password and OAuth credentials

### SMTP Real Connection Test
- Introduce explicit SMTP connectivity test action (opt-in)
- Validate host/port/auth handshake without sending client communication by default
- Persist test telemetry and error classification

### WhatsApp Business API Integration
- WhatsApp message queue (similar to email queue)
- Manual copy-to-clipboard or API integration
- Delivery confirmation and read receipts
- Audit log for each WhatsApp action

### Reminder Scheduling
- Auto-reminder rules for pending document requests (only after sending controls mature)
- Configurable frequency (daily, weekly, monthly)
- Tenant-level override controls
- Audit log for automated sends

### Client Portal Email Integration
- Client-facing portal for document upload with email request status
- Linked to document requests
- Auto-mark document requests as received
- File storage and version tracking

## Previous Phases (Pre-SMTP Manual Send Worker)

### SMTP Dry Run Preview Evolution
- Extend dry-run preview into transport-level simulation with provider-specific envelope validation
- Add approval-gated transition from dry-run snapshot to dispatch-eligible artifact
- Keep strict no-send default until worker governance and credential controls are complete

### Email Operations Control Room Enhancements
- Add trend metrics for draft-to-queue conversion and provider assignment lag
- Add aging buckets and SLA indicators for queued and ready_to_send items
- Add operator ownership tags and escalation queues
- Keep dispatch disabled in control room until worker governance phases are approved

### Reviewed Email Sending Worker
- Move from internal queue metadata to controlled dispatch worker
- Enforce reviewed-only gate before dispatch
- Add idempotency keys and retry-safe send semantics

### Reviewed Email Sending Worker (Post Provider Assignment)
- Activate worker-driven dispatch only after provider encryption and manual approval controls
- Require queue provider assignment + reviewed draft gate before any dispatch attempt
- Keep explicit no-auto-send governance for high-risk client communications

### Gmail OAuth Send
- OAuth consent/token lifecycle with tenant-safe credential storage
- Dispatch reviewed queued emails through Gmail API
- Persist delivery outcomes and API error traces

### Zoho Mail Send
- Zoho Mail OAuth/SMTP credential lifecycle for reviewed queue dispatch
- Dispatch reviewed queued emails through Zoho path
- Persist delivery outcomes and provider-specific failures

# Next Roadmap

This roadmap extends the current post-W12 architecture and AI Automation Center baseline.

## Phase A — Automation Registry

### Objective
Introduce a policy-driven automation control plane for agent assignment and execution governance.

### Scope
- Registry model for automation definitions.
- Task-type to agent routing rules.
- Default/fallback assignment policies.
- Tenant-level override hooks.
- Observability metadata for assignment decisions.

### Deliverables
- Registry schema and service layer.
- Admin/internal management UI for registry entries.
- Safe defaults with explicit fallback behavior.

## Phase B — Accounting Connector Foundation

### Objective
Create connector framework primitives before provider-specific integrations.

### Scope
- Connector abstractions and provider adapters.
- Sync run tracking and retry semantics.
- Normalized accounting schema and validation rules.
- Tenant-safe credential/token handling model.

### Deliverables
- Base connector interfaces.
- Core sync orchestration components.
- Initial ingestion telemetry and failure reporting.

## Phase C — Manual Upload Connector

### Objective
Enable value delivery quickly via guided file ingestion.

### Scope
- Upload UX for ledgers/vouchers/register extracts.
- Validation and normalization pipeline.
- Error feedback and correction loop.

### Deliverables
- Manual upload wizard and parser.
- Mapping templates and import summaries.
- Data quality checks with user-visible issue reporting.

## Phase D — Zoho Books OAuth Connector

### Objective
Integrate cloud accounting source with direct OAuth/API sync.

### Scope
- OAuth consent and token lifecycle.
- Incremental sync with watermarking.
- API paging and backfill strategy.

### Deliverables
- Zoho connector setup flow.
- Scheduled and on-demand sync runs.
- Normalized write pipeline into accounting tables.

## Phase E — Tally Local Bridge

### Objective
Support Tally environments via secure local bridge architecture.

### Scope
- Local bridge installer/runtime protocol.
- Signed bridge-to-cloud communication.
- Resilient offline queue and retry behavior.

### Deliverables
- Bridge service specification.
- Secure registration and heartbeat model.
- Tally data extraction and normalized ingestion path.

## Phase F — Accounting-Data-Powered AI Automations

### Objective
Use normalized accounting data to improve AI drafting, risk checks, and reviewer productivity.

### Scope
- Enriched AI payload generation from accounting tables.
- Agent-specific logic for GST/TDS/Audit/ITR workflows.
- Confidence/risk tuning with reviewer feedback loops.

### Deliverables
- Accounting-context-aware automation templates.
- Cross-module insights (dashboard + automation center).
- Measurable quality improvements in draft accuracy and turnaround.

## Cross-Phase Controls

| Control Area | Requirement |
|---|---|
| Security | Tenant isolation, least privilege, secret management |
| Reliability | Idempotent sync/dispatch, retry/backoff, failure triage |
| Auditability | Action logs for registry changes and connector runs |
| Product UX | Keep external orchestration internals hidden from CA users |
| Governance | Human review gate for high-risk outcomes |

## Additional Registry-Led Future Phases

### Phase G — Credential Vault Foundation

#### Objective
Introduce secure credential-management primitives required for future portal automation.

#### Scope
- Encrypted tenant-scoped vault design.
- Secret rotation and access lifecycle policies.
- Audited runtime credential lease model.

#### Deliverables
- Vault service interfaces and policy definitions.
- Redaction-safe logging and monitoring rules.
- Security review checklist for credential operations.

### Phase H — Portal RPA Foundation

#### Objective
Provide resilient and auditable portal-runner foundations for GST/IT/MCA/TRACES/PF-ESI workflows.

#### Scope
- Orchestration contracts for portal tasks.
- Retry/backoff and failure state normalization.
- Operator-visible status checkpoints and recovery paths.

#### Deliverables
- Portal task execution protocol.
- Error classification and remediation playbooks.
- Auditable run ledger and trace model.

### Phase I — Company Incorporation Workflow

#### Objective
Operationalize incorporation journeys (Pvt Ltd/OPC/LLP) with governed checklist orchestration.

#### Scope
- Name approval and SPICe+ readiness flows.
- MOA/AOA drafting checkpoints.
- Post-incorporation compliance starter pack.

#### Deliverables
- Incorporation workflow templates.
- Role-based review/approval gates.
- Progress dashboard for incorporation milestones.

### Phase J — Registration Workflow

#### Objective
Standardize registration and license workflows across GST/Udyam/Startup India/labour/FEMA/license domains.

#### Scope
- Domain-specific checklist templates.
- State/industry conditional requirements.
- Escalation rules for blocked registrations.

#### Deliverables
- Registration playbook library.
- SLA tracking for registration progress.
- Compliance evidence hooks for each registration stage.

### Phase K — Evidence Pack Builder

#### Objective
Automate assembly of reviewer-ready filing evidence packs with strong traceability.

#### Scope
- Acknowledgement/challan evidence capture pipeline.
- Task-wise artifact linking and completeness scoring.
- Exportable evidence pack manifest and review checklist.

#### Deliverables
- Evidence assembly service and schema.
- Evidence quality dashboard.
- Audit-export format for internal/external review.

### Phase L — External Stat Audit Agent Connector

#### Objective
Integrate CA Assist with the user's existing Stat Audit Agent without duplicating statutory audit intelligence inside CA Assist.

#### Scope
- Connector settings for the existing Stat Audit Agent.
- Send audit task/client/evidence context from CA Assist.
- Receive structured statutory audit outputs.
- Store returned results in CA Assist.
- Route all outputs through CA Assist review workflow and audit logs.
- Keep deep statutory audit logic external (no internal reimplementation).

#### Deliverables
- External connector configuration model and transport contract.
- Context payload mapper for audit tasks, evidence, and accounting context.
- Structured output ingestion into `ai_outputs` (and future audit evidence tables if needed).
- Reviewer-first gating flow for audit conclusion/finalization.

## Voice Assistant Future Phases

### Voice Assistant Phase 2 — LLM-Assisted Command Understanding

#### Objective
Expand command recognition quality using LLM assistance while preserving strict safety and confirmation controls.

### Voice Assistant Phase 3 — Task-Aware Assistant

#### Objective
Make assistant actions context-aware to active task state, pending documents, and reviewer workflow.

### Voice Assistant Phase 4 — Local Wake Word (Optional)

#### Objective
Evaluate optional local wake-word support only if needed, with explicit user opt-in and privacy controls.

### Voice Assistant Phase 5 — Safe RPA Handoff with Confirmation

#### Objective
Support controlled handoff to portal/RPA workflows with explicit confirmation, policy checks, and full auditability.

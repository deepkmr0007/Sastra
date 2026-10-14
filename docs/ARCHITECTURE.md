# SASTRA — Architecture Specification

SASTRA (formerly ASTRA) is a personal AI workspace for one owner: conversation, governed memory, cited research, background tasks, a code sandbox and an approval-gated action system, controlled from an offline-first Android app.

## 1. Components

```
 Android app (Kotlin/Compose)                Private server (Python, Starlette)
 ┌────────────────────────────┐   HTTPS      ┌──────────────────────────────────────────────┐
 │ 11 screens · command box   │◄────────────►│ API (auth, validation, rate limit, headers)  │
 │ Local SQLite (offline)     │  bearer token│  ├─ ChatService ── ModelGateway ─┬─ Anthropic │
 │ Keystore-encrypted secrets │              │  │                (route/consent/ ├─ OpenAI-compat│
 │ LocalEngine (calc, units,  │              │  │                 budget/retry)  ├─ self-hosted │
 │   notes, memory recall)    │              │  │                                └─ Offline     │
 │ On-device model client ────┼──► 127.0.0.1 │  ├─ MemoryStore (governed, hybrid search)       │
 │ SyncWorker (outbox, notify)│  (Termux)    │  ├─ ResearchEngine (search→fetch→extract→cite)  │
 │ App lock · FLAG_SECURE     │              │  ├─ TaskService + Orchestrator + Worker (leases)│
 └────────────────────────────┘              │  ├─ ActionGateway + ToolRegistry + Approvals    │
                                             │  ├─ Sandbox (Docker | weak process)             │
                                             │  ├─ Projects (git, checkpoints, artifacts)      │
                                             │  ├─ Scheduler · Learning · Ventures             │
                                             │  ├─ Budget · PrivacyLedger · Metrics            │
                                             │  └─ AuditLog (hash chain) · SecurityEvents      │
                                             │ SQLite WAL (or PostgreSQL) · encrypted backups  │
                                             └──────────────────────────────────────────────┘
```

Web console at `/` (same API) works from any browser before the APK is installed.

## 2. Request flow
1. **Understand / classify** — `classify()` maps a request to `direct | compute | research | code | android | data | document | action`. In chat, questions are answered inline; imperative jobs ("build…", "research…") become tasks immediately and the reply says so honestly.
2. **Plan** — template plans for known kinds; model-written plans only for open-ended actions. Every plan is validated against the role's allowed tools and the step limit before execution.
3. **Execute** — one step at a time; outputs persisted after each step; tool calls go through the Action Gateway; tool outputs inserted into prompts are wrapped as untrusted data.
4. **Approve** — consequential actions park the task in `AWAITING_APPROVAL`; the owner approves exactly that call (or a scoped batch grant).
5. **Verify** — research needs ≥1 usable source and valid citations; code must exit 0 and pass its self-tests with a clean secret scan; failures re-run with the error fed back (bounded retries with exponential backoff + jitter).
6. **Summarise, notify, learn** — result stored; notification queued; repeated failure patterns become *proposals* in the learning loop.

### Task state machine
```
CREATED → PLANNING → (AWAITING_APPROVAL) → RUNNING → VERIFYING → COMPLETED
             │              │                 │           │
             ├→ NEEDS_USER_INPUT ─────────────┤           └→ RUNNING (retry with error context)
             ├→ BLOCKED (switch/role/mode) ───┤
             └→ FAILED / CANCELLED ◄──────────┘   FAILED → PLANNING (manual retry)
```
Per-task limits: seconds, tokens, tool calls, USD, retries, steps.

## 3. Autonomy modes
| Mode | Allowed | Notes |
|---|---|---|
| 0 Advisory | answers, research, drafts; read-only tools | side-effect tools blocked by the gateway |
| 1 Assisted (default) | + reversible local actions (workspace writes, sandbox runs) | consequential actions need approval |
| 2 Delegated | multi-step tasks within budgets/limits | same approvals |
| 3 Scheduled worker | + recurring schedules fire | each run is an ordinary capped task |
Kill switches: pause agents, internet off, workers off, cloud models off, **panic** (all four).

## 4. Data model (tables)
users, sessions, audit_log, security_events, settings_kv, projects, conversations, messages, memories, tasks, task_events, approvals, grants, tool_calls, usage_ledger, data_flows, research_reports, artifacts, schedules, lessons, workflow_versions, ventures, venture_entries, metrics, notifications. See `backend/sastra/migrations/0001_init.sql`.

## 5. Memory
Categories: preference · project · summary · fact · procedure · experimental · working (auto-expiring). Rules: secrets/ID numbers refused; untrusted content that looks like an injection is quarantined; exact and near duplicates merged; contradictions on the same subject flagged (or superseded when the owner says so); provenance + timestamps on every record; project isolation; export; delete; wipe with confirmation phrase; conversation retention (default 90 days). Retrieval = BM25 + optional embedding cosine + recency + confidence.

## 6. Research
Search adapters (SearXNG, Brave, Tavily) → independent-source selection (one page per site, primary sources ranked first, low-quality domains skipped) → SSRF-guarded, robots-respecting fetch → HTML/PDF extraction → claim mining with labels (statistic, estimate, interpretation, hypothesis, methodology, date) → numeric conflict detection → model synthesis with untrusted wrapping **or** extractive report → citation validation (citations to non-existent sources are stripped and reported) → Sources list with title, URL, access time, publish date, content hash and a short supporting passage.

## 7. Tools (22)
calculator · memory_search · memory_propose · web_search · fetch_page · research_report · fs_list · fs_read · fs_write · code_run · git_checkpoint · git_log · git_rollback · secrets_scan · spreadsheet_analyze · document_generate (md/html/docx) · artifact_store · android_project_generate · notify · schedule_create · webhook_send · project_delete. Each declares permissions, data exposure, network, cost, reversibility, risk, timeout and minimum mode (`GET /api/tools`).

## 8. Model gateway
Preference order (owner-set) → filter by availability, cloud switch, consent for the request's data class (`public < normal < private < secret`), circuit breaker → fast model for simple turns, strong model otherwise → projected-cost check against caps → stream with TTFT measurement → retries on transient errors (never on a half-streamed answer) → offline fallback that is honest about being offline. Every outbound call writes the usage ledger and the privacy ledger.

## 9. API surface (all JSON; bearer auth except setup/login/health)
`/api/setup` · `/api/auth/*` (login, logout, sessions, revoke, password, TOTP) · `/api/status` · `/api/chat` (SSE) · `/api/conversations` · `/api/tasks` (+cancel/retry/input) · `/api/approvals` (+decide) · `/api/grants` · `/api/research` · `/api/memory` (+export/wipe/retention/approve/resolve) · `/api/projects` (+file, checkpoint, rollback, diff) · `/api/artifacts` · `/api/tools` (+run) · `/api/controls` (+panic/resume) · `/api/models` (+preference/consent) · `/api/budget` (+pricing/report) · `/api/privacy` · `/api/security/events` · `/api/audit` (+verify) · `/api/schedules` · `/api/learning` · `/api/ventures` · `/api/metrics` · `/api/notifications` · `/api/backup` · `/api/sync`.

## 10. Observability
Metrics: model TTFT and latency (p50/p95), chat time-to-first-useful-answer, sandbox duration, task success rate, cost per task. Audit log is hash-chained (`/api/audit/verify`, `python -m sastra verify-audit`).

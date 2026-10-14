# Architecture Decision Records — SASTRA 1.0

Environment assessed at build time (14 Oct 2026): Linux build container, Python 3.13, JDK 21, Gradle 8.14.3, git, `unshare` (user + network namespaces). **Not available:** Docker daemon, Android SDK, Google Maven / Maven Central / PyPI (blocked by the build environment's egress policy). Consequences are recorded below and in `LIMITATIONS.md`.

## ADR-001 — Hybrid: Android control center + private server + optional on-device model
**Decision.** Three cooperating parts: (1) Kotlin/Compose app that is offline-first; (2) a Python server that does the heavy, privileged work (research, tasks, sandbox, memory, approvals); (3) an optional on-device model reached through an OpenAI-compatible endpoint on `127.0.0.1` (llama.cpp `llama-server` or Ollama in Termux).
**Why.** Phones cannot safely run untrusted code or long jobs; a server can. Keeping the phone useful offline needs local storage + deterministic tools + optionally a local model.
**Alternative rejected for v1.** Embedding llama.cpp in the APK via the NDK: large APK, per-ABI builds, and very uneven device performance. The Models screen benchmarks the local endpoint on the real phone (time-to-first-token, tokens/s, battery %, battery temperature) so the owner decides with evidence. Embedding is on the roadmap.

## ADR-002 — Starlette + Pydantic instead of FastAPI
**Decision.** API built directly on Starlette (routing, ASGI) and Pydantic v2 (validation) — the two libraries FastAPI itself is built on.
**Why.** Central, explicit auth wrapper on every route; fewer dependencies. Also practical: only packages already present could be installed and tested in the build environment, and I chose to ship code that was actually run rather than untested FastAPI code.
**Cost.** No auto-generated OpenAPI page. Migrating to FastAPI is mechanical (same models, same handlers).

## ADR-003 — SQLite (WAL) by default; PostgreSQL through the same portable SQL
**Decision.** All SQL is portable (TEXT UUID keys, epoch DOUBLE PRECISION times, `ON CONFLICT`). SQLite is the tested default; setting `SASTRA_DATABASE_URL=postgresql://…` and installing `psycopg` switches dialect.
**Why.** Single owner, zero-ops, cheap, works on a ₹0 home machine. Embeddings are stored as JSON and scored in Python — fine to tens of thousands of memories.
**Gap.** The PostgreSQL path was **not tested** here (no server available). pgvector is roadmap.

## ADR-004 — The `tasks` table is the queue (leases), not Redis/Celery
**Decision.** Workers claim a task with an atomic compare-and-set lease (120 s, heart-beaten). Dead worker → lease expires → another worker resumes from the persisted `step_index`.
**Why.** Durable, crash-recoverable, idempotent (keys on tasks and tool calls), and no extra service. The prompt says Redis "only when justified"; it is not, at single-owner scale. Tested: concurrent claims never duplicate; crash recovery resumes.

## ADR-005 — Authorization lives outside the model
**Decision.** Every tool call passes the **Action Gateway**: schema validation → kill switches → least-privilege role → autonomy mode → canary check → idempotency → approval/grant → budget → timeout → audit. Model-written plans are validated against the role's tool list *before* anything runs.
**Why.** Prompt injection can make a model say anything; it cannot make the gateway skip a check.

## ADR-006 — Sandbox: hardened Docker; weak process runner for development only
**Decision.** `DockerRunner`: `--network none`, read-only root, `--cap-drop ALL`, `no-new-privileges`, uid 65534, pids/memory/CPU/file-size limits, only the project mounted; optional gVisor (`SASTRA_SANDBOX_RUNTIME=runsc`); package installs only through an allowlisting egress proxy and only with approval. `ProcessRunner` (rlimits + empty env + `unshare -rn` network namespace) is disabled unless `SASTRA_ALLOW_WEAK_SANDBOX=1`.
**Why.** Docker is good but not a complete boundary (shared kernel) — documented in the threat model with the upgrade path.

## ADR-007 — Authentication
Password (scrypt) + optional TOTP (secret stored AES-GCM encrypted) + revocable server-side sessions (JWT only carries a session id) + lockout + per-IP login rate limit + one-time setup code printed to the server console. Passkeys/WebAuthn: roadmap.

## ADR-008 — Android: platform APIs over libraries
`HttpURLConnection` + `org.json` + `SQLiteOpenHelper` + Keystore AES-GCM, no Retrofit/Room/KSP. Fewer moving parts and fewer version conflicts for a first build; cost is some boilerplate.

## ADR-009 — Models and prices
Defaults: Claude Haiku 5.5 for simple turns, Claude Sonnet 5.5 for hard work; any OpenAI-compatible provider or self-hosted model can replace them. Prices are **configuration**, and an unknown price for a paid model is refused (fail closed). The shipped default prices came from third-party pricing pages and must be checked against the official page.

## Highest-risk engineering decisions (watch these)
1. **Docker socket access** for the sandbox (even through a filtering proxy) is powerful — a compromised server could start a privileged container. Mitigate with rootless Docker or a separate sandbox VM (roadmap).
2. **Android app is CI-built but not yet tested on a device**; instrumented tests are the next P0 item.
3. **Heuristic prompt-injection detection** is bypassable; safety rests on the gateway, roles and approvals, not on detection.
4. **SQLite single connection + lock** limits concurrency; fine for one owner, not for teams.
5. **SSRF guard** resolves DNS before urllib connects (TOCTOU / DNS rebinding window).

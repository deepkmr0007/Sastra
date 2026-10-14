# Test Report — SASTRA 1.0 (14 Oct 2026)

Every number below is from an actual run. Nothing was assumed.

## GitHub Actions (github.com/deepkmr0007/Sastra) — all 3 jobs green
| Job | Result |
|---|---|
| backend | ruff lint clean · bandit (medium+) clean · pip-audit clean · **104 tests, 0 failures** on Python 3.12 (1 skipped: network-namespace test, runner has no unprivileged user namespaces — it passes in the build environment) — includes **3 real Docker sandbox tests** (runs as uid 65534, no network, read-only root, timeout kills the container) |
| android | **compiles**, JVM unit tests pass, lint runs, **debug APK built (≈11.5 MB artifact `sastra-debug-apk`)** |
| docker | server image builds; container starts, `/health` OK, setup code printed |

## Backend in the build environment — 101 tests passing (≈22 s; +3 Docker tests that run in CI)
`cd backend && python -m unittest discover -s tests -t .`

| Suite | Tests | Covers |
|---|---|---|
| test_security | 13 | scrypt, JWT expiry/signature, TOTP incl. RFC 6238 vector, AES-GCM, secret & Indian-ID redaction (Luhn, Aadhaar, PAN, OTP/UPI PIN), injection heuristics, nonce wrapping, canary, SSRF blocklist (loopback, metadata, private, IPv6, file://, creds, odd ports), audit-chain tamper & deletion detection, rate limiter |
| test_models | 11 | real HTTP streaming against fake OpenAI-compatible and Anthropic servers, cost + usage + privacy ledger, private data never sent to cloud, local model allowed for private, unknown price fails closed, budget cap & zero budget, cloud kill switch, retry on 503, circuit breaker, honest offline adapter |
| test_memory | 10 | hybrid search, exact/near duplicate merge, sensitive refusal, injection quarantine, experimental pending validation, contradiction flag/resolve/supersede, project isolation, TTL expiry, edit/export/wipe with confirmation, embeddings path |
| test_research | 6 | primary-source ranking, one-page-per-site, 403 handling, low-quality skip, timestamps & hashes, numeric conflict (105 GW vs 98 GW), injection-flagged page triggers no tools, fabricated citation removal, untrusted wrapping in synthesis prompt, internet-off offline alternative, user-supplied URLs, search cost & cap |
| test_tasks | 12 | state machine, idempotency, bounded retries with backoff → FAILED → manual retry, cancel (unleased + at step boundary), crash recovery via lease expiry, no duplicate claims across 6 threads, limit enforcement, offline direct task, NEEDS_USER_INPUT, pause/resume, tool output wrapped as untrusted |
| test_tools | 11 | schema validation, least-privilege roles (researcher cannot send), advisory mode, disabled tool, internet switch, pause vs operator, approval single-use + exact-args, rejection never executes, scoped grants (count/scope/limits), canary block, idempotent tool calls, irreversible ⇒ approval |
| test_sandbox_workspace | 14 | process sandbox: runs, timeout kill, env secrets not inherited, **network blocked (netns)**, 2 GB allocation refused (memory cap), network mode refused, code_run tool, no-sandbox refusal; workspace: traversal/absolute/symlink escape blocked, diff + checkpoint + reversible rollback, secret scan, versioned/encrypted/tamper-detected artifacts, Android project generator, CSV profiling (duplicates, missing, sums), DOCX generation |
| test_api_e2e | 24 | setup code, auth required, revocation, token-in-URL refused, rate limit + lockout, TOTP login, security headers, 413/422, SSE chat with memory context, private chat stays local, `/remember` + task routing, idempotent offline sync, research task end-to-end, **code task that fails its own self-test, gets the error fed back, and passes on retry**, action → approval → runs exactly once (privacy ledger records it), rejection fails task, injected plan rejected by role validation, input & cancel endpoints, panic + audit verify, scheduler only in mode 3, learning governance (protected params need owner, clamp, rollback), ventures verified vs forecast, encrypted backup/restore (+ wrong key refused), backup refuses without key, metrics/budget endpoints, retention |

## Live server smoke test (real uvicorn, real HTTP)
`/health` 200 → setup with console code → login → `POST /api/chat` streamed SSE (`25*4+7 = 107`, computed locally, provider `offline`) → `POST /api/tasks "(1234+766)/4"` → background worker completed it (`= 500`) within 3 s → `/api/status` reported `offline`, mode ASSISTED, sandbox `process/weak`, encryption at rest on.

## Android
- Pure-Kotlin engine compiled with Kotlin 2.0.21 (bundled in Gradle) and **10/10 JUnit tests passed** (CalculatorTest 5, LocalEngineTest 5).
- Full app (UI, data, worker layers) compiled and packaged by CI on the first run.

## Not run here
PostgreSQL · live Anthropic/OpenAI/Brave/Tavily calls · Android instrumented tests · on-device behaviour of the APK (needs a phone or emulator). Release gate for "production-ready": all CI jobs green + the P0 items in ROADMAP.md.

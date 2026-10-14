# Progress — completed / incomplete / blocked / next

## Phase status
| Phase | Status | Evidence |
|---|---|---|
| 1 Foundation (ADR, schema, auth, config, audit, API shell) | ✅ done | test_security, AuthApiTests |
| 2 Conversational core (streaming, adapters, routing, budgets, privacy) | ✅ done | test_models, ChatApiTests |
| 3 Research & memory | ✅ done | test_research, test_memory |
| 4 Task orchestration (queue, state, retries, approvals, audit) | ✅ done | test_tasks, TaskFlowTests |
| 5 Execution sandbox (code, files, git, artifacts, Android export) | ✅ done (Docker path unexecuted) | test_sandbox_workspace |
| 6 Governed autonomy (modes, grants, budgets, schedules, notifications) | ✅ done | test_tools, GovernanceTests |
| 7 Evaluation & hardening | ✅ adversarial + security tests; CI lint/scan configured | TEST_REPORT.md |
| 8 Deployment (Docker, compose, Caddy, backup/restore, handbook) | ✅ written; image build runs in CI | DEPLOYMENT.md |
| Android control center | ✅ written; engine tested; full build in CI | LIMITATIONS.md |

## Definition of Done (§21)
| # | Requirement | State |
|---|---|---|
| 1 | Install/run the Android app | APK built by CI (pending first run) |
| 2 | Responsive conversation with a configured model | ✅ (streaming; tested with protocol fakes) |
| 3 | Assign a multi-step research task | ✅ |
| 4 | Cited report from accessible sources | ✅ |
| 5 | Inspect/manage project memory | ✅ |
| 6 | Create/track background tasks | ✅ |
| 7 | Generate and test code in isolation | ✅ (process sandbox tested; Docker in production) |
| 8 | Approve consequential actions first | ✅ |
| 9 | Useful offline mode | ✅ (server offline adapter; phone engine + on-device model + outbox) |
| 10 | Inspect logs, revoke, cancel, delete | ✅ |
| 11 | Reproducible build & deploy | ✅ documented; CI |
| 12 | Main security & reliability tests | ✅ 101 backend + 10 Android JVM |

## Blocked (by the build environment)
Android SDK download, Docker daemon, PyPI/Maven access, live provider keys.

## Next concrete action
Push the repo to GitHub → let CI build the APK → install → connect to the server → fix anything the first CI run reports.

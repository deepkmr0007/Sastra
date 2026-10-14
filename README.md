# SASTRA — personal AI workspace

**S**ecure · **A**utonomous (governed) · **S**elf-improving (owner-approved) · **T**ask & **R**esearch **A**ssistant — formerly ASTRA.

One owner. One private server. One Android app that keeps working offline. SASTRA chats, remembers what you approve, researches the web with citations, runs background tasks, writes and tests code in a sandbox, and never sends, publishes, spends or deletes without your approval.

```
sastra/
├── backend/             Python server + worker (Starlette, Pydantic, SQLite/PostgreSQL)
│   ├── sastra/          API, model gateway, memory, research, tasks, tools, sandbox, workspaces
│   └── tests/           101 tests (security, adversarial, end-to-end)
├── android/             Kotlin + Jetpack Compose + Material 3 control center (offline-first)
├── deploy/              Dockerfile, docker-compose (Caddy HTTPS, sandbox socket proxy, egress allowlist), .env.example
├── scripts/             dev server, nightly encrypted backup
├── docs/                architecture, ADRs, threat model, privacy, setup, deployment, costs, tests, limitations, roadmap
├── .github/workflows/   CI: tests, lint, security scan, APK build, container smoke test
└── SASTRA_AI_Master_Build_Prompt_v2.txt
```

## Quick start (development)
```bash
cd backend
pip install -r requirements.txt
../scripts/dev.sh                    # prints a one-time setup code
# open http://127.0.0.1:8787 → create owner with the code → chat
python -m unittest discover -s tests -t .   # run the test suite
```
Connect a model by setting `SASTRA_ANTHROPIC_API_KEY` (or any OpenAI-compatible endpoint, including your own Ollama). With no model, SASTRA still works in an honest offline mode.

## Phone
Push to GitHub → CI builds `sastra-debug-apk` → install → Settings → server URL → create owner. Full guide: [docs/SETUP_ANDROID.md](docs/SETUP_ANDROID.md).

## Read next
[ARCHITECTURE](docs/ARCHITECTURE.md) · [ADR](docs/ADR.md) · [THREAT_MODEL](docs/THREAT_MODEL.md) · [PRIVACY](docs/PRIVACY.md) · [DEPLOYMENT](docs/DEPLOYMENT.md) · [COSTS](docs/COSTS.md) · [TEST_REPORT](docs/TEST_REPORT.md) · [LIMITATIONS](docs/LIMITATIONS.md) · [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) · [ROADMAP](docs/ROADMAP.md) · [PROGRESS](docs/PROGRESS.md)

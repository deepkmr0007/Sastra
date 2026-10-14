# Deployment & Operations Handbook

## Prerequisites
Python 3.12+ (server), git, Docker (for the code sandbox and the compose stack), a domain or Tailscale for HTTPS.

## Environment variables
See `deploy/.env.example`. Required in production: `SASTRA_JWT_SECRET`, `SASTRA_DATA_KEY`. Everything else is optional and fails closed (missing key → feature off).

| Variable | Purpose |
|---|---|
| `SASTRA_DATA_DIR` | data directory (db, workspaces, artifacts, backups) |
| `SASTRA_DATABASE_URL` | `sqlite:///…` (default) or `postgresql://…` (+ `pip install psycopg[binary]`, untested) |
| `SASTRA_ANTHROPIC_API_KEY`, `_MODEL`, `_FAST_MODEL` | Claude models |
| `SASTRA_OPENAI_COMPAT_BASE_URL`, `_API_KEY`, `_MODEL`, `_IS_LOCAL`, `SASTRA_EMBED_MODEL` | any OpenAI-compatible or self-hosted model |
| `SASTRA_SEARXNG_URL`, `SASTRA_BRAVE_API_KEY`, `SASTRA_TAVILY_API_KEY` | search |
| `SASTRA_DAILY_BUDGET_USD`, `SASTRA_MONTHLY_BUDGET_USD` | hard caps |
| `SASTRA_SANDBOX_BACKEND` (`auto|docker|process|disabled`), `SASTRA_SANDBOX_RUNTIME` (`runsc`), `SASTRA_SANDBOX_EGRESS_PROXY`, `SASTRA_ALLOW_WEAK_SANDBOX` | code execution |
| `SASTRA_CONVERSATION_RETENTION_DAYS` | chat retention |
| `SASTRA_WORKER_IN_PROCESS` | `false` to run `python -m sastra worker` separately |

## Commands
```bash
python -m sastra serve            # API + in-process worker
python -m sastra worker           # standalone worker (scale out)
python -m sastra migrate          # apply migrations (also automatic on start)
python -m sastra setup-code       # show the owner setup code again
python -m sastra gen-key          # new data key
python -m sastra backup           # encrypted backup to $DATA_DIR/backups
python -m sastra restore FILE     # restore (STOP the server first)
python -m sastra verify-audit     # check audit hash chain
python -m sastra status
```

## Database migrations
Migrations live in `backend/sastra/migrations/NNNN_name.sql`, apply in order, are recorded in `schema_migrations` and run inside a transaction. Add a new numbered file for every schema change; never edit an applied file.

## Backup & restore
- Nightly: `scripts/backup.sh` (cron) → keeps 14 encrypted backups and verifies the audit chain. Copy backups off the machine (rclone/scp).
- Keep `SASTRA_DATA_KEY` in a password manager, **not** on the same disk as the backups.
- Restore: stop the server → `SASTRA_DATA_KEY=… python -m sastra restore sastra-YYYYMMDD-HHMMSS.bak` → previous data is kept with suffix `.pre-restore-<timestamp>` → start the server.
- Test a restore once a month on another machine.

## Upgrades
`git pull && docker compose up -d --build` (or reinstall requirements and restart). Migrations run automatically. Back up first.

## Monitoring
`/health` (Docker HEALTHCHECK), `/api/metrics` (TTFT, latency p50/p95, task success rate, cost per task), `/api/security/events`, `/api/budget/report`. Logs go to stdout; tokens are never logged and the Caddy config deletes the Authorization header from access logs.

## Hardening checklist
- [ ] HTTPS only (Caddy or Tailscale) — the app refuses plain HTTP except localhost.
- [ ] `SASTRA_DATA_KEY` and `SASTRA_JWT_SECRET` set; key stored off-server.
- [ ] TOTP enabled; app lock on the phone.
- [ ] Spending caps set deliberately.
- [ ] Docker sandbox with `runsc` (gVisor) if you run code you did not write; or a separate sandbox VM.
- [ ] Firewall: only 80/443 (or nothing public with Tailscale).
- [ ] Unattended upgrades on the host; `pip-audit` in CI.
- [ ] Off-site backup copy and a tested restore.

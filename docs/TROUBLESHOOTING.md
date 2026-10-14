# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| App says "Server must use HTTPS" | URL starts with `http://` | Use Tailscale HTTPS or Caddy. Only `127.0.0.1`/`localhost`/`10.0.2.2` may be plain HTTP. |
| "Not reachable" in Settings | Server down, wrong URL, phone not on Tailscale | Open `https://your-server/health` in the phone browser; it must show `{"ok":true}`. |
| "Invalid setup code" | Code mistyped or owner already created | `python -m sastra setup-code` (or `docker compose logs sastra | grep "setup code"`). |
| "Account temporarily locked" | 5 failed logins | Wait 15 minutes. |
| "Too many login attempts" | Per-IP rate limit | Wait a minute. |
| Every answer starts "I'm in offline mode" | No model configured, cloud switched off, price not set, caps reached, or the chat is private and no local model exists | Status pill + Models screen show which. Check `/api/budget`; set prices with `PUT /api/budget/pricing`; check Security → Cloud models. |
| "No price configured for …" | Fail-closed pricing | Models → set price, or `PUT /api/budget/pricing {"provider_model":"openai_compat:<model>","input_per_m":…,"output_per_m":…}` |
| Code task: "No sandbox available" | Docker not installed / not reachable | Install Docker; in compose, keep the `docker-proxy` service. Dev only: `SASTRA_ALLOW_WEAK_SANDBOX=1`. |
| Code task: "needs a project" | Tasks run code only inside a project workspace | Create a project, then create the task from inside it (or include `project_id`). |
| Task stuck in AWAITING_APPROVAL | Waiting for you | Approvals screen. |
| Task BLOCKED "needs autonomy mode ≥ …" | Mode 0 (Advisory) | Security → Autonomy → 1 or higher. |
| Research: "No search provider configured" | No search key | Set `SASTRA_SEARXNG_URL` (free) or Brave/Tavily key; or give URLs directly. |
| Research: "too little readable text" | Script-only page, login/paywall | Supply a different source or the PDF. |
| Memory: "Refused: looks like sensitive data" | Card/Aadhaar/PAN/OTP/API key detected | Intentional. Store a hint, never the secret. |
| Benchmark: "Failed … Is the model server running" | Termux `llama-server` not running or wrong port | Keep Termux open (disable battery optimisation for Termux), check `http://127.0.0.1:8080/v1`. |
| No phone notifications | Permission denied or battery saver | Android Settings → Apps → SASTRA → Notifications; exclude from battery optimisation. |
| "Session expired" | Session revoked/expired or JWT secret rotated | Sign in again. |
| Backup: "Set SASTRA_DATA_KEY" | No key | Generate with `python -m sastra gen-key`, store safely, restart. |
| Audit verify says BROKEN | Database rows edited outside SASTRA | Treat as an incident (THREAT_MODEL.md → Incident response). |
| GitHub Actions Android job fails | First compile of UI code | Open the job log; the Kotlin error names file and line. Share it with Claude for a fix. |

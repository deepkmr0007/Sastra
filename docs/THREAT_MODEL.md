# Threat Model & Incident Response

## Assets
Owner's conversations, memory, project files and artifacts · model/search API keys · the data key (`SASTRA_DATA_KEY`) · the owner session · the server host · the owner's money (paid APIs) · the owner's reputation (messages sent in their name).

## Adversaries
Internet attackers reaching the server · malicious web pages/documents/emails (indirect prompt injection) · malicious or compromised model output · malicious dependencies in generated code · a thief with the unlocked or locked phone · a compromised third-party provider.

## Trust boundaries
Phone ↔ server (HTTPS + bearer session) · server ↔ model/search providers (consent + privacy ledger) · server ↔ the web (SSRF guard) · server ↔ sandbox (container boundary) · model ↔ tools (Action Gateway) · owner ↔ agent (approvals).

## Threats and controls
| # | Threat | Controls (implemented) | Tests |
|---|---|---|---|
| T1 | Stranger claims a fresh server | One-time setup code printed only to the server console; wrong code raises a security event | `test_setup_requires_one_time_code` |
| T2 | Password guessing | scrypt hashing, per-IP login rate limit, account lockout after 5 failures, optional TOTP | `test_lockout_after_failures`, `test_totp_second_factor` |
| T3 | Stolen token | Server-side sessions (revocable per device), expiry, tokens never accepted in URLs, Android token Keystore-encrypted, app lock, FLAG_SECURE | `test_routes_require_auth_and_sessions_are_revocable`, `test_token_in_url_not_accepted` |
| T4 | Direct prompt injection by a page/document | Untrusted content wrapped in nonce-tagged data blocks + explicit policy; heuristic scoring and flags; research role cannot hold action tools; plans validated before execution | `test_injected_research_page_cannot_trigger_actions`, `test_tool_outputs_are_wrapped_as_untrusted_in_prompts` |
| T5 | Agent takes a consequential action | Action Gateway: high/critical or irreversible tools always need approval; approval covers one call with exact arguments; scoped grants have use count + expiry; advisory mode blocks side effects; panic switch | `test_approval_is_single_use_and_exact`, `test_scoped_grant_limits_uses_and_scope`, `test_action_needs_approval_then_runs_once` |
| T6 | Data exfiltration / system-prompt leak | Canary token in system prompts; any tool argument or output containing it is blocked and alerted; webhook needs approval; privacy ledger records every outbound byte | `test_canary_full_token_blocked` |
| T7 | Untrusted code escapes | Docker: no network, read-only, cap-drop ALL, no-new-privileges, uid 65534, limits; optional gVisor; secrets never in the sandbox env; weak runner off by default | `SandboxTests` (timeout, memory, env, network) |
| T8 | SSRF via fetch | Scheme/port/credential checks, DNS resolution to public IPs only, metadata hosts blocked, every redirect re-validated, size/time caps, robots.txt | `test_blocks_ssrf_targets` |
| T9 | Memory poisoning | Agent/untrusted memories quarantined if injection-like; experimental lessons excluded from retrieval until approved; contradictions flagged | `test_untrusted_injection_quarantined_and_not_retrieved` |
| T10 | Runaway spend | Hard daily/monthly caps, projected-cost pre-check, unknown price refused, per-task cost limit, per-schedule budget | `test_budget_cap_blocks_paid_calls`, `test_unknown_price_fails_closed` |
| T11 | Log tampering / hiding actions | Hash-chained audit log, verify endpoint/CLI, redaction before writing | `test_chain_detects_tampering` |
| T12 | Secrets in generated code/logs | Redaction of keys/tokens/passwords; secret scan gate in code tasks; `.gitignore` for keys | `test_secret_scan`, `test_secrets_redacted` |
| T13 | Sensitive personal data stored | Memory refuses card numbers (Luhn), Aadhaar, PAN, OTP/UPI PIN phrases, API keys | `test_sensitive_data_refused` |
| T14 | Backup theft | Backups AES-256-GCM encrypted; refuses to run without a key; restore rejects path traversal and links | `test_encrypted_backup_and_restore` |
| T15 | Phone theft | App lock (biometric/PIN), FLAG_SECURE, no cloud backup of app data, remote session revoke, local wipe | manual |

## Residual risks (honest list)
1. **Prompt injection is not "solved".** A model can still be talked into writing a misleading answer. Controls guarantee it cannot *act* without the gateway; they do not guarantee every sentence is true. Read cited sources for consequential decisions.
2. **Docker socket proxy.** Allowing container creation is close to host root if the SASTRA server itself is compromised. Stronger: rootless Docker, gVisor, or a separate sandbox VM/host.
3. **Docker is a shared-kernel boundary.** Kernel exploits escape containers. Use gVisor (`runsc`) or Firecracker for hostile code.
4. **DNS rebinding window** between our DNS check and urllib's connect.
5. **Weak process sandbox** shares the filesystem view; development only.
6. **Android local database is not encrypted beyond Android's file-based encryption**; app-private and excluded from backups. SQLCipher is on the roadmap.
7. **Rate limiter is in-process** (resets on restart).
8. **Web console** uses inline scripts (CSP `unsafe-inline` for scripts on `/` only).
9. **Third-party providers** process what you allow them to; consent classes limit this, they do not change the provider's own retention.

## Incident response
1. **Contain:** Security → *Stop everything* (or `POST /api/controls/panic`). Revoke other sessions (`POST /api/auth/revoke-others`).
2. **Rotate:** change password; rotate provider API keys at the provider; rotate `SASTRA_JWT_SECRET` (signs everyone out). Rotating `SASTRA_DATA_KEY` requires decrypt-and-re-encrypt (restore a backup with the old key, then back up with the new key).
3. **Investigate:** `python -m sastra verify-audit`; review `/api/audit`, `/api/security/events`, `/api/privacy` (outbound flows), `tool_calls` table.
4. **Recover:** restore the last known-good encrypted backup (`python -m sastra restore FILE` with the server stopped).
5. **Learn:** add a regression test for the path that failed.

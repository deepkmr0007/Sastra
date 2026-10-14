# Privacy Guide

SASTRA is private by default: your data lives on your server and your phone, and it leaves only to providers you allow, for the purpose shown, recorded in the privacy ledger.

## What is stored where
| Data | Where | Encrypted | Retention |
|---|---|---|---|
| Conversations & messages | Server DB; phone DB | Server: disk/host encryption; backups AES-GCM | Default 90 days (Security → "Keep chats"), 0 = forever |
| Memory | Server DB; synced cache on phone | as above | Until you delete; `working` items expire (24 h default) |
| Tasks, research reports, audit log | Server DB | as above | Until deleted / project deleted |
| Project files & artifacts | Server `workspaces/`, `artifacts/` | Artifacts optionally AES-GCM | Until deleted |
| Session token, local-model key | Phone | Android Keystore AES-GCM | Until sign-out/wipe |
| TOTP secret | Server DB | AES-GCM (requires data key) | Until disabled |

Never stored: passwords in clear text, card numbers, Aadhaar/PAN, OTP/UPI PINs, API keys (memory refuses them).

## What can leave the server
| Destination | When | What | Control |
|---|---|---|---|
| Cloud model (Anthropic or other OpenAI-compatible) | Chat/task needs a model and the data class is allowed | Prompt: your message, recent history, relevant memory snippets, tool results | Models → "May receive"; Security → Cloud models switch; private chats never go to cloud |
| Self-hosted model | Same | Same | Stays on your hardware |
| Search API | Research | Query text only | Remove the key to disable; Internet switch |
| Websites | Research / fetch_page | URL request only | Internet switch; robots.txt respected |
| Webhook | Only after you approve the exact message | The approved text | Approvals |

Data classes: `public` (web content) < `normal` (default chats) < `private` (private chats — only local/self-hosted models) < `secret` (never leaves the server).

The **Privacy dashboard** (Security screen / `GET /api/privacy`) lists every outbound flow: destination, provider, data class, bytes, purpose and the provider's retention note.

## Your controls
- Export memory as JSON (Memory → Export, `GET /api/memory/export`).
- Delete one memory, a project's memory, or everything (`POST /api/memory/wipe` with the phrase `DELETE ALL MEMORY`).
- Delete conversations; delete projects (approval-gated, removes files, artifacts, memory, reports).
- Change retention; switch off cloud models; switch off internet.
- Phone: Settings → "Delete all SASTRA data on this phone".

## Training
SASTRA never trains or fine-tunes any model on your data. "Learning" means versioned workflow parameters and saved procedures that you can inspect, approve, reject and roll back. Whether a cloud provider uses API data is governed by that provider's terms — check them before allowing `normal` data.

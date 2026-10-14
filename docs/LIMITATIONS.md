# Known Limitations (v1.0)

Stated plainly so nothing is mistaken for a capability it does not have.

## Build/verification gaps
- **The Android app was not compiled in the build environment** (no Android SDK; Google's Maven repository was blocked). The pure-Kotlin engine (calculator, offline engine) **was** compiled and its 10 JVM unit tests passed using the Kotlin compiler bundled with Gradle. The UI/data layers will first compile in GitHub Actions; expect possibly a few small fixes on the first run.
- **PostgreSQL** support is written but untested; SQLite is the tested path.
- **Docker sandbox** command construction is implemented but was not executed (no Docker daemon in the build environment). The weak process sandbox was executed and tested (timeout, memory cap, environment isolation, network namespace).
- **Real model providers** were tested against faithful local fakes of the OpenAI-compatible and Anthropic streaming protocols, not against live APIs (no keys in the build environment).
- **Live web research** was tested with a controlled set of pages; real-site extraction quality varies (script-heavy pages yield little text; scanned PDFs need OCR, which is not built in).

## Product limitations
- SASTRA is not a superintelligence. It is as capable as the models you connect, bounded by tools, permissions and budgets. With no model configured it does arithmetic, unit conversion, memory search and note capture — and says so.
- No native function-calling: plans are JSON validated by the server. Works with any chat model, but weaker models produce weaker plans (template plans cover the common task kinds).
- One owner per server (no multi-user/teams).
- Passkeys/WebAuthn not implemented (password + TOTP instead).
- On-device model runs via a separate local server app (Termux); it is not embedded in the APK.
- Android local database is not separately encrypted (relies on Android file-based encryption + app sandbox).
- Voice uses the phone's own speech recognizer and TTS (may use Google services depending on the phone).
- Email/calendar/storage integrations are not built; the generic `webhook_send` tool (approval-gated) covers Slack/Discord/ntfy-style notifications.
- Browser automation is not built (fetch + extraction only).
- Research cannot pass logins, paywalls or CAPTCHAs (by design).
- Learning loop tunes a defined set of workflow parameters; it does not rewrite code or prompts autonomously.
- The in-process rate limiter resets on restart; the SQLite connection is serialized (fine for one owner).
- The DOCX generator handles headings, bullets and paragraphs only.
- APK build/export runs in GitHub Actions or Android Studio, not inside the sandbox.

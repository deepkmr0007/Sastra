# Setup Guide (Android-first owner)

You need: an Android phone (8.0+), a GitHub account (to build the APK for free), and **one** place to run the server.

## Step 1 — Choose where the server runs
| Option | Cost | Good for |
|---|---|---|
| **A. Old laptop/PC at home + Tailscale** | ₹0 | Best start. Private network between your phone and PC, no domain needed. |
| **B. Small VPS + domain** | ~€6–7/month (see COSTS.md) | Always on, reachable anywhere. |
| C. Termux on the phone itself | ₹0 | Experimental; slow installs; no Docker sandbox. |

### Option A (home PC, Linux/WSL/macOS)
```bash
git clone <your repo> sastra && cd sastra
python3 -m pip install -r backend/requirements.txt
cd backend
export SASTRA_DATA_KEY=$(python3 -m sastra gen-key)   # SAVE THIS somewhere safe (password manager)
export SASTRA_JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export SASTRA_ANTHROPIC_API_KEY=...        # optional; without a model SASTRA runs in honest offline mode
python3 -m sastra serve --host 0.0.0.0 --port 8787
```
Install Tailscale on the PC and phone (same account). In Tailscale, enable **HTTPS certificates**, then run `tailscale serve --bg 8787` on the PC. Your server URL is `https://<pc-name>.<tailnet>.ts.net`.
For code execution, install Docker on the PC (Docker Desktop on Windows/macOS). Without Docker, code tools refuse to run (safe default).

### Option B (VPS)
```bash
git clone <your repo> sastra && cd sastra/deploy
cp .env.example .env && nano .env          # set SASTRA_DOMAIN, JWT_SECRET, DATA_KEY, model key, caps
docker compose up -d --build
docker compose logs sastra | grep "setup code"
```
Point your domain's DNS A record at the VPS first; Caddy gets the HTTPS certificate automatically.

## Step 2 — Build and install the APK
1. Push this repository to GitHub (private is fine).
2. Open **Actions → CI** — it runs automatically on push. When it is green, open the run and download **sastra-debug-apk**.
3. On the phone: open the downloaded zip → `app-debug.apk` → allow "Install unknown apps" for your browser/files app → Install.
(Alternative: open `android/` in Android Studio and press Run.)

Release build: create a keystore once (`keytool -genkeypair -v -keystore sastra.jks -alias sastra -keyalg RSA -keysize 4096 -validity 10000`), keep it safe, and set `SASTRA_KEYSTORE_PATH`, `SASTRA_KEYSTORE_PASSWORD`, `SASTRA_KEY_ALIAS`, `SASTRA_KEY_PASSWORD` (as GitHub secrets in CI) before `./gradlew assembleRelease`.

## Step 3 — First run on the phone
1. Open SASTRA → **Settings** → server URL → Save. "Reachable" should turn green.
2. "First run: create the owner": choose a username and a password (10+ characters), type the **setup code** from the server log → Create owner.
3. Turn on **App lock**. Keep "Hide from screenshots" on.
4. Security → check the mode (1 — Assisted is the default) and the switches.
5. Models → set spending caps (default $0.50/day, $10/month), and which providers may receive `normal`/`private` data.
6. Strongly recommended: Settings → **Enable 2-step login**, add the key to an authenticator app, confirm with a code (needs `SASTRA_DATA_KEY` on the server).

You can also use the **web console** at your server URL in any browser.

## Step 4 (optional) — On-device model for offline/private chats
In **Termux** (install from F-Droid):
```bash
pkg update && pkg install llama-cpp wget
mkdir -p ~/models && cd ~/models
# pick a small instruct model in GGUF format, Q4_K_M quantisation, from Hugging Face
wget -O model.gguf "<direct .gguf download link>"
llama-server -m model.gguf --host 127.0.0.1 --port 8080 -c 4096
```
In SASTRA → **Models** → endpoint `http://127.0.0.1:8080/v1`, model name (any label) → **Benchmark on this phone**. It reports time-to-first-token, tokens/s, battery % and battery temperature before/after.

Rough fit (measure — phones differ a lot): 4 GB RAM → 0.5–1.5B parameters; 6–8 GB → up to ~3B; 12 GB+ → 7–8B (slow, warm). If the benchmark shows > 5 s to first token or the phone gets hot, use a smaller model.

## Everyday use
- **Home**: type anything. Questions are answered in chat; "build/research/analyse/draft…" become background tasks.
- **Approvals**: anything that sends, publishes, deletes or schedules waits here.
- **Offline**: chat still works (on-device model, or arithmetic/units/notes/memory search); tasks and notes queue and sync automatically every ~15 minutes or via Settings → Sync now.
- Chat commands: `/remember subject: text`, `/forget <id>`, `/task <request>`, `/status`.

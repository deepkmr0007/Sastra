#!/data/data/com.termux/files/usr/bin/bash
# Run the SASTRA server on the phone itself (Termux). One command:
#   bash ~/Sastra/scripts/termux.sh
# First run installs everything (can take 15–30 min while Rust builds pydantic-core).
# Then in the SASTRA app: Settings → server URL = http://127.0.0.1:8787
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS="$HOME/.sastra/secrets"
mkdir -p "$HOME/.sastra"

if [ ! -f "$HOME/.sastra/.installed" ]; then
  echo "== First run: installing (keep the screen on) =="
  pkg update -y && pkg upgrade -y
  pkg install -y python git rust binutils python-cryptography || pkg install -y python git rust binutils
  pip install --upgrade pip
  pip install starlette uvicorn pydantic PyJWT pypdf openpyxl
  touch "$HOME/.sastra/.installed"
fi

if [ ! -f "$SECRETS" ]; then
  echo "== Creating your secret keys (stored only on this phone) =="
  cd "$REPO/backend"
  {
    echo "export SASTRA_DATA_KEY=$(python -m sastra gen-key)"
    echo "export SASTRA_JWT_SECRET=$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
    echo "# Optional — uncomment and fill in, then restart:"
    echo "# export SASTRA_ANTHROPIC_API_KEY=sk-ant-..."
    echo "# export SASTRA_BRAVE_API_KEY=..."
  } > "$SECRETS"
  chmod 600 "$SECRETS"
  echo "Keys saved in $SECRETS — back this file up somewhere safe (it decrypts your backups)."
fi

# shellcheck disable=SC1090
source "$SECRETS"
export SASTRA_DATA_DIR="$HOME/.sastra/data"
export SASTRA_SANDBOX_BACKEND=disabled   # no Docker on a phone: code tools stay off (safe default)

command -v termux-wake-lock >/dev/null && termux-wake-lock || true   # keep running with the screen off
cd "$REPO/backend"
echo "== SASTRA starting. In the app use server URL: http://127.0.0.1:8787 =="
python -m sastra setup-code | sed 's/^/Setup code (first run only): /'
exec python -m sastra serve --host 127.0.0.1 --port 8787

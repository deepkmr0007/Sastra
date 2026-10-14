#!/usr/bin/env bash
# Local development server (no Docker): weak process sandbox, data in ./data
set -euo pipefail
cd "$(dirname "$0")/../backend"
export SASTRA_DATA_DIR="${SASTRA_DATA_DIR:-$PWD/../data}"
export SASTRA_ALLOW_WEAK_SANDBOX="${SASTRA_ALLOW_WEAK_SANDBOX:-1}"
if [ -z "${SASTRA_DATA_KEY:-}" ]; then
  KEYFILE="$SASTRA_DATA_DIR/.dev-data-key"
  mkdir -p "$SASTRA_DATA_DIR"
  [ -f "$KEYFILE" ] || python3 -m sastra gen-key > "$KEYFILE"
  export SASTRA_DATA_KEY="$(cat "$KEYFILE")"
  echo "Dev data key stored in $KEYFILE (development only)"
fi
python3 -m sastra serve --host 127.0.0.1 --port 8787

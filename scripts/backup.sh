#!/usr/bin/env bash
# Nightly encrypted backup + retention (keep 14). Add to cron:
#   15 2 * * * /opt/sastra/scripts/backup.sh >> /var/log/sastra-backup.log 2>&1
# Then copy the newest file off-site (rclone, scp to another machine, …).
set -euo pipefail
cd "$(dirname "$0")/../deploy"
docker compose exec -T sastra python -m sastra backup
docker compose exec -T sastra sh -c 'ls -1t /data/backups/*.bak | tail -n +15 | xargs -r rm -f'
docker compose exec -T sastra python -m sastra verify-audit

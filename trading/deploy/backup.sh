#!/usr/bin/env bash
# Nightly consistent SQLite backups of the trading state (keeps 14 days).
set -euo pipefail
cd "$(dirname "$0")"
dest=/var/backups/tradebot; mkdir -p "$dest"; chmod 700 "$dest"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
for db in control paper live; do
  docker compose exec -T tradebot python -c "
import sqlite3, os, sys
src = '/data/$db.db'
if not os.path.exists(src): sys.exit(0)
sqlite3.connect(src).backup(sqlite3.connect('/data/$db.backup.db'))" 
  cid=$(docker compose ps -q tradebot)
  docker cp "$cid:/data/$db.backup.db" "$dest/$db-$stamp.db" 2>/dev/null || true
done
find "$dest" -name '*.db' -mtime +14 -delete

#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: bash ops/db/restore.sh /path/to/backup.dump"
  exit 1
fi

backup_file=$1
if [[ ! -f "$backup_file" ]]; then
  echo "Backup file not found: $backup_file"
  exit 1
fi

db_host=${PGHOST:-localhost}
db_port=${PGPORT:-5432}
db_name=${PGDATABASE:-selfrag}
db_user=${PGUSER:-postgres}

start_epoch=$(date +%s)

if command -v pg_restore >/dev/null 2>&1; then
  pg_restore \
    --host "$db_host" \
    --port "$db_port" \
    --username "$db_user" \
    --dbname "$db_name" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    "$backup_file"
else
  if ! command -v docker >/dev/null 2>&1; then
    echo "pg_restore and docker are both unavailable; cannot restore"
    exit 1
  fi

  container_name=${POSTGRES_CONTAINER_NAME:-selfrag-postgres}
  docker cp "$backup_file" "$container_name":/tmp/selfrag_restore.dump
  docker exec "$container_name" pg_restore \
    --username "$db_user" \
    --dbname "$db_name" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    /tmp/selfrag_restore.dump
  docker exec "$container_name" rm -f /tmp/selfrag_restore.dump
fi

end_epoch=$(date +%s)
duration=$((end_epoch - start_epoch))

echo "Restore completed from: $backup_file"
echo "Restore duration_seconds: $duration"

#!/usr/bin/env bash
set -euo pipefail

timestamp=$(date +"%Y%m%d_%H%M%S")
output_dir=${BACKUP_OUTPUT_DIR:-ops/db/backups}
mkdir -p "$output_dir"

db_host=${PGHOST:-localhost}
db_port=${PGPORT:-5432}
db_name=${PGDATABASE:-selfrag}
db_user=${PGUSER:-postgres}

backup_file="$output_dir/selfrag_${timestamp}.dump"

start_epoch=$(date +%s)

if command -v pg_dump >/dev/null 2>&1; then
  pg_dump \
    --host "$db_host" \
    --port "$db_port" \
    --username "$db_user" \
    --dbname "$db_name" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --file "$backup_file"
else
  if ! command -v docker >/dev/null 2>&1; then
    echo "pg_dump and docker are both unavailable; cannot create backup"
    exit 1
  fi

  container_name=${POSTGRES_CONTAINER_NAME:-selfrag-postgres}
  docker exec "$container_name" pg_dump \
    --username "$db_user" \
    --dbname "$db_name" \
    --format=custom \
    --no-owner \
    --no-privileges \
    --file /tmp/selfrag_backup.dump
  docker cp "$container_name":/tmp/selfrag_backup.dump "$backup_file"
  docker exec "$container_name" rm -f /tmp/selfrag_backup.dump
fi

end_epoch=$(date +%s)
duration=$((end_epoch - start_epoch))

echo "Backup created: $backup_file"
echo "Backup duration_seconds: $duration"

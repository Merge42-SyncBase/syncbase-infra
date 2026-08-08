#!/bin/sh
set -eu

: "${SYNCBASE_POSTGRES_OWNER_PASSWORD:?required}"
: "${SYNCBASE_WEB_DB_PASSWORD:?required}"
: "${SYNCBASE_WORKER_DB_PASSWORD:?required}"
: "${SYNCBASE_MCP_DB_PASSWORD:?required}"

export PGPASSWORD="$SYNCBASE_POSTGRES_OWNER_PASSWORD"
attempt=0
until psql --host=postgres --username=syncbase --dbname=syncbase --set=ON_ERROR_STOP=1 --command='SELECT 1' >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "PostgreSQL did not become stable before role configuration" >&2
    exit 1
  fi
  sleep 1
done
exec psql \
  --host=postgres \
  --username=syncbase \
  --dbname=syncbase \
  --set=ON_ERROR_STOP=1 \
  --file=/opt/syncbase/postgres/runtime-roles.sql

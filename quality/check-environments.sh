#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_root"

common_env=(
  SYNCBASE_POSTGRES_OWNER_PASSWORD=owner-test-password
  SYNCBASE_WEB_DB_PASSWORD=web-test-password
  SYNCBASE_WORKER_DB_PASSWORD=worker-test-password
  SYNCBASE_MCP_DB_PASSWORD=mcp-test-password
  SYNCBASE_ADMIN_PASSWORD_BCRYPT=bcrypt-test-placeholder
  SYNCBASE_MCP_TOKEN_FILE=/tmp/syncbase-environment-check-token
  SYNCBASE_MCP_TOKEN_SHA256=0000000000000000000000000000000000000000000000000000000000000000
  SYNCBASE_PUBLIC_BASE_URL=https://syncbase.example.test
  SYNCBASE_ORT_LIBRARY_FILE=libonnxruntime.so.1.26.0
)

local_json="$(env "${common_env[@]}" \
  SYNCBASE_DB_HOST=postgres \
  SYNCBASE_DB_SSLMODE=disable \
  SYNCBASE_GITHUB_TOKEN_FILE=/tmp/syncbase-environment-check-token \
  docker compose \
  -f infra/compose.yml \
  -f infra/environments/local/compose.yml \
  config --format json)"
prod_json="$(env "${common_env[@]}" \
  SYNCBASE_WEB_IMAGE=registry.example/syncbase-web:test \
  SYNCBASE_API_IMAGE=registry.example/syncbase-api:test \
  SYNCBASE_WORKER_IMAGE=registry.example/syncbase-worker:test \
  SYNCBASE_MIGRATE_IMAGE=registry.example/syncbase-migrate:test \
  SYNCBASE_MCP_IMAGE=registry.example/syncbase-mcp:test \
  SYNCBASE_DB_HOST=opensql.example.test \
  SYNCBASE_DB_SSLMODE=require \
  docker compose \
  -f infra/compose.yml \
  -f infra/environments/prod/compose.yml \
  config --format json)"

jq -e --arg root "$project_root" '
  .name == "syncbase" and
  .services.api.build.context == ($root + "/was") and
  .services.web.build.context == ($root + "/frontend") and
  .services.mcp.build.context == ($root + "/mcp") and
  .services.api.user == "0:0" and
  .services.api.entrypoint == ["/usr/local/bin/syncbase-api-entrypoint"] and
  .services.api.environment.SYNCBASE_COOKIE_SECURE == "false" and
  (.services.postgres != null) and
  (.services.roles != null) and
  (.services.permissions != null)
' >/dev/null <<<"$local_json"

jq -e '
  .name == "syncbase-prod" and
  (.services.web.build | not) and
  (.services.api.build | not) and
  (.services.worker.build | not) and
  (.services.mcp.build | not) and
  .services.web.image == "registry.example/syncbase-web:test" and
  .services.api.image == "registry.example/syncbase-api:test" and
  .services.api.user == "0:0" and
  .services.api.entrypoint == ["/usr/local/bin/syncbase-api-entrypoint"] and
  .services.api.environment.SYNCBASE_COOKIE_SECURE == "true" and
  all(.services.web.ports[]; .host_ip == "127.0.0.1") and
  all(.services.mcp.ports[]; .host_ip == "127.0.0.1") and
  (.services.postgres == null) and
  (.services.roles == null) and
  (.services.permissions == null) and
  (.volumes["postgres-data"] == null)
' >/dev/null <<<"$prod_json"

printf 'ENVIRONMENT_CHECK_PASS local=build prod=images prod_bind=loopback\n'

#!/usr/bin/env bash
set -euo pipefail

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_root="${SYNCBASE_WORKSPACE_ROOT:-$(cd "$infra_root/.." && pwd)}"

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
  SYNCBASE_API_IMAGE=registry.example/syncbase-api:test \
  SYNCBASE_WORKER_IMAGE=registry.example/syncbase-worker:test \
  SYNCBASE_MIGRATE_IMAGE=registry.example/syncbase-migrate:test \
  SYNCBASE_WEB_IMAGE=registry.example/syncbase-web:test \
  SYNCBASE_MCP_IMAGE=registry.example/syncbase-mcp:test \
  docker compose \
  -f "$infra_root/compose.yml" \
  -f "$infra_root/environments/local/compose.yml" \
  config --format json)"
local_build_json="$(env "${common_env[@]}" \
  SYNCBASE_DB_HOST=postgres \
  SYNCBASE_DB_SSLMODE=disable \
  SYNCBASE_API_IMAGE=registry.example/syncbase-api:test \
  SYNCBASE_WORKER_IMAGE=registry.example/syncbase-worker:test \
  SYNCBASE_MIGRATE_IMAGE=registry.example/syncbase-migrate:test \
  SYNCBASE_WEB_IMAGE=registry.example/syncbase-web:test \
  SYNCBASE_MCP_IMAGE=registry.example/syncbase-mcp:test \
  docker compose \
  -f "$infra_root/compose.yml" \
  -f "$infra_root/environments/local/compose.yml" \
  -f "$infra_root/environments/local/build-was.yml" \
  -f "$infra_root/environments/local/build-mcp.yml" \
  -f "$infra_root/environments/local/build-frontend.yml" \
  config --format json)"
prod_json="$(env "${common_env[@]}" \
  SYNCBASE_WEB_IMAGE=registry.example/syncbase-web:test \
  SYNCBASE_API_IMAGE=registry.example/syncbase-api:test \
  SYNCBASE_WORKER_IMAGE=registry.example/syncbase-worker:test \
  SYNCBASE_MIGRATE_IMAGE=registry.example/syncbase-migrate:test \
  SYNCBASE_MCP_IMAGE=registry.example/syncbase-mcp:test \
  SYNCBASE_MODEL_FETCHER_IMAGE=registry.example/syncbase-model-fetcher:test \
  SYNCBASE_DB_HOST=opensql.example.test \
  SYNCBASE_DB_SSLMODE=require \
  docker compose \
  -f "$infra_root/compose.yml" \
  -f "$infra_root/environments/prod/compose.yml" \
  config --format json)"

jq -e '
  .name == "syncbase" and
  (.services.api.build | not) and
  (.services.web.build | not) and
  (.services.mcp.build | not) and
  .services.api.image == "registry.example/syncbase-api:test" and
  .services.web.image == "registry.example/syncbase-web:test" and
  .services.mcp.image == "registry.example/syncbase-mcp:test" and
  .services.api.user == "0:0" and
  .services.api.entrypoint == ["/usr/local/bin/syncbase-api-entrypoint"] and
  .services.api.environment.SYNCBASE_COOKIE_SECURE == "false" and
  .services.api.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.worker.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.mcp.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.migrate.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.api.environment.SYNCBASE_ORIGINAL_ROOT == "/data/originals" and
  .services.worker.environment.SYNCBASE_ORIGINAL_ROOT == "/data/originals" and
  .services.mcp.environment.SYNCBASE_ORIGINAL_ROOT == "/data/originals" and
  ([.services.api.volumes[] | select(.source == "originals" and .target == "/data/originals" and .read_only != true)] | length) == 1 and
  ([.services.worker.volumes[] | select(.source == "originals" and .target == "/data/originals" and .read_only != true)] | length) == 1 and
  ([.services.mcp.volumes[] | select(.source == "originals" and .target == "/data/originals" and .read_only == true)] | length) == 1 and
  (.services.postgres != null) and
  (.services.roles != null) and
  (.services.permissions != null) and
  (.services.models != null)
' >/dev/null <<<"$local_json"

# With all three build-*.yml overlays layered on, was/mcp/frontend should
# build from their sibling source directories instead of pulling.
jq -e --arg root "$workspace_root" '
  .services.api.build.context == ($root + "/syncbase-was") and
  .services.worker.build.context == ($root + "/syncbase-was") and
  .services.migrate.build.context == ($root + "/syncbase-was") and
  .services.mcp.build.context == ($root + "/syncbase-mcp") and
  .services.web.build.context == ($root + "/SyncBase-FE") and
  .services.api.environment.SYNCBASE_COOKIE_SECURE == "false"
' >/dev/null <<<"$local_build_json"

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
  .services.api.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.worker.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.mcp.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.migrate.environment.SYNCBASE_MINIMUM_SCORE == "0.62" and
  .services.api.environment.SYNCBASE_ORIGINAL_ROOT == "/data/originals" and
  .services.worker.environment.SYNCBASE_ORIGINAL_ROOT == "/data/originals" and
  .services.mcp.environment.SYNCBASE_ORIGINAL_ROOT == "/data/originals" and
  ([.services.api.volumes[] | select(.source == "originals" and .target == "/data/originals" and .read_only != true)] | length) == 1 and
  ([.services.worker.volumes[] | select(.source == "originals" and .target == "/data/originals" and .read_only != true)] | length) == 1 and
  ([.services.mcp.volumes[] | select(.source == "originals" and .target == "/data/originals" and .read_only == true)] | length) == 1 and
  all(.services.web.ports[]; .host_ip == "127.0.0.1") and
  all(.services.mcp.ports[]; .host_ip == "127.0.0.1") and
  (.services.postgres == null) and
  (.services.roles != null) and
  (.services.permissions != null) and
  (.services.models != null) and
  (.volumes["postgres-data"] == null)
' >/dev/null <<<"$prod_json"

printf 'ENVIRONMENT_CHECK_PASS local=images local_build_overlay=build prod=images prod_bind=loopback\n'

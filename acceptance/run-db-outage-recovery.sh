#!/usr/bin/env bash
set -euo pipefail

required=(
  SYNCBASE_WEB_URL
  SYNCBASE_SESSION_COOKIE_JAR
  SYNCBASE_MCP_URL
  SYNCBASE_MCP_TOKEN_FILE
  SYNCBASE_SEARCH_QUERY
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done

[[ -r "$SYNCBASE_SESSION_COOKIE_JAR" ]] || { echo "session cookie jar unreadable" >&2; exit 66; }
[[ -r "$SYNCBASE_MCP_TOKEN_FILE" ]] || { echo "MCP token file unreadable" >&2; exit 66; }

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
expected_version="${SYNCBASE_EXPECTED_DOCUMENT_VERSION:-2}"
expected_document_id="${SYNCBASE_EXPECTED_DOCUMENT_ID:-}"
if [[ -n "$expected_document_id" && -r "$expected_document_id" ]]; then
  expected_document_id="$(tr -d '\r\n' <"$expected_document_id")"
fi
[[ "$expected_version" =~ ^[1-9][0-9]*$ ]] || {
  echo "SYNCBASE_EXPECTED_DOCUMENT_VERSION must be a positive integer" >&2
  exit 64
}
if [[ -n "${SYNCBASE_COMPOSE_FILE:-}" ]]; then
  compose=(docker compose -f "$SYNCBASE_COMPOSE_FILE")
elif [[ -n "${COMPOSE_FILE:-}" ]]; then
  compose=(docker compose)
else
  compose=(
    docker compose
    -f "$project_root/infra/compose.yml"
    -f "$project_root/infra/environments/local/compose.yml"
  )
fi
web_url="${SYNCBASE_WEB_URL%/}"
mcp_url="${SYNCBASE_MCP_URL%/}"
mcp_token="$(tr -d '\n' <"$SYNCBASE_MCP_TOKEN_FILE")"
request_body="$(jq -cn --arg query "$SYNCBASE_SEARCH_QUERY" \
  '{jsonrpc:"2.0",id:7,method:"tools/call",params:{name:"search_documents",arguments:{query:$query,limit:20}}}')"
postgres_stopped=false
temporary_dir="$(mktemp -d)"

restore_database() {
  if [[ "$postgres_stopped" == true ]]; then
    "${compose[@]}" start postgres >/dev/null 2>&1 || true
  fi
  rm -rf "$temporary_dir"
}
trap restore_database EXIT

service_id() {
  local service="$1" id
  id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$id" ]] || { echo "$service container is not running" >&2; exit 1; }
  printf '%s\n' "$id"
}

call_mcp() {
  curl --fail --silent --show-error --max-time 15 \
    --header "Authorization: Bearer $mcp_token" \
    --header 'Content-Type: application/json' \
    --header 'Accept: application/json, text/event-stream' \
    --data "$request_body" \
    "$mcp_url/mcp"
}

assert_active_version() {
  local response="$1"
  if [[ -n "$expected_document_id" ]]; then
    jq -e --arg document "$expected_document_id" --argjson expected "$expected_version" '
      any(.result.structuredContent.results[]?;
          .document_id == $document and .document_version == $expected)
    ' >/dev/null <<<"$response"
    return
  fi
  jq -e --argjson expected "$expected_version" '
      (.result.structuredContent.results | length) > 0 and
      all(.result.structuredContent.results[]; .document_version == $expected)
    ' >/dev/null <<<"$response"
}

before_mcp="$(service_id mcp)"
before_api="$(service_id api)"
before_web="$(service_id web)"
before_worker="$(service_id worker)"
assert_active_version "$(call_mcp)"

"${compose[@]}" stop postgres >/dev/null
postgres_stopped=true

unavailable_response="$(call_mcp)"
jq -e '
  .result.isError == true and
  any(.result.content[]?; .type == "text" and .text == "TEMPORARILY_UNAVAILABLE")
' >/dev/null <<<"$unavailable_response"

api_outage_body="$temporary_dir/api-outage.json"
api_outage_status="$(curl --silent --show-error --max-time 15 \
  --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
  --output "$api_outage_body" --write-out '%{http_code}' \
  --get --data-urlencode "q=$SYNCBASE_SEARCH_QUERY" \
  "$web_url/api/v1/search" || true)"
[[ "$api_outage_status" == "503" ]] || {
  echo "API outage search status=$api_outage_status, want 503" >&2
  cat "$api_outage_body" >&2
  exit 1
}
jq -e '.error.code == "TEMPORARILY_UNAVAILABLE" and .error.retryable == true' \
  "$api_outage_body" >/dev/null

"${compose[@]}" start postgres >/dev/null
postgres_stopped=false
for attempt in $(seq 1 60); do
  if curl --fail --silent "$web_url/readyz" >/dev/null && \
    curl --fail --silent "$mcp_url/readyz" >/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 60 ]]; then
    echo "services did not recover after PostgreSQL restart" >&2
    exit 1
  fi
  sleep 1
done

assert_active_version "$(call_mcp)"
[[ "$before_mcp" == "$(service_id mcp)" ]]
[[ "$before_api" == "$(service_id api)" ]]
[[ "$before_web" == "$(service_id web)" ]]
[[ "$before_worker" == "$(service_id worker)" ]]

printf 'DB_OUTAGE_RECOVERY_PASS code=TEMPORARILY_UNAVAILABLE app_restarted=false active_version=%s\n' \
  "$expected_version"

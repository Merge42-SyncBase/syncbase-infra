#!/usr/bin/env bash
set -euo pipefail

required=(
  SYNCBASE_WEB_URL
  SYNCBASE_SESSION_COOKIE_JAR
  SYNCBASE_MCP_URL
  SYNCBASE_MCP_TOKEN_FILE
  SYNCBASE_SEARCH_QUERY
  SYNCBASE_SAMPLE_PDF
  SYNCBASE_SAMPLE_DOCUMENT_NAME
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done

[[ -r "$SYNCBASE_SESSION_COOKIE_JAR" ]] || { echo "session cookie jar unreadable" >&2; exit 66; }
[[ -r "$SYNCBASE_MCP_TOKEN_FILE" ]] || { echo "MCP token file unreadable" >&2; exit 66; }
[[ -r "$SYNCBASE_SAMPLE_PDF" ]] || { echo "sample PDF unreadable" >&2; exit 66; }

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
worker_paused=false
temporary_dir="$(mktemp -d)"

restore_database() {
  if [[ "$postgres_stopped" == true ]]; then
    "${compose[@]}" start postgres >/dev/null 2>&1 || true
  fi
  if [[ "$worker_paused" == true ]]; then
    "${compose[@]}" unpause worker >/dev/null 2>&1 || true
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

service_started_at() {
  docker inspect --format '{{.State.StartedAt}}' "$(service_id "$1")"
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

csrf_token() {
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    --header 'Accept: application/json' "$web_url/api/v1/session" | jq -er '.csrfToken'
}

document_response() {
  local document_id="$1"
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    "$web_url/api/v1/documents/$document_id"
}

wait_for_processing() {
  local document_id="$1" response
  for attempt in $(seq 1 100); do
    response="$(document_response "$document_id")"
    if jq -e '
      (.versions | length == 1) and (.versions[0].status == "PROCESSING") and
      (.activeVersion == null) and (.versions[0].runId | strings)
    ' >/dev/null <<<"$response"; then
      return 0
    fi
    if jq -e '(.versions | length == 1) and (.versions[0].status == "ACTIVE")' >/dev/null <<<"$response"; then
      echo "in-flight recovery document completed before database outage injection" >&2
      return 1
    fi
    sleep 0.1
  done
  echo "worker did not claim the recovery document" >&2
  return 1
}

wait_for_recovered_processing() {
  local document_id="$1" response
  for attempt in $(seq 1 120); do
    response="$(document_response "$document_id")"
    if jq -e '
      (.activeVersion == 1) and (.versions | length == 1) and
      (.versions[0].status == "ACTIVE") and (.versions[0].pageCount >= 1)
    ' >/dev/null <<<"$response"; then
      return 0
    fi
    sleep 1
  done
  echo "in-flight processing run did not recover to active" >&2
  return 1
}

register_recovery_document() {
  local request_key="$1" csrf result
  csrf="$(csrf_token)"
  result="$(curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    --header 'Accept: application/json' --header "X-CSRF-Token: $csrf" \
    --form "documentName=$SYNCBASE_SAMPLE_DOCUMENT_NAME PostgreSQL recovery" \
    --form "requestKey=$request_key" \
    --form "file=@$SYNCBASE_SAMPLE_PDF;type=application/pdf" \
    "$web_url/api/v1/documents")"
  jq -er '.documentId | strings' <<<"$result"
}

before_mcp="$(service_id mcp)"
before_api="$(service_id api)"
before_web="$(service_id web)"
before_worker="$(service_id worker)"
before_mcp_started_at="$(service_started_at mcp)"
before_api_started_at="$(service_started_at api)"
before_web_started_at="$(service_started_at web)"
before_worker_started_at="$(service_started_at worker)"
assert_active_version "$(call_mcp)"

# Queue a fresh document while the worker is frozen, then interrupt it after
# it has leased the run. pause/unpause preserves the same process and avoids
# turning the recovery demonstration into an application restart test.
"${compose[@]}" pause worker >/dev/null
worker_paused=true
recovery_document_id="$(register_recovery_document "postgres-recovery-$(openssl rand -hex 16)")"
[[ "$recovery_document_id" =~ ^[0-9a-f-]{36}$ ]] || {
  echo "recovery registration did not return a document ID" >&2
  exit 1
}
queued_response="$(document_response "$recovery_document_id")"
jq -e '.activeVersion == null and (.versions | length == 1) and .versions[0].status == "QUEUED"' \
  >/dev/null <<<"$queued_response"
"${compose[@]}" unpause worker >/dev/null
worker_paused=false
wait_for_processing "$recovery_document_id"

recovery_started_at="$SECONDS"
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
rto_seconds=$((SECONDS - recovery_started_at))
(( rto_seconds <= 30 )) || {
  echo "PostgreSQL outage recovery exceeded 30 seconds: ${rto_seconds}s" >&2
  exit 1
}

assert_active_version "$(call_mcp)"
wait_for_recovered_processing "$recovery_document_id"
[[ "$before_mcp" == "$(service_id mcp)" ]]
[[ "$before_api" == "$(service_id api)" ]]
[[ "$before_web" == "$(service_id web)" ]]
[[ "$before_worker" == "$(service_id worker)" ]]
[[ "$before_mcp_started_at" == "$(service_started_at mcp)" ]]
[[ "$before_api_started_at" == "$(service_started_at api)" ]]
[[ "$before_web_started_at" == "$(service_started_at web)" ]]
[[ "$before_worker_started_at" == "$(service_started_at worker)" ]]

printf 'DB_OUTAGE_RECOVERY_PASS code=TEMPORARILY_UNAVAILABLE app_restarted=false active_version=%s processing_recovered=true committed_registration_retained=true rto_seconds=%s\n' \
	"$expected_version" "$rto_seconds"

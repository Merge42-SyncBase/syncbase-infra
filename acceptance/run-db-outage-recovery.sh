#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "${SYNCBASE_OUTAGE_ENVIRONMENT:-}" != "isolated-test" ]]; then
  echo "database outage diagnostic requires SYNCBASE_OUTAGE_ENVIRONMENT=isolated-test" >&2
  exit 64
fi

required=(
  SYNCBASE_COMPOSE_PROJECT_NAME
  SYNCBASE_COMPOSE_ENV_FILE
  SYNCBASE_EVIDENCE_DIR
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

[[ "$SYNCBASE_COMPOSE_PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || {
  echo "invalid SYNCBASE_COMPOSE_PROJECT_NAME" >&2
  exit 64
}
require_loopback_origin() {
  local name="$1" value="${!1}" port
  local pattern='^https?://(localhost|127[.]0[.]0[.]1|\[::1\])(:([0-9]{1,5}))?/?$'
  if [[ "$value" =~ $pattern ]]; then
    port="${BASH_REMATCH[3]}"
    if [[ -z "$port" ]] || (( 10#$port >= 1 && 10#$port <= 65535 )); then
      return 0
    fi
  fi
  echo "$name must be an uncredentialed loopback origin without a path, query, or fragment" >&2
  exit 64
}

require_loopback_origin SYNCBASE_WEB_URL
require_loopback_origin SYNCBASE_MCP_URL
[[ "$SYNCBASE_EVIDENCE_DIR" = /* && "$SYNCBASE_EVIDENCE_DIR" != *$'\n'* ]] || {
  echo "SYNCBASE_EVIDENCE_DIR must be an absolute path" >&2
  exit 64
}

file_mode() {
  local path="$1" mode
  if mode="$(stat -f '%Lp' "$path" 2>/dev/null)" && [[ "$mode" =~ ^[0-7]{3,4}$ ]]; then
    printf '%s\n' "$mode"
    return 0
  fi
  mode="$(stat -c '%a' "$path" 2>/dev/null)" && [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  printf '%s\n' "$mode"
}

require_protected_file() {
  local name="$1" path="${!1}" mode
  [[ "$path" = /* && -f "$path" && -r "$path" ]] || {
    echo "$name must name a readable absolute regular file" >&2
    exit 66
  }
  mode="$(file_mode "$path")" || {
    echo "$name permissions could not be checked" >&2
    exit 66
  }
  [[ "$mode" =~ ^[0-7]*00$ ]] || {
    echo "$name must not be group- or world-accessible" >&2
    exit 66
  }
}

require_protected_file SYNCBASE_COMPOSE_ENV_FILE
require_protected_file SYNCBASE_SESSION_COOKIE_JAR
require_protected_file SYNCBASE_MCP_TOKEN_FILE
[[ "$SYNCBASE_SAMPLE_PDF" = /* && -f "$SYNCBASE_SAMPLE_PDF" && -r "$SYNCBASE_SAMPLE_PDF" ]] || {
  echo "SYNCBASE_SAMPLE_PDF must name a readable absolute regular file" >&2
  exit 66
}

for config_path_name in SYNCBASE_SESSION_COOKIE_JAR SYNCBASE_MCP_TOKEN_FILE; do
  config_path_value="${!config_path_name}"
  case "$config_path_value" in
    *$'\n'*|*$'\r'*|*'"'*|*$'\\'*)
      echo "$config_path_name contains characters unsafe for a curl config" >&2
      exit 64
      ;;
  esac
done
if ! LC_ALL=C tr -d '\r\n' <"$SYNCBASE_MCP_TOKEN_FILE" |
  grep -Eq '^[A-Za-z0-9._~-]{16,255}$'; then
  echo "MCP token file has an invalid protected value" >&2
  exit 66
fi

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_files=(
  "$infra_root/compose.yml"
  "$infra_root/environments/local/compose.yml"
  "$infra_root/environments/local/build-was.yml"
  "$infra_root/environments/local/build-mcp.yml"
  "$infra_root/environments/local/build-frontend.yml"
)
for compose_file in "${compose_files[@]}"; do
  [[ -r "$compose_file" ]] || { echo "required Compose file is unreadable" >&2; exit 66; }
done
compose=(
  docker compose
  --ansi never
  --project-name "$SYNCBASE_COMPOSE_PROJECT_NAME"
  --project-directory "$infra_root"
  --env-file "$SYNCBASE_COMPOSE_ENV_FILE"
)
for compose_file in "${compose_files[@]}"; do
  compose+=(-f "$compose_file")
done

expected_version="${SYNCBASE_EXPECTED_DOCUMENT_VERSION:-2}"
expected_document_id="${SYNCBASE_EXPECTED_DOCUMENT_ID:-}"
[[ "$expected_version" =~ ^[1-9][0-9]*$ ]] || {
  echo "SYNCBASE_EXPECTED_DOCUMENT_VERSION must be a positive integer" >&2
  exit 64
}
if [[ -n "$expected_document_id" &&
      ! "$expected_document_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
  echo "SYNCBASE_EXPECTED_DOCUMENT_ID must be a UUID when supplied" >&2
  exit 64
fi
run_id="${SYNCBASE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$ ]] || {
  echo "invalid SYNCBASE_RUN_ID" >&2
  exit 64
}

web_url="${SYNCBASE_WEB_URL%/}"
mcp_url="${SYNCBASE_MCP_URL%/}"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$SYNCBASE_EVIDENCE_DIR"
final_evidence_dir="$SYNCBASE_EVIDENCE_DIR/db-outage-recovery-$run_id"
[[ ! -e "$final_evidence_dir" ]] || {
  echo "evidence destination already exists" >&2
  exit 73
}
staging_evidence_dir=""
temporary_dir=""
runtime_mutation_armed=false
worker_may_be_paused=false
postgres_may_be_stopped=false
cleanup() {
  local status=$? restore_failed=false
  trap - EXIT INT TERM HUP
  set +e
  if [[ "$runtime_mutation_armed" == true ]]; then
    if ! "${compose[@]}" start postgres >/dev/null 2>&1; then
      [[ "$postgres_may_be_stopped" != true ]] || restore_failed=true
    fi
    if ! "${compose[@]}" unpause worker >/dev/null 2>&1; then
      [[ "$worker_may_be_paused" != true ]] || restore_failed=true
    fi
  fi
  if [[ -n "$temporary_dir" && -d "$temporary_dir" ]]; then
    rm -rf -- "$temporary_dir"
  fi
  if [[ -n "$staging_evidence_dir" && -d "$staging_evidence_dir" ]]; then
    rm -rf -- "$staging_evidence_dir"
  fi
  if [[ "$restore_failed" == true ]]; then
    echo "cleanup could not restore all isolated diagnostic services" >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

temporary_dir="$(mktemp -d)"
staging_evidence_dir="$(mktemp -d "$SYNCBASE_EVIDENCE_DIR/.db-outage-recovery-$run_id.XXXXXX")"

public_curl_config="$temporary_dir/curl-public.conf"
web_curl_config="$temporary_dir/curl-web.conf"
mcp_curl_config="$temporary_dir/curl-mcp.conf"
csrf_curl_config="$temporary_dir/curl-csrf.conf"
{
  printf '%s\n' 'silent' 'show-error'
  printf '%s\n' 'noproxy = "*"'
  printf '%s\n' 'header = "Accept: application/json"'
} >"$public_curl_config"
{
  cat "$public_curl_config"
  printf 'cookie = "%s"\n' "$SYNCBASE_SESSION_COOKIE_JAR"
} >"$web_curl_config"
{
  printf '%s\n' 'silent' 'show-error'
  printf '%s\n' 'noproxy = "*"'
  printf '%s\n' 'header = "Content-Type: application/json"'
  printf '%s\n' 'header = "Accept: application/json, text/event-stream"'
  printf '%s' 'header = "Authorization: Bearer '
  LC_ALL=C tr -d '\r\n' <"$SYNCBASE_MCP_TOKEN_FILE"
  printf '%s\n' '"'
} >"$mcp_curl_config"
chmod 600 "$public_curl_config" "$web_curl_config" "$mcp_curl_config"

query_file="$temporary_dir/search-query.txt"
request_body_file="$temporary_dir/mcp-request.json"
printf '%s' "$SYNCBASE_SEARCH_QUERY" >"$query_file"
jq -cn --rawfile query "$query_file" '
  {jsonrpc:"2.0",id:7,method:"tools/call",
   params:{name:"search_documents",arguments:{query:$query,limit:20}}}
' >"$request_body_file"

curl_status() {
  local output="$1" max_time="$2" url="$3" status
  shift 3
  if ! status="$(curl "$@" --max-time "$max_time" --output "$output" \
    --write-out '%{http_code}' "$url")"; then
    return 1
  fi
  [[ "$status" =~ ^[0-9]{3}$ ]] || return 1
  printf '%s\n' "$status"
}

call_mcp() {
  local output="$1" max_time="${2:-15}"
  curl_status "$output" "$max_time" "$mcp_url/mcp" \
    --config "$mcp_curl_config" --data-binary "@$request_body_file"
}

assert_active_version() {
  local response_file="$1"
  if [[ -n "$expected_document_id" ]]; then
    jq -e --arg document "$expected_document_id" --argjson expected "$expected_version" '
      .result.isError != true and
      any(.result.structuredContent.results[]?;
          .document_id == $document and .document_version == $expected)
    ' "$response_file" >/dev/null
    return
  fi
  jq -e --argjson expected "$expected_version" '
    .result.isError != true and
    (.result.structuredContent.results | type == "array" and length > 0) and
    all(.result.structuredContent.results[]; .document_version == $expected)
  ' "$response_file" >/dev/null
}

service_id() {
  local service="$1" id
  id="$("${compose[@]}" ps --status running -q "$service" 2>/dev/null)"
  [[ "$id" =~ ^[0-9a-f]{12,64}$ ]] || {
    echo "$service must have exactly one running container" >&2
    return 1
  }
  printf '%s\n' "$id"
}

container_started_at() {
  local id="$1" started
  started="$(docker inspect --format '{{.State.StartedAt}}' "$id" 2>/dev/null)" || return 1
  [[ -n "$started" && "$started" != *$'\n'* && "$started" != *$'\r'* ]] || return 1
  printf '%s\n' "$started"
}

container_running() {
  local id="$1" running
  running="$(docker inspect --format '{{.State.Running}}' "$id" 2>/dev/null)" || return 1
  [[ "$running" == "true" || "$running" == "false" ]] || return 1
  printf '%s\n' "$running"
}

capture_app_state() {
  local output="$1" service id started next
  printf '{}\n' >"$output"
  for service in api web worker mcp; do
    id="$(service_id "$service")" || return 1
    started="$(container_started_at "$id")" || return 1
    next="$output.next"
    jq --arg service "$service" --arg id "$id" --arg started "$started" '
      . + {($service): {id:$id, started_at:$started}}
    ' "$output" >"$next"
    mv "$next" "$output"
  done
}

fetch_csrf_config() {
  local response="$temporary_dir/session.json" csrf_file="$temporary_dir/csrf-token" status
  status="$(curl_status "$response" 15 "$web_url/api/v1/session" \
    --config "$web_curl_config")" || {
    echo "session request failed" >&2
    return 1
  }
  [[ "$status" == "200" ]] || {
    echo "session request did not return HTTP 200" >&2
    return 1
  }
  jq -er '
    .csrfToken | strings |
    select(test("^[A-Za-z0-9._~-]{16,255}$"))
  ' "$response" >"$csrf_file" || {
    echo "session response did not contain a valid CSRF value" >&2
    return 1
  }
  {
    printf '%s' 'header = "X-CSRF-Token: '
    LC_ALL=C tr -d '\r\n' <"$csrf_file"
    printf '%s\n' '"'
  } >"$csrf_curl_config"
  chmod 600 "$csrf_file" "$csrf_curl_config"
}

document_response() {
  local document_id="$1" output="$2" max_time="${3:-5}" status
  if ! status="$(curl_status "$output" "$max_time" \
    "$web_url/api/v1/documents/$document_id" --config "$web_curl_config")"; then
    return 1
  fi
  [[ "$status" == "200" ]]
}

wait_for_processing() {
  local document_id="$1" output="$2" claim_deadline remaining request_timeout
  claim_deadline=$((SECONDS + 10))
  while (( SECONDS < claim_deadline )); do
    remaining=$((claim_deadline - SECONDS))
    request_timeout=$((remaining < 2 ? remaining : 2))
    if document_response "$document_id" "$output" "$request_timeout"; then
      if jq -e '
        (.versions | length == 1) and (.versions[0].status == "PROCESSING") and
        (.activeVersion == null) and (.versions[0].runId | strings)
      ' "$output" >/dev/null; then
        return 0
      fi
      if jq -e '
        (.versions | length == 1) and (.versions[0].status == "ACTIVE")
      ' "$output" >/dev/null; then
        echo "recovery Document completed before outage injection" >&2
        return 1
      fi
    fi
    if (( SECONDS < claim_deadline )); then
      sleep 0.1
    fi
  done
  echo "worker did not claim the recovery Document within 10 seconds" >&2
  return 1
}

wait_for_recovered_processing() {
  local document_id="$1" output="$2" remaining request_timeout
  processing_deadline=$((SECONDS + 120))
  while (( SECONDS < processing_deadline )); do
    remaining=$((processing_deadline - SECONDS))
    request_timeout=$((remaining < 5 ? remaining : 5))
    if document_response "$document_id" "$output" "$request_timeout" &&
      jq -e '
        (.activeVersion == 1) and (.versions | length == 1) and
        (.versions[0].status == "ACTIVE") and (.versions[0].pageCount >= 1)
      ' "$output" >/dev/null; then
      return 0
    fi
    if (( SECONDS < processing_deadline )); then
      sleep 1
    fi
  done
  echo "processing recovery exceeded 120 seconds" >&2
  return 1
}

register_recovery_document() {
  local response="$1" document_name_file request_key_file status
  document_name_file="$temporary_dir/recovery-document-name.txt"
  request_key_file="$temporary_dir/recovery-request-key.txt"
  printf '%s PostgreSQL-compatible recovery' "$SYNCBASE_SAMPLE_DOCUMENT_NAME" >"$document_name_file"
  {
    printf '%s' 'postgres-compatible-recovery-'
    openssl rand -hex 16 | LC_ALL=C tr -d '\r\n'
  } >"$request_key_file"
  fetch_csrf_config
  status="$(curl_status "$response" 15 "$web_url/api/v1/documents" \
    --config "$web_curl_config" --config "$csrf_curl_config" \
    --form "documentName=<$document_name_file" \
    --form "requestKey=<$request_key_file" \
    --form "file=@$SYNCBASE_SAMPLE_PDF;type=application/pdf")" || {
    echo "recovery Document registration request failed" >&2
    return 1
  }
  [[ "$status" == "201" ]] || {
    echo "recovery Document registration did not return HTTP 201" >&2
    return 1
  }
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

before_apps="$temporary_dir/apps-before.json"
after_apps="$temporary_dir/apps-after.json"
before_mcp_response="$temporary_dir/mcp-before.json"
outage_mcp_response="$temporary_dir/mcp-outage.json"
recovery_mcp_response="$temporary_dir/mcp-recovery.json"
api_outage_response="$temporary_dir/api-outage.json"
queued_response="$temporary_dir/document-queued.json"
processing_response="$temporary_dir/document-processing.json"
recovered_processing_response="$temporary_dir/document-recovered.json"
registration_response="$temporary_dir/document-registration.json"
readiness_web_response="$temporary_dir/readiness-web.json"
readiness_mcp_response="$temporary_dir/readiness-mcp.json"

printf '%s\n' \
  'DB_OUTAGE_RECOVERY_SCOPE_NOTICE additional_document=true safe_run_order=after-frozen-benchmark-or-separate-corpus-project' \
  >&2

capture_app_state "$before_apps"
before_postgres_id="$(service_id postgres)"
before_mcp_status="$(call_mcp "$before_mcp_response")" || {
  echo "MCP search failed before outage injection" >&2
  exit 1
}
if [[ "$before_mcp_status" != "200" ]] || ! assert_active_version "$before_mcp_response"; then
  echo "MCP precondition did not prove the expected active Version" >&2
  exit 1
fi

runtime_mutation_armed=true
worker_may_be_paused=true
"${compose[@]}" pause worker >/dev/null 2>&1 || {
  echo "worker could not be paused for the isolated diagnostic" >&2
  exit 1
}
register_recovery_document "$registration_response"
recovery_document_id="$(jq -er '.documentId | strings' "$registration_response")"
[[ "$recovery_document_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || {
  echo "recovery registration did not return a Document ID" >&2
  exit 1
}
document_response "$recovery_document_id" "$queued_response" 5 || {
  echo "queued recovery Document could not be inspected" >&2
  exit 1
}
jq -e '
  .activeVersion == null and (.versions | length == 1) and
  .versions[0].status == "QUEUED"
' "$queued_response" >/dev/null || {
  echo "recovery Document was not observed in QUEUED state" >&2
  exit 1
}
"${compose[@]}" unpause worker >/dev/null 2>&1 || {
  echo "worker could not be unpaused before outage injection" >&2
  exit 1
}
worker_may_be_paused=false
wait_for_processing "$recovery_document_id" "$processing_response"

outage_started_seconds=$SECONDS
postgres_may_be_stopped=true
"${compose[@]}" stop postgres >/dev/null 2>&1 || {
  echo "database container could not be stopped" >&2
  exit 1
}
[[ "$(container_running "$before_postgres_id")" == "false" ]] || {
  echo "database container did not reach stopped state" >&2
  exit 1
}

outage_mcp_status="$(call_mcp "$outage_mcp_response")" || {
  echo "MCP outage contract request failed at the transport boundary" >&2
  exit 1
}
if [[ "$outage_mcp_status" != "200" ]] || ! jq -e '
  .result.isError != true and
  .result.structuredContent.grounding_status == "INSUFFICIENT_EVIDENCE" and
  .result.structuredContent.grounding_reason == "SOURCE_UNAVAILABLE" and
  (.result.structuredContent.results | type == "array" and length == 0)
' "$outage_mcp_response" >/dev/null; then
  echo "MCP did not satisfy its sanitized dependency-unavailable contract" >&2
  exit 1
fi

api_outage_status="$(curl_status "$api_outage_response" 15 "$web_url/api/v1/search" \
  --config "$web_curl_config" --get --data-urlencode "q@$query_file")" || {
  echo "API outage contract request failed at the transport boundary" >&2
  exit 1
}
if [[ "$api_outage_status" != "503" ]] || ! jq -e '
  .error.code == "TEMPORARILY_UNAVAILABLE" and .error.retryable == true
' "$api_outage_response" >/dev/null; then
  echo "API did not satisfy its retryable dependency-unavailable contract" >&2
  exit 1
fi

database_restart_requested_seconds=$SECONDS
"${compose[@]}" start postgres >/dev/null 2>&1 || {
  echo "database container could not be started" >&2
  exit 1
}
postgres_may_be_stopped=false
database_deadline=$((SECONDS + 30))
database_ready=false
web_ready_status=0
mcp_ready_status=0
while (( SECONDS < database_deadline )); do
  remaining=$((database_deadline - SECONDS))
  request_timeout=$((remaining < 2 ? remaining : 2))
  if web_ready_status="$(curl_status "$readiness_web_response" "$request_timeout" \
    "$web_url/readyz" --config "$public_curl_config" 2>/dev/null)" &&
    [[ "$web_ready_status" == "200" ]]; then
    remaining=$((database_deadline - SECONDS))
    if (( remaining > 0 )); then
      request_timeout=$((remaining < 2 ? remaining : 2))
      if mcp_ready_status="$(curl_status "$readiness_mcp_response" "$request_timeout" \
        "$mcp_url/readyz" --config "$public_curl_config" 2>/dev/null)" &&
        [[ "$mcp_ready_status" == "200" ]]; then
        database_ready=true
        break
      fi
    fi
  fi
  if (( SECONDS < database_deadline )); then
    sleep 1
  fi
done
database_readiness_seconds=$((SECONDS - database_restart_requested_seconds))
if [[ "$database_ready" != true ]] || (( database_readiness_seconds > 30 )); then
  echo "database readiness did not recover within 30 seconds" >&2
  exit 1
fi

recovery_mcp_status="$(call_mcp "$recovery_mcp_response")" || {
  echo "MCP search failed after database readiness recovery" >&2
  exit 1
}
if [[ "$recovery_mcp_status" != "200" ]] || ! assert_active_version "$recovery_mcp_response"; then
  echo "MCP recovery did not prove the expected active Version" >&2
  exit 1
fi
processing_wait_started_seconds=$SECONDS
wait_for_recovered_processing "$recovery_document_id" "$recovered_processing_response"
processing_recovery_seconds=$((SECONDS - processing_wait_started_seconds))
(( processing_recovery_seconds <= 120 )) || {
  echo "processing recovery exceeded 120 seconds" >&2
  exit 1
}

after_postgres_id="$(service_id postgres)"
[[ "$before_postgres_id" == "$after_postgres_id" ]] || {
  echo "database container identity changed during the diagnostic" >&2
  exit 1
}
capture_app_state "$after_apps"
jq -ne --slurpfile before "$before_apps" --slurpfile after "$after_apps" '
  $before[0] == $after[0]
' >/dev/null || {
  echo "an application container ID or start time changed during the diagnostic" >&2
  exit 1
}

completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
outage_to_readiness_seconds=$((database_restart_requested_seconds + database_readiness_seconds - outage_started_seconds))
outage_to_processing_recovery_seconds=$((SECONDS - outage_started_seconds))
runner_hash="$(hash_file "$infra_root/acceptance/run-db-outage-recovery.sh")"
result_temporary="$staging_evidence_dir/result.json.tmp"
jq -n \
  --arg run_id "$run_id" \
  --arg started_at "$started_at" \
  --arg completed_at "$completed_at" \
  --arg compose_project "$SYNCBASE_COMPOSE_PROJECT_NAME" \
  --arg before_postgres_id "$before_postgres_id" \
  --arg after_postgres_id "$after_postgres_id" \
  --arg runner_hash "$runner_hash" \
  --argjson expected_version "$expected_version" \
  --argjson before_mcp_status "$before_mcp_status" \
  --argjson outage_mcp_status "$outage_mcp_status" \
  --argjson api_outage_status "$api_outage_status" \
  --argjson web_ready_status "$web_ready_status" \
  --argjson mcp_ready_status "$mcp_ready_status" \
  --argjson recovery_mcp_status "$recovery_mcp_status" \
  --argjson database_readiness_seconds "$database_readiness_seconds" \
  --argjson processing_recovery_seconds "$processing_recovery_seconds" \
  --argjson outage_to_readiness_seconds "$outage_to_readiness_seconds" \
  --argjson outage_to_processing_recovery_seconds "$outage_to_processing_recovery_seconds" \
  --slurpfile before_apps "$before_apps" \
  --slurpfile after_apps "$after_apps" '
  {
    schema_version:"1.0",
    task_id:"DB_OUTAGE_RECOVERY_DIAGNOSTIC",
    run_id:$run_id,
    overall_result:"PASS",
    evidence_grade:"ISOLATED_SINGLE_NODE_DIAGNOSTIC_NOT_RELEASE_CLAIM_GRADE",
    claim_eligible:false,
    environment:"isolated-test",
    topology:"single-node",
    database_compatibility:"PostgreSQL-compatible",
    started_at:$started_at,
    completed_at:$completed_at,
    release_bindings:{
      status:"NOT_SUPPLIED",
      repository_revisions:null,
      image_digests:null,
      required_for_claim_eligibility:true
    },
    corpus_impact:{
      registers_additional_document:true,
      registration_retained_after_diagnostic:true,
      safe_run_order:"AFTER_FROZEN_BENCHMARK_OR_SEPARATE_CORPUS_PROJECT"
    },
    inputs:{
      compose_project_name:$compose_project,
      compose_files:[
        "compose.yml",
        "environments/local/compose.yml",
        "environments/local/build-was.yml",
        "environments/local/build-mcp.yml",
        "environments/local/build-frontend.yml"
      ],
      compose_env_file_supplied:true,
      existing_project_required:true,
      expected_search_active_version:$expected_version,
      protected_values_recorded:false
    },
    facts:{
      before:{
        database_container:{id:$before_postgres_id,running:true},
        app_containers:$before_apps[0],
        mcp_search:{
          http_status:$before_mcp_status,
          expected_active_version:$expected_version,
          contract_observed:true
        },
        recovery_document:{
          queued_observed:true,
          processing_observed:true,
          active_version:null,
          processing_run_present:true
        }
      },
      outage:{
        database_container_stopped:true,
        mcp_search:{
          http_status:$outage_mcp_status,
          is_error:false,
          grounding_status:"INSUFFICIENT_EVIDENCE",
          grounding_reason:"SOURCE_UNAVAILABLE",
          results_count:0,
          contract_observed:true
        },
        api_search:{
          http_status:$api_outage_status,
          error_code:"TEMPORARILY_UNAVAILABLE",
          retryable:true,
          contract_observed:true
        }
      },
      recovery:{
        database_container:{id:$after_postgres_id,running:true,id_unchanged:true},
        readiness:{web_http_status:$web_ready_status,mcp_http_status:$mcp_ready_status},
        app_containers:$after_apps[0],
        app_containers_unchanged:true,
        application_restarts_observed:0,
        search_active_version:$expected_version,
        mcp_search_http_status:$recovery_mcp_status,
        processing:{
          recovered:true,
          status:"ACTIVE",
          active_version:1,
          page_count_at_least_one:true
        },
        committed_registration_retained:true
      }
    },
    measurements:{
      database_readiness_seconds:$database_readiness_seconds,
      database_readiness_limit_seconds:30,
      processing_recovery_seconds:$processing_recovery_seconds,
      processing_recovery_limit_seconds:120,
      outage_to_readiness_seconds:$outage_to_readiness_seconds,
      outage_to_processing_recovery_seconds:$outage_to_processing_recovery_seconds
    },
    artifact_hashes:{
      "syncbase-infra/acceptance/run-db-outage-recovery.sh":$runner_hash
    },
    limitations:[
      "This is a local single-node database container stop/start diagnostic.",
      "Database product identity and multi-node failover were not evaluated.",
      "The diagnostic registers an additional Document; run it after a frozen benchmark or in a separate corpus/project.",
      "Release source revisions and image digest bindings were not supplied."
    ],
    failure_reason:null
  }
' >"$result_temporary"
jq -e '
  .overall_result == "PASS" and
  .claim_eligible == false and
  .environment == "isolated-test" and
  .topology == "single-node" and
  .facts.recovery.app_containers_unchanged == true and
  .measurements.database_readiness_seconds <= 30 and
  .measurements.processing_recovery_seconds <= 120
' "$result_temporary" >/dev/null
mv "$result_temporary" "$staging_evidence_dir/result.json"
mv "$staging_evidence_dir" "$final_evidence_dir"

printf 'DB_OUTAGE_RECOVERY_DIAGNOSTIC_PASS evidence_directory=%s claim_eligible=false database_readiness_seconds=%s processing_recovery_seconds=%s\n' \
  "$final_evidence_dir" "$database_readiness_seconds" "$processing_recovery_seconds"

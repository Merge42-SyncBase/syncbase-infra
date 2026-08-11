#!/usr/bin/env bash
set -euo pipefail

required=(
  SYNCBASE_OPENSQL_GRADE
  SYNCBASE_FAILOVER_ENVIRONMENT
  SYNCBASE_FAILOVER_CLUSTER_ID
  SYNCBASE_FAILOVER_INVENTORY_JSON
  SYNCBASE_FAILOVER_CONFIRMATION_TOKEN_FILE
  SYNCBASE_PRIMARY_STOP_EXECUTABLE
  SYNCBASE_TOPOLOGY_CHECK_EXECUTABLE
  SYNCBASE_G0_EVIDENCE_PATH
  SYNCBASE_PSQL_SERVICE
  PGSERVICEFILE
  SYNCBASE_APP_PID_FILE
  SYNCBASE_EVIDENCE_DIR
  SYNCBASE_WEB_URL
  SYNCBASE_SESSION_COOKIE_JAR
  SYNCBASE_SAMPLE_PDF
  SYNCBASE_SAMPLE_DOCUMENT_NAME
  SYNCBASE_MCP_URL
  SYNCBASE_MCP_TOKEN_FILE
  SYNCBASE_SEARCH_QUERY
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done

[[ "$SYNCBASE_OPENSQL_GRADE" == "actual-opensql" ]] || {
  echo "failover evidence must use SYNCBASE_OPENSQL_GRADE=actual-opensql" >&2
  exit 64
}
[[ "$SYNCBASE_FAILOVER_ENVIRONMENT" == "ha-test" ]] || {
  echo "destructive failover is restricted to environment=ha-test" >&2
  exit 64
}
[[ "$SYNCBASE_FAILOVER_CLUSTER_ID" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || {
  echo "invalid failover cluster id" >&2
  exit 64
}
for executable in "$SYNCBASE_PRIMARY_STOP_EXECUTABLE" "$SYNCBASE_TOPOLOGY_CHECK_EXECUTABLE"; do
  [[ "$executable" = /* && -x "$executable" ]] || { echo "invalid executable: $executable" >&2; exit 66; }
done
for protected_file in \
  "$SYNCBASE_FAILOVER_INVENTORY_JSON" \
  "$SYNCBASE_FAILOVER_CONFIRMATION_TOKEN_FILE" \
  "$SYNCBASE_MCP_TOKEN_FILE" \
  "$SYNCBASE_SESSION_COOKIE_JAR" \
  "$SYNCBASE_SAMPLE_PDF" \
  "$PGSERVICEFILE" \
  "$SYNCBASE_APP_PID_FILE"; do
  [[ -r "$protected_file" ]] || { echo "required protected file unreadable" >&2; exit 66; }
done

jq -e --arg cluster "$SYNCBASE_FAILOVER_CLUSTER_ID" '
  .environment == "ha-test" and .cluster_id == $cluster and
  (.current_primary | type == "string" and length > 0)
' "$SYNCBASE_FAILOVER_INVENTORY_JSON" >/dev/null || {
  echo "inventory does not authorize this HA test cluster" >&2
  exit 64
}
expected_primary="$(jq -r '.current_primary' "$SYNCBASE_FAILOVER_INVENTORY_JSON")"

mkdir -p "$SYNCBASE_EVIDENCE_DIR"
temporary_dir="$(mktemp -d)"
probe_pid=""
cleanup() {
  if [[ -n "$probe_pid" ]]; then kill "$probe_pid" 2>/dev/null || true; fi
  rm -rf "$temporary_dir"
}
trap cleanup EXIT

jq -e '.overall_result == "PASS" and .environment.evidence_grade == "ACTUAL_OPENSQL"' \
  "$SYNCBASE_G0_EVIDENCE_PATH" >/dev/null

"$SYNCBASE_TOPOLOGY_CHECK_EXECUTABLE" \
  --cluster-id "$SYNCBASE_FAILOVER_CLUSTER_ID" >"$temporary_dir/topology-before.json"
jq -e --arg cluster "$SYNCBASE_FAILOVER_CLUSTER_ID" --arg primary "$expected_primary" '
  .environment == "ha-test" and .cluster_id == $cluster and .current_primary == $primary and
  (.eligible_synchronous_standbys | type == "array" and length >= 1)
' "$temporary_dir/topology-before.json" >/dev/null || {
  echo "topology is not eligible for a synchronous failover test" >&2
  exit 1
}

PGSERVICE="$SYNCBASE_PSQL_SERVICE" psql -X -A -t -v ON_ERROR_STOP=1 <<'SQL' \
  >"$temporary_dir/synchronous-settings.txt"
SELECT 'fsync|' || current_setting('fsync');
SELECT 'full_page_writes|' || current_setting('full_page_writes');
SELECT 'synchronous_commit|' || current_setting('synchronous_commit');
SELECT 'synchronous_standby_names_configured|' ||
       CASE WHEN current_setting('synchronous_standby_names') <> '' THEN 'true' ELSE 'false' END;
SELECT 'streaming_sync_standbys|' || count(*)
FROM pg_stat_replication WHERE state = 'streaming' AND sync_state = 'sync';
SQL
awk -F '|' '
  $1 == "fsync" && $2 == "on" {fsync=1}
  $1 == "full_page_writes" && $2 == "on" {full=1}
  $1 == "synchronous_commit" && $2 != "off" && $2 != "local" {commit=1}
  $1 == "synchronous_standby_names_configured" && $2 == "true" {names=1}
  $1 == "streaming_sync_standbys" && $2 + 0 >= 1 {standby=1}
  END {exit !(fsync && full && commit && names && standby)}
' "$temporary_dir/synchronous-settings.txt" || {
  echo "RPO=0 prerequisites are not satisfied" >&2
  exit 1
}

mcp_token="$(<"$SYNCBASE_MCP_TOKEN_FILE")"
request_body="$(jq -cn --arg query "$SYNCBASE_SEARCH_QUERY" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"search_documents",arguments:{query:$query,limit:5}}}')"

millis() { date +%s%3N; }
hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}
probe() {
  local response http_code
  response="$temporary_dir/response.json"
  http_code="$(curl --silent --show-error --max-time 3 --output "$response" --write-out '%{http_code}' \
    --header "Authorization: Bearer $mcp_token" --header 'Content-Type: application/json' \
    --header 'Accept: application/json, text/event-stream' \
    --data "$request_body" "$SYNCBASE_MCP_URL/mcp" || true)"
  [[ "$http_code" == "200" ]] && jq -e '.result.structuredContent.results | type == "array"' \
    "$response" >/dev/null
}
snapshot_ids() {
  PGSERVICE="$SYNCBASE_PSQL_SERVICE" psql -X -A -t -v ON_ERROR_STOP=1 <<'SQL' | LC_ALL=C sort -u
SELECT 'document|' || id FROM syncbase.document;
SELECT 'version|' || id FROM syncbase.document_version;
SELECT 'run|' || id FROM syncbase.processing_run;
SELECT 'checkpoint|' || run_id || '|' || stage FROM syncbase.processing_checkpoint;
SELECT 'change|' || sequence_id FROM syncbase.change_log;
SELECT 'chunk|' || version_id || '|' || profile_fingerprint || '|' || chunk_index FROM syncbase.search_chunk;
SQL
}

probe || { echo "MCP search must succeed before failover" >&2; exit 1; }

csrf_token="$(curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
  --header 'Accept: application/json' "$SYNCBASE_WEB_URL/api/v1/session" | jq -er '.csrfToken')"
request_key="failover-$(openssl rand -hex 16)"
registration="$(curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
  --header 'Accept: application/json' --header "X-CSRF-Token: $csrf_token" \
  --form "documentName=$SYNCBASE_SAMPLE_DOCUMENT_NAME failover" \
  --form "requestKey=$request_key" \
  --form "file=@$SYNCBASE_SAMPLE_PDF;type=application/pdf" \
  "$SYNCBASE_WEB_URL/api/v1/documents")"
document_id="$(jq -er '.documentId' <<<"$registration")"
[[ "$document_id" =~ ^[0-9a-f-]{36}$ ]] || {
  echo "failover processing registration failed" >&2
  exit 1
}
read -r version_id run_id run_status < <(
  PGSERVICE="$SYNCBASE_PSQL_SERVICE" psql -X -A -t -F ' ' -v ON_ERROR_STOP=1 \
    -v document_id="$document_id" <<'SQL'
SELECT v.id, r.id, r.status
FROM syncbase.document_version v
JOIN syncbase.processing_run r ON r.version_id = v.id
WHERE v.document_id = :'document_id'::uuid
ORDER BY v.version_number DESC, r.queued_at DESC LIMIT 1;
SQL
)
[[ "$run_status" == "QUEUED" || "$run_status" == "RUNNING" ]] || {
  echo "processing completed before failover injection; use a larger sample PDF" >&2
  exit 1
}

snapshot_ids >"$temporary_dir/before.ids"
cp "$SYNCBASE_APP_PID_FILE" "$temporary_dir/pids.before"

(
  while true; do
    timestamp="$(millis)"
    if probe; then state="ok"; else state="unavailable"; fi
    printf '%s\t%s\n' "$timestamp" "$state" >>"$temporary_dir/probe.tsv"
    sleep 0.25
  done
) &
probe_pid="$!"

failover_started="$(millis)"
"$SYNCBASE_PRIMARY_STOP_EXECUTABLE" \
  --cluster-id "$SYNCBASE_FAILOVER_CLUSTER_ID" \
  --expected-primary "$expected_primary" \
  --confirmation-token-file "$SYNCBASE_FAILOVER_CONFIRMATION_TOKEN_FILE"

recovered=""
deadline=$((SECONDS + 35))
while (( SECONDS < deadline )); do
  recovered="$(awk -F '\t' -v start="$failover_started" '
    $1 >= start {
      if ($2 == "ok") { first=second; second=third; third=$1;
        if (first > 0 && third-first <= 5000) { print third; exit } }
      else { first=0; second=0; third=0 }
    }' "$temporary_dir/probe.tsv" 2>/dev/null || true)"
  [[ -n "$recovered" ]] && break
  sleep 0.2
done
kill "$probe_pid" 2>/dev/null || true
wait "$probe_pid" 2>/dev/null || true
probe_pid=""

[[ -n "$recovered" ]] || { echo "three consecutive searches did not recover" >&2; exit 1; }
rto_ms=$((recovered - failover_started))
(( rto_ms <= 30000 )) || { echo "RTO exceeded: ${rto_ms}ms" >&2; exit 1; }

"$SYNCBASE_TOPOLOGY_CHECK_EXECUTABLE" \
  --cluster-id "$SYNCBASE_FAILOVER_CLUSTER_ID" >"$temporary_dir/topology-after.json"
jq -e --arg cluster "$SYNCBASE_FAILOVER_CLUSTER_ID" --arg old "$expected_primary" '
  .environment == "ha-test" and .cluster_id == $cluster and
  .current_primary != $old and (.current_primary | length > 0)
' "$temporary_dir/topology-after.json" >/dev/null || {
  echo "a different Primary was not observed after failover" >&2
  exit 1
}

cmp --silent "$temporary_dir/pids.before" "$SYNCBASE_APP_PID_FILE" || {
  echo "application PID set changed during failover" >&2; exit 1;
}
while IFS= read -r pid; do
  if [[ ! "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "application process unavailable after failover" >&2; exit 1;
  fi
done <"$SYNCBASE_APP_PID_FILE"

processing_recovered="false"
processing_deadline=$((SECONDS + 120))
while (( SECONDS < processing_deadline )); do
  state="$(PGSERVICE="$SYNCBASE_PSQL_SERVICE" psql -X -A -t -v ON_ERROR_STOP=1 \
    -v run_id="$run_id" -v version_id="$version_id" <<'SQL'
SELECT CASE WHEN r.status = 'SUCCEEDED' AND v.status = 'ACTIVE'
    AND d.active_version_id = v.id
    AND (SELECT count(*) FROM syncbase.search_chunk c WHERE c.version_id = v.id) > 0
  THEN 'recovered' ELSE 'pending' END
FROM syncbase.processing_run r
JOIN syncbase.document_version v ON v.id = r.version_id
JOIN syncbase.document d ON d.id = v.document_id
WHERE r.id = :'run_id'::uuid AND v.id = :'version_id'::uuid;
SQL
)"
  if [[ "$state" == "recovered" ]]; then processing_recovered="true"; break; fi
  sleep 2
done
[[ "$processing_recovered" == "true" ]] || {
  echo "the in-flight processing run did not recover" >&2
  exit 1
}

snapshot_ids >"$temporary_dir/after.ids"
comm -23 "$temporary_dir/before.ids" "$temporary_dir/after.ids" >"$temporary_dir/lost.ids"
[[ ! -s "$temporary_dir/lost.ids" ]] || { echo "RPO loss detected" >&2; exit 1; }
probe || { echo "MCP search failed after recovery" >&2; exit 1; }

before_hash="$(hash_file "$temporary_dir/before.ids")"
after_hash="$(hash_file "$temporary_dir/after.ids")"
topology_before_hash="$(hash_file "$temporary_dir/topology-before.json")"
topology_after_hash="$(hash_file "$temporary_dir/topology-after.json")"
settings_hash="$(hash_file "$temporary_dir/synchronous-settings.txt")"
evidence_file="$SYNCBASE_EVIDENCE_DIR/failover-$(date -u +%Y%m%dT%H%M%SZ).json"
jq -n \
  --arg grade "actual-opensql" \
  --arg cluster "$SYNCBASE_FAILOVER_CLUSTER_ID" \
  --argjson started "$failover_started" \
  --argjson recovered "$recovered" \
  --argjson rto_ms "$rto_ms" \
  --arg before_hash "$before_hash" \
  --arg after_hash "$after_hash" \
  --arg topology_before_hash "$topology_before_hash" \
  --arg topology_after_hash "$topology_after_hash" \
  --arg settings_hash "$settings_hash" \
  '{grade:$grade,result:"PASS",environment:"ha-test",cluster_id:$cluster,
    failover_started_epoch_ms:$started,recovered_epoch_ms:$recovered,rto_ms:$rto_ms,
    rpo_lost_ids:0,application_restarts:0,processing_run_recovered:true,
    before_identity_sha256:$before_hash,after_identity_sha256:$after_hash,
    topology_before_sha256:$topology_before_hash,topology_after_sha256:$topology_after_hash,
    synchronous_settings_sha256:$settings_hash}' >"$evidence_file"
echo "OPENSQL_FAILOVER_PASS evidence=$evidence_file rto_ms=$rto_ms"

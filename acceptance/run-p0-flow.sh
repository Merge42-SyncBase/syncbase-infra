#!/usr/bin/env bash
set -euo pipefail

required=(
  SYNCBASE_WEB_URL
  SYNCBASE_SESSION_COOKIE_JAR
  SYNCBASE_SAMPLE_PDF
  SYNCBASE_SAMPLE_PDF_V2
  SYNCBASE_SAMPLE_DOCUMENT_NAME
  SYNCBASE_MCP_URL
  SYNCBASE_MCP_TOKEN_FILE
  SYNCBASE_SEARCH_QUERY
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done

for file in "$SYNCBASE_SESSION_COOKIE_JAR" "$SYNCBASE_SAMPLE_PDF" "$SYNCBASE_SAMPLE_PDF_V2" "$SYNCBASE_MCP_TOKEN_FILE"; do
  [[ -r "$file" ]] || { echo "unreadable required file: $file" >&2; exit 66; }
done

temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT
web_url="${SYNCBASE_WEB_URL%/}"
mcp_url="${SYNCBASE_MCP_URL%/}"
mcp_token="$(<"$SYNCBASE_MCP_TOKEN_FILE")"
request_body="$(jq -cn --arg query "$SYNCBASE_SEARCH_QUERY" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"search_documents",arguments:{query:$query,limit:20}}}')"

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

csrf_token() {
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    --header 'Accept: application/json' \
    "$web_url/api/v1/session" | jq -er '.csrfToken'
}

register_new_document() {
  local request_key="$1" csrf result
  csrf="$(csrf_token)"
  result="$(curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    --header 'Accept: application/json' --header "X-CSRF-Token: $csrf" \
    --form "documentName=$SYNCBASE_SAMPLE_DOCUMENT_NAME" \
    --form "requestKey=$request_key" \
    --form "file=@$SYNCBASE_SAMPLE_PDF;type=application/pdf" \
    "$web_url/api/v1/documents")"
  jq -er '.documentId | strings' <<<"$result"
}

register_new_version() {
  local document_id="$1" request_key="$2" csrf result
  csrf="$(csrf_token)"
  result="$(curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    --header 'Accept: application/json' --header "X-CSRF-Token: $csrf" \
    --form "requestKey=$request_key" \
    --form "file=@$SYNCBASE_SAMPLE_PDF_V2;type=application/pdf" \
    "$web_url/api/v1/documents/$document_id/versions")"
  jq -er --arg document "$document_id" '
    .documentId == $document and .version == 2 and (.documentUrl == "/documents/" + $document)
  ' >/dev/null <<<"$result"
}

call_mcp() {
  curl --fail --silent --show-error \
    --header "Authorization: Bearer $mcp_token" \
    --header 'Content-Type: application/json' \
    --header 'Accept: application/json, text/event-stream' \
    --data "$request_body" "$mcp_url/mcp"
}

wait_for_version() {
  local document_id="$1" version="$2" reject_version="${3:-}" deadline response hit
  deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    response="$(call_mcp)"
    hit="$(jq -c --arg document "$document_id" --argjson version "$version" '
      [.result.structuredContent.results[]? |
       select(.document_id == $document and .document_version == $version)][0] // empty
    ' <<<"$response")"
    if [[ -n "$hit" ]]; then
      if [[ -n "$reject_version" ]] && jq -e --arg document "$document_id" \
        --argjson rejected "$reject_version" '
          any(.result.structuredContent.results[]?;
              .document_id == $document and .document_version == $rejected)
        ' >/dev/null <<<"$response"; then
        echo "superseded version is still exposed by MCP" >&2
        exit 1
      fi
      jq -e '
        (.rank >= 1) and (.score >= 0 and .score <= 1) and
        (.version_id | strings) and (.page_number >= 1) and
        (.snippet | length > 0) and (.source_url | contains("/sources/"))
      ' >/dev/null <<<"$hit"
      printf '%s\n' "$hit"
      return 0
    fi
    sleep 2
  done
  echo "document version v$version did not become searchable within 120 seconds" >&2
  return 1
}

verify_source() {
  local document_id="$1" version="$2" hit="$3" expected_pdf="$4"
  local page source_url source raw
  source_url="$(jq -er '.source_url' <<<"$hit")"
  page="$(jq -er '.page_number' <<<"$hit")"
  [[ "$source_url" == *"/sources/$document_id/versions/$version?page=$page" ]] || {
    echo "source_url does not identify the expected version and page" >&2
    exit 1
  }
  source="$(curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    "$web_url/api/v1/documents/$document_id/versions/$version/source?page=$page")"
  jq -e --arg document "$document_id" --argjson expected_version "$version" --argjson expected_page "$page" '
    .documentId == $document and .version == $expected_version and .page == $expected_page and
    .sourceUrl == ("/sources/" + $document + "/versions/" + ($expected_version | tostring) + "?page=" + ($expected_page | tostring)) and
    (.rawPdfUrl | contains("/api/v1/documents/" + $document + "/versions/" + ($expected_version | tostring) + "/raw.pdf"))
  ' >/dev/null <<<"$source"
  raw="$temporary_dir/raw-v$version.pdf"
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    "$web_url/api/v1/documents/$document_id/versions/$version/raw.pdf?page=$page" >"$raw"
  [[ "$(hash_file "$raw")" == "$(hash_file "$expected_pdf")" ]] || {
    echo "raw source PDF differs from the registered version" >&2
    exit 1
  }
}

v1_request_key="p0-v1-$(openssl rand -hex 16)"
document_id="$(register_new_document "$v1_request_key")"
[[ "$document_id" =~ ^[0-9a-f-]{36}$ ]] || {
  echo "registration did not return a document ID" >&2
  exit 1
}
v1_hit="$(wait_for_version "$document_id" 1)"
verify_source "$document_id" 1 "$v1_hit" "$SYNCBASE_SAMPLE_PDF"

v2_request_key="p0-v2-$(openssl rand -hex 16)"
register_new_version "$document_id" "$v2_request_key"
v2_hit="$(wait_for_version "$document_id" 2 1)"
verify_source "$document_id" 2 "$v2_hit" "$SYNCBASE_SAMPLE_PDF_V2"

if [[ -n "${SYNCBASE_P0_DOCUMENT_ID_FILE:-}" ]]; then
  printf '%s\n' "$document_id" >"$SYNCBASE_P0_DOCUMENT_ID_FILE"
fi
echo "P0_FLOW_PASS document_id=$document_id versions=1,2 source_verified=true"

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

[[ -r "$SYNCBASE_SESSION_COOKIE_JAR" ]] || { echo "session cookie jar unreadable" >&2; exit 66; }
[[ -r "$SYNCBASE_SAMPLE_PDF" ]] || { echo "sample PDF unreadable" >&2; exit 66; }
[[ -r "$SYNCBASE_SAMPLE_PDF_V2" ]] || { echo "sample v2 PDF unreadable" >&2; exit 66; }
[[ -r "$SYNCBASE_MCP_TOKEN_FILE" ]] || { echo "MCP token file unreadable" >&2; exit 66; }

temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT
mcp_token="$(<"$SYNCBASE_MCP_TOKEN_FILE")"
request_body="$(jq -cn --arg query "$SYNCBASE_SEARCH_QUERY" '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"search_documents",arguments:{query:$query,limit:20}}}')"

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

csrf_from() {
  local route="$1" output="$2"
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    "$SYNCBASE_WEB_URL$route" >"$output"
  perl -ne 'if (/name="_csrf"[^>]*content="([^"]+)"/) { print $1; exit }' "$output"
}

location_from_headers() {
  local headers="$1" location
  location="$(awk 'tolower($1) == "location:" {gsub("\\r", "", $2); print $2}' \
    "$headers" | tail -n 1)"
  if [[ "$location" =~ ^https?://[^/]+(/.*)$ ]]; then
    location="${BASH_REMATCH[1]}"
  fi
  printf '%s\n' "$location"
}

register_new_document() {
  local request_key="$1" page="$temporary_dir/new-document.html" headers="$temporary_dir/v1-headers.txt"
  local csrf_token
  csrf_token="$(csrf_from "/documents/new" "$page")"
  [[ -n "$csrf_token" ]] || { echo "CSRF token not found; refresh the cookie jar" >&2; exit 1; }
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    --dump-header "$headers" --output /dev/null \
    --form "csrf=$csrf_token" \
    --form "documentName=$SYNCBASE_SAMPLE_DOCUMENT_NAME" \
    --form "requestKey=$request_key" \
    --form "file=@$SYNCBASE_SAMPLE_PDF;type=application/pdf" \
    "$SYNCBASE_WEB_URL/documents"
  location_from_headers "$headers"
}

register_new_version() {
  local document_id="$1" request_key="$2"
  local page="$temporary_dir/new-version.html" headers="$temporary_dir/v2-headers.txt" csrf_token
  csrf_token="$(csrf_from "/documents/$document_id/versions/new" "$page")"
  [[ -n "$csrf_token" ]] || { echo "new-version CSRF token not found" >&2; exit 1; }
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    --dump-header "$headers" --output /dev/null \
    --form "csrf=$csrf_token" \
    --form "requestKey=$request_key" \
    --form "file=@$SYNCBASE_SAMPLE_PDF_V2;type=application/pdf" \
    "$SYNCBASE_WEB_URL/documents/$document_id/versions"
  location_from_headers "$headers"
}

wait_for_version() {
  local document_id="$1" version="$2" reject_version="${3:-}" deadline response hit
  deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    response="$(curl --silent --show-error --header "Authorization: Bearer $mcp_token" \
      --header 'Content-Type: application/json' \
      --header 'Accept: application/json, text/event-stream' \
      --data "$request_body" "$SYNCBASE_MCP_URL/mcp")"
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
        (.page_number >= 1) and (.snippet | length > 0) and
        (.source_url | contains("/sources/"))
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
  local source_url page viewer="$temporary_dir/viewer-v$version.html" raw="$temporary_dir/raw-v$version.pdf"
  source_url="$(jq -r '.source_url' <<<"$hit")"
  page="$(jq -r '.page_number' <<<"$hit")"
  [[ "$source_url" == *"/sources/$document_id/versions/$version?page=$page" ]] || {
    echo "source_url does not identify the expected version and page" >&2
    exit 1
  }
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    "$source_url" >"$viewer"
  grep -F 'id="pdf-canvas"' "$viewer" >/dev/null
  grep -F '/vendor/pdfjs/pdf.mjs' "$viewer" >/dev/null
  grep -F "v$version · $page" "$viewer" >/dev/null
  grep -F "/sources/$document_id/versions/$version/raw.pdf" "$viewer" >/dev/null
  curl --fail --silent --show-error --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
    "$SYNCBASE_WEB_URL/sources/$document_id/versions/$version/raw.pdf" >"$raw"
  [[ "$(hash_file "$raw")" == "$(hash_file "$expected_pdf")" ]] || {
    echo "raw source PDF differs from the registered version" >&2
    exit 1
  }
}

v1_request_key="p0-v1-$(openssl rand -hex 16)"
v1_location="$(register_new_document "$v1_request_key")"
document_id="${v1_location#/documents/}"
document_id="${document_id%%\?*}"
[[ "$v1_location" == /documents/* && "$document_id" =~ ^[0-9a-f-]{36}$ ]] || {
  echo "registration did not return a document location" >&2
  exit 1
}
v1_hit="$(wait_for_version "$document_id" 1)"
verify_source "$document_id" 1 "$v1_hit" "$SYNCBASE_SAMPLE_PDF"

v2_request_key="p0-v2-$(openssl rand -hex 16)"
v2_location="$(register_new_version "$document_id" "$v2_request_key")"
[[ "$v2_location" == "/documents/$document_id" || "$v2_location" == "/documents/$document_id?"* ]] || {
  echo "new version did not return the original document location" >&2
  exit 1
}
v2_hit="$(wait_for_version "$document_id" 2 1)"
verify_source "$document_id" 2 "$v2_hit" "$SYNCBASE_SAMPLE_PDF_V2"

echo "P0_FLOW_PASS document_id=$document_id versions=1,2 source_verified=true"

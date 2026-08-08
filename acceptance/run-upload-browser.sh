#!/usr/bin/env bash
set -euo pipefail

required=(
  SYNCBASE_WEB_URL
  SYNCBASE_ADMIN_USERNAME
  SYNCBASE_ADMIN_PASSWORD
  SYNCBASE_SAMPLE_PDF
  SYNCBASE_SAMPLE_PDF_V2
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done
[[ -r "$SYNCBASE_SAMPLE_PDF" ]] || { echo "sample PDF unreadable" >&2; exit 66; }
[[ -r "$SYNCBASE_SAMPLE_PDF_V2" ]] || { echo "sample v2 PDF unreadable" >&2; exit 66; }
command -v npx >/dev/null 2>&1 || {
  echo "npx is required; install Node.js and npm" >&2
  exit 69
}

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_file="$project_root/frontend/test/upload-recovery.playwright.js"
output_dir="${SYNCBASE_PLAYWRIGHT_OUTPUT_DIR:-$project_root/output/playwright/upload-recovery-ci}"
version="0.1.18"
session="syncbase-upload-${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-1}"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/syncbase-upload-browser.XXXXXX")"
browser_open=false
cleanup() {
  if [[ "$browser_open" == true ]]; then
    playwright_cli close >/dev/null 2>&1 || true
  fi
  rm -rf "$temporary_dir"
}
trap cleanup EXIT
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
install -m 0644 "$SYNCBASE_SAMPLE_PDF" "$output_dir/sample-v1.pdf"
install -m 0644 "$SYNCBASE_SAMPLE_PDF_V2" "$output_dir/sample-v2.pdf"

web_url="${SYNCBASE_WEB_URL%/}"
cookie_jar="$temporary_dir/session-cookie"
storage_state="$temporary_dir/storage-state.json"
curl --fail --silent --show-error --location \
  --cookie-jar "$cookie_jar" \
  --data-urlencode "username=$SYNCBASE_ADMIN_USERNAME" \
  --data-urlencode "password=$SYNCBASE_ADMIN_PASSWORD" \
  "$web_url/login" >/dev/null
session_cookie="$(awk '$6 == "syncbase_session" {print $7}' "$cookie_jar" | tail -n 1)"
[[ -n "$session_cookie" ]] || { echo "browser test login did not create a session" >&2; exit 1; }
cookie_domain="${web_url#*://}"
cookie_domain="${cookie_domain%%/*}"
cookie_domain="${cookie_domain%%:*}"
cookie_secure=false
[[ "$web_url" == https://* ]] && cookie_secure=true
jq -n \
  --arg value "$session_cookie" \
  --arg domain "$cookie_domain" \
  --argjson secure "$cookie_secure" \
  '{cookies:[{name:"syncbase_session",value:$value,domain:$domain,path:"/",expires:-1,httpOnly:true,secure:$secure,sameSite:"Lax"}],origins:[]}' \
  >"$storage_state"

playwright_cli() {
  npx --yes --package "@playwright/cli@$version" playwright-cli -s="$session" "$@"
}

cd "$output_dir"
playwright_cli open "$web_url/login"
browser_open=true
playwright_cli state-load "$storage_state"
playwright_cli goto "$web_url/documents/new"
status=0
playwright_cli run-code --filename "$test_file" || status=$?
cleanup
trap - EXIT
if [[ "$status" -ne 0 ]]; then
  exit "$status"
fi

printf 'UPLOAD_BROWSER_PASS file_replacement=true pending_recovery=true\n'

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
for file in "$SYNCBASE_SAMPLE_PDF" "$SYNCBASE_SAMPLE_PDF_V2"; do
  [[ -r "$file" ]] || { echo "sample PDF unreadable: $file" >&2; exit 66; }
done
command -v npx >/dev/null 2>&1 || {
  echo "npx is required; install Node.js and npm" >&2
  exit 69
}

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${SYNCBASE_PLAYWRIGHT_OUTPUT_DIR:-$infra_root/output/playwright/upload-real-api}"
session="syncbase-upload-${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-1}"
pwcli="${CODEX_HOME:-$HOME/.codex}/skills/playwright/scripts/playwright_cli.sh"
storage_key='syncbase.upload./documents/new'
new_document_name="브라우저 실제 등록 $(date +%s)"

mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"

playwright_cli() {
  if [[ -x "$pwcli" ]]; then
    "$pwcli" -s="$session" "$@"
    return
  fi
  npx --yes --package '@playwright/cli@0.1.18' playwright-cli -s="$session" "$@"
}

wait_for_browser_expression() {
  local expression="$1"
  local description="$2"
  local deadline=$(( $(date +%s) + 120 ))
  while (( $(date +%s) < deadline )); do
    if [[ "$(playwright_cli --raw eval "$expression")" == "true" ]]; then
      return
    fi
    sleep 2
  done
  echo "timed out waiting for $description" >&2
  return 1
}

upload_file() {
  local file="$1"
  # The accessible projection identifies the nested input rather than its
  # label. Opening the real chooser explicitly preserves the browser's normal
  # change event; upload then supplies the selected local PDF to that chooser.
  playwright_cli eval "document.querySelector('input[type=file]').click()" > /dev/null
  playwright_cli upload "$file" > /dev/null
  wait_for_browser_expression "document.querySelector('.preflight') !== null" "PDF preflight"
}

read_upload_state() {
  local raw
  raw="$(playwright_cli --raw localstorage-get "$storage_key")"
  printf '%s' "${raw#*=}"
}

cleanup() {
  playwright_cli tracing-stop >/dev/null 2>&1 || true
  playwright_cli video-stop >/dev/null 2>&1 || true
  playwright_cli close >/dev/null 2>&1 || true
}
trap cleanup EXIT

playwright_cli open "$SYNCBASE_WEB_URL/login" > /dev/null
playwright_cli resize 1280 800 > /dev/null
playwright_cli video-start "$output_dir/upload-real-api.webm" --size 1280x800 > /dev/null
playwright_cli tracing-start > /dev/null

playwright_cli fill "input[autocomplete='username']" "$SYNCBASE_ADMIN_USERNAME" > /dev/null
playwright_cli fill "input[autocomplete='current-password']" "$SYNCBASE_ADMIN_PASSWORD" > /dev/null
playwright_cli click "button:has-text('로그인')" > /dev/null
wait_for_browser_expression "location.pathname === '/documents'" "document list after login"

playwright_cli click "a[href='/documents/new']" > /dev/null
wait_for_browser_expression "location.pathname === '/documents/new'" "PDF registration page"

upload_file "$SYNCBASE_SAMPLE_PDF"
first_state="$(read_upload_state)"
first_key="$(jq --raw-output '.requestKey' <<<"$first_state")"
first_hash="$(jq --raw-output '.hash' <<<"$first_state")"
[[ "$first_key" != "null" && "$first_hash" != "null" && "$(jq --raw-output '.submitted' <<<"$first_state")" == "false" ]] || {
  echo "first preflight did not persist a recoverable state" >&2
  exit 1
}

playwright_cli click "button:has-text('파일 교체')" > /dev/null
upload_file "$SYNCBASE_SAMPLE_PDF_V2"
replacement_state="$(read_upload_state)"
replacement_key="$(jq --raw-output '.requestKey' <<<"$replacement_state")"
replacement_hash="$(jq --raw-output '.hash' <<<"$replacement_state")"
[[ "$replacement_key" != "$first_key" && "$replacement_hash" != "$first_hash" &&
  "$(jq --raw-output '.submitted' <<<"$replacement_state")" == "false" ]] || {
  echo "replacement PDF did not rotate recoverable upload identity" >&2
  exit 1
}
playwright_cli screenshot --filename "$output_dir/upload-preflight-replacement.png" --full-page > /dev/null

playwright_cli click "button:has-text('파일 교체')" > /dev/null
upload_file "$SYNCBASE_SAMPLE_PDF"
playwright_cli fill "input[required]" "$new_document_name" > /dev/null
playwright_cli click "button:has-text('문서 등록')" > /dev/null
wait_for_browser_expression "location.pathname.startsWith('/documents/') && location.pathname !== '/documents/new'" "document detail after registration"
playwright_cli screenshot --filename "$output_dir/upload-registered-real-api.png" --full-page > /dev/null

wait_for_browser_expression "document.body.innerText.includes('검색 가능')" "active document version"
wait_for_browser_expression "document.body.innerText.includes('현재 검색 버전')" "published document version"
playwright_cli screenshot --filename "$output_dir/upload-active-real-api.png" --full-page > /dev/null

playwright_cli click "a:has-text('원문 열기')" > /dev/null
wait_for_browser_expression "location.pathname.startsWith('/sources/')" "source viewer route"
wait_for_browser_expression "document.querySelector('canvas')?.width > 0" "rendered source PDF"
wait_for_browser_expression "document.body.innerText.includes('검색 근거 위치')" "source provenance panel"
playwright_cli screenshot --filename "$output_dir/source-viewer-real-api.png" --full-page > /dev/null
playwright_cli tracing-stop > /dev/null
playwright_cli video-stop > /dev/null

printf 'UPLOAD_BROWSER_PASS real_api=true video=%s/upload-real-api.webm\n' "$output_dir"

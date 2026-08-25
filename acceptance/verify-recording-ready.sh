#!/usr/bin/env bash
set -euo pipefail

web_url="${SYNCBASE_WEB_URL:-http://127.0.0.1:8080}"
web_url="${web_url%/}"
response_file="$(mktemp)"
trap 'rm -f -- "$response_file"' EXIT

http_status="$(curl --silent --show-error \
  --connect-timeout 2 --max-time 5 \
  --output "$response_file" --write-out '%{http_code}' \
  "$web_url/readyz" || true)"

if [[ "$http_status" != "200" ]]; then
  printf 'CAPTURE_BLOCKED readyz_http_status=%s web_url=%s\n' "${http_status:-000}" "$web_url" >&2
  exit 1
fi

if ! jq -e '.status == "ready"' "$response_file" >/dev/null 2>&1; then
  printf 'CAPTURE_BLOCKED readyz_payload_not_ready web_url=%s\n' "$web_url" >&2
  exit 1
fi

printf 'CAPTURE_READY readyz_http_status=200 readyz_payload_status=ready web_url=%s\n' "$web_url"

#!/usr/bin/env bash
set -euo pipefail

required=(SYNCBASE_WEB_URL SYNCBASE_SESSION_COOKIE_JAR)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done
[[ -r "$SYNCBASE_SESSION_COOKIE_JAR" ]] || { echo "unreadable session cookie jar" >&2; exit 66; }

docker compose restart api
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
    "$SYNCBASE_WEB_URL/readyz" >/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    docker compose logs --no-color api worker >&2 || true
    echo "API did not become ready after restart" >&2
    exit 1
  fi
  sleep 2
done

response="$(curl --fail --silent --show-error \
  --cookie "$SYNCBASE_SESSION_COOKIE_JAR" \
  "$SYNCBASE_WEB_URL/api/v1/session")"
jq -e '.user.role == "DOCUMENT_ADMIN" and (.csrfToken | type == "string" and length >= 32)' \
  >/dev/null <<<"$response"
printf 'SESSION_RESTART_PASS\n'

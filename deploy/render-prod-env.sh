#!/usr/bin/env bash
set -euo pipefail

output_file="${1:?usage: render-prod-env.sh OUTPUT_FILE MCP_TOKEN_FILE}"
mcp_token_file="${2:?usage: render-prod-env.sh OUTPUT_FILE MCP_TOKEN_FILE}"

required=(
  SYNCBASE_PROD_WEB_IMAGE
  SYNCBASE_PROD_API_IMAGE
  SYNCBASE_PROD_WORKER_IMAGE
  SYNCBASE_PROD_MIGRATE_IMAGE
  SYNCBASE_PROD_MCP_IMAGE
  SYNCBASE_PROD_POSTGRES_OWNER_PASSWORD
  SYNCBASE_PROD_WEB_DB_PASSWORD
  SYNCBASE_PROD_WORKER_DB_PASSWORD
  SYNCBASE_PROD_MCP_DB_PASSWORD
  SYNCBASE_PROD_ADMIN_USERNAME
  SYNCBASE_PROD_ADMIN_PASSWORD_BCRYPT
  SYNCBASE_PROD_MCP_TOKEN
  SYNCBASE_PROD_MCP_ALLOWED_HOSTS
  SYNCBASE_PROD_PUBLIC_BASE_URL
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done

case "$SYNCBASE_PROD_PUBLIC_BASE_URL" in
  https://*) ;;
  *) echo "SYNCBASE_PROD_PUBLIC_BASE_URL must use https" >&2; exit 64 ;;
esac

write_literal() {
  local name="$1" value="$2"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* || "$value" == *"'"* ]]; then
    echo "$name contains a character that cannot be represented safely" >&2
    exit 64
  fi
  printf "%s='%s'\n" "$name" "$value"
}

output_dir="$(dirname "$output_file")"
token_dir="$(dirname "$mcp_token_file")"
mkdir -p "$output_dir" "$token_dir"
umask 077
printf '%s\n' "$SYNCBASE_PROD_MCP_TOKEN" >"$mcp_token_file"
if command -v sha256sum >/dev/null 2>&1; then
  mcp_token_sha256="$(printf '%s' "$SYNCBASE_PROD_MCP_TOKEN" | sha256sum | awk '{print $1}')"
else
  mcp_token_sha256="$(printf '%s' "$SYNCBASE_PROD_MCP_TOKEN" | shasum -a 256 | awk '{print $1}')"
fi

{
  write_literal SYNCBASE_WEB_IMAGE "$SYNCBASE_PROD_WEB_IMAGE"
  write_literal SYNCBASE_API_IMAGE "$SYNCBASE_PROD_API_IMAGE"
  write_literal SYNCBASE_WORKER_IMAGE "$SYNCBASE_PROD_WORKER_IMAGE"
  write_literal SYNCBASE_MIGRATE_IMAGE "$SYNCBASE_PROD_MIGRATE_IMAGE"
  write_literal SYNCBASE_MCP_IMAGE "$SYNCBASE_PROD_MCP_IMAGE"
  printf '\n'
  write_literal SYNCBASE_POSTGRES_OWNER_PASSWORD "$SYNCBASE_PROD_POSTGRES_OWNER_PASSWORD"
  write_literal SYNCBASE_WEB_DB_PASSWORD "$SYNCBASE_PROD_WEB_DB_PASSWORD"
  write_literal SYNCBASE_WORKER_DB_PASSWORD "$SYNCBASE_PROD_WORKER_DB_PASSWORD"
  write_literal SYNCBASE_MCP_DB_PASSWORD "$SYNCBASE_PROD_MCP_DB_PASSWORD"
  write_literal SYNCBASE_ADMIN_USERNAME "$SYNCBASE_PROD_ADMIN_USERNAME"
  write_literal SYNCBASE_ADMIN_PASSWORD_BCRYPT "$SYNCBASE_PROD_ADMIN_PASSWORD_BCRYPT"
  printf '\n'
  write_literal SYNCBASE_MCP_TOKEN_FILE './environments/prod/secrets/mcp-token'
  write_literal SYNCBASE_MCP_TOKEN_SHA256 "$mcp_token_sha256"
  write_literal SYNCBASE_MCP_ALLOWED_HOSTS "$SYNCBASE_PROD_MCP_ALLOWED_HOSTS"
  write_literal SYNCBASE_MCP_ALLOWED_ORIGINS "${SYNCBASE_PROD_MCP_ALLOWED_ORIGINS:-}"
  write_literal SYNCBASE_PUBLIC_BASE_URL "$SYNCBASE_PROD_PUBLIC_BASE_URL"
  printf '\n'
  write_literal SYNCBASE_WEB_BIND_ADDRESS "${SYNCBASE_PROD_WEB_BIND_ADDRESS:-127.0.0.1}"
  write_literal SYNCBASE_WEB_PORT "${SYNCBASE_PROD_WEB_PORT:-8080}"
  write_literal SYNCBASE_MCP_BIND_ADDRESS "${SYNCBASE_PROD_MCP_BIND_ADDRESS:-127.0.0.1}"
  write_literal SYNCBASE_MCP_PORT "${SYNCBASE_PROD_MCP_PORT:-8081}"
  write_literal SYNCBASE_ORT_LIBRARY_FILE "${SYNCBASE_PROD_ORT_LIBRARY_FILE:-libonnxruntime.so.1.26.0}"
} >"$output_file"

chmod 0600 "$output_file" "$mcp_token_file"
printf 'PROD_ENV_RENDER_PASS output=%s token=%s\n' "$output_file" "$mcp_token_file"

#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <client-token-file> <server-digest-file>" >&2
  exit 64
fi
for target in "$1" "$2"; do
  [[ "$target" = /* ]] || { echo "output paths must be absolute" >&2; exit 64; }
  [[ ! -e "$target" ]] || { echo "refusing to overwrite $target" >&2; exit 73; }
  mkdir -p "$(dirname "$target")"
done

payload="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"
token="sb_mcp_v1_$payload"
if command -v sha256sum >/dev/null 2>&1; then
  digest="$(printf '%s' "$token" | sha256sum | awk '{print $1}')"
else
  digest="$(printf '%s' "$token" | shasum -a 256 | awk '{print $1}')"
fi

token_temporary="$(mktemp "$(dirname "$1")/.mcp-token.XXXXXX")"
digest_temporary="$(mktemp "$(dirname "$2")/.mcp-digest.XXXXXX")"
cleanup() { rm -f "$token_temporary" "$digest_temporary"; }
trap cleanup EXIT
printf '%s\n' "$token" >"$token_temporary"
printf '%s\n' "$digest" >"$digest_temporary"
mv "$token_temporary" "$1"
mv "$digest_temporary" "$2"
echo "MCP credentials created; distribute the token only to the client and the digest only to the server."

#!/usr/bin/env bash
set -euo pipefail

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_root="${SYNCBASE_WORKSPACE_ROOT:-$(cd "$infra_root/.." && pwd)}"
mcp_root="$workspace_root/syncbase-mcp"
was_root="$workspace_root/syncbase-was"
frontend_root="$workspace_root/SyncBase-FE"
embedding_root="$workspace_root/syncbase-embedding"

python3 "$infra_root/quality/verify_repositories.py" \
  --workspace-root "$workspace_root" --allow-dirty >/dev/null

if rg -n 'github.com/yeomin4242/syncbase-backend' \
  "$mcp_root" "$was_root" "$embedding_root" --glob '*.go' --glob 'go.mod'; then
  echo "legacy Go module import remains" >&2
  exit 1
fi

if rg -n 'github.com/Merge42-SyncBase/syncbase-was' \
  "$frontend_root" "$embedding_root" --glob '*.go' --glob 'go.mod'; then
  echo "frontend or embedding depends on WAS" >&2
  exit 1
fi

if rg -n 'github.com/Merge42-SyncBase/syncbase-was/internal/' \
  "$mcp_root" --glob '*.go' --glob 'go.mod'; then
  echo "MCP imports WAS internals" >&2
  exit 1
fi

unexpected_was_imports="$(rg -n 'github.com/Merge42-SyncBase/syncbase-was/' \
  "$mcp_root" --glob '*.go' | rg -v '/searchruntime"' || true)"
if [[ -n "$unexpected_was_imports" ]]; then
  printf '%s\n' "$unexpected_was_imports" >&2
  echo "MCP may depend only on the WAS searchruntime facade" >&2
  exit 1
fi

printf 'BOUNDARY_CHECK_PASS modules=5 mcp_facade=searchruntime\n'

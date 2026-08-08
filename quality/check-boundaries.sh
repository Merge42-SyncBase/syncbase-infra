#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_root"

for module_dir in mcp was frontend vector-embedding infra; do
  [[ -d "$module_dir" ]] || { echo "missing module directory: $module_dir" >&2; exit 1; }
done

if rg -n 'github.com/yeomin4242/syncbase-backend' \
  mcp was frontend vector-embedding --glob '*.go' --glob 'go.mod'; then
  echo "legacy Go module import remains" >&2
  exit 1
fi

if rg -n 'github.com/Merge42-SyncBase/syncbase-was' \
  frontend vector-embedding --glob '*.go' --glob 'go.mod'; then
  echo "frontend or embedding depends on WAS" >&2
  exit 1
fi

if rg -n 'github.com/Merge42-SyncBase/syncbase-was/internal/' \
  mcp --glob '*.go' --glob 'go.mod'; then
  echo "MCP imports WAS internals" >&2
  exit 1
fi

unexpected_was_imports="$(rg -n 'github.com/Merge42-SyncBase/syncbase-was/' \
  mcp --glob '*.go' | rg -v '/searchruntime"' || true)"
if [[ -n "$unexpected_was_imports" ]]; then
  printf '%s\n' "$unexpected_was_imports" >&2
  echo "MCP may depend only on the WAS searchruntime facade" >&2
  exit 1
fi

printf 'BOUNDARY_CHECK_PASS modules=5 mcp_facade=searchruntime\n'

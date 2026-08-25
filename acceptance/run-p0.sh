#!/usr/bin/env bash
set -euo pipefail

[[ -n "${SYNCBASE_E5_MODEL_DIR:-}" ]] || { echo "missing SYNCBASE_E5_MODEL_DIR" >&2; exit 64; }
[[ -n "${SYNCBASE_ORT_LIBRARY_PATH:-}" ]] || { echo "missing SYNCBASE_ORT_LIBRARY_PATH" >&2; exit 64; }
[[ -n "${SYNCBASE_TEST_DB_URL:-}" ]] || { echo "missing SYNCBASE_TEST_DB_URL" >&2; exit 64; }
infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace_root="${SYNCBASE_WORKSPACE_ROOT:-$(cd "$infra_root/.." && pwd)}"
frontend_root="$workspace_root/SyncBase-FE"
embedding_root="$workspace_root/syncbase-embedding"
was_root="$workspace_root/syncbase-was"
mcp_root="$workspace_root/syncbase-mcp"
run_id="${SYNCBASE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
repository_evidence="${SYNCBASE_REPOSITORY_EVIDENCE_PATH:-}"
temporary_repository_evidence=""
if [[ -z "$repository_evidence" ]]; then
  temporary_repository_evidence="$(mktemp)"
  repository_evidence="$temporary_repository_evidence"
  trap 'rm -f "$temporary_repository_evidence"' EXIT
fi
repository_arguments=(
  --workspace-root "$workspace_root"
  --run-id "$run_id"
  --output "$repository_evidence"
)
if [[ "${SYNCBASE_ALLOW_DIRTY_REPOSITORIES:-false}" == "true" ]]; then
  repository_arguments+=(--allow-dirty)
fi
python3 "$infra_root/quality/verify_repositories.py" "${repository_arguments[@]}"

"$embedding_root/ops/model/fetch-e5-small.sh" "$SYNCBASE_E5_MODEL_DIR"
export SYNCBASE_TEST_E5_MODEL_PATH="$SYNCBASE_E5_MODEL_DIR/model.onnx"
export SYNCBASE_TEST_E5_TOKENIZER_PATH="$SYNCBASE_E5_MODEL_DIR/tokenizer.json"
export SYNCBASE_TEST_ORT_LIBRARY_PATH="$SYNCBASE_ORT_LIBRARY_PATH"

go -C "$was_root" test ./... -count=1
go -C "$mcp_root" test ./... -count=1
go -C "$embedding_root" test ./... -count=1
npm --prefix "$frontend_root" ci
npm --prefix "$frontend_root" run check
go -C "$was_root/qualification/pdf-gate/go" test ./... -count=1
go -C "$was_root" vet ./...
go -C "$mcp_root" vet ./...
go -C "$embedding_root" vet ./...
go -C "$was_root/qualification/pdf-gate/go" vet ./...
go -C "$was_root" test -race ./... -count=1
go -C "$embedding_root" test -v ./... -count=1
"$infra_root/acceptance/run-p0-flow.sh"
"$infra_root/acceptance/run-upload-browser.sh"
if [[ "${SYNCBASE_RUN_OPENSQL_FAILOVER:-false}" == "true" ]]; then
  "$infra_root/acceptance/run-failover.sh"
else
  echo "OPENSQL_FAILOVER_BLOCKED reason=SYNCBASE_RUN_OPENSQL_FAILOVER_not_enabled"
fi

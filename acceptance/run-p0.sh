#!/usr/bin/env bash
set -euo pipefail

[[ -n "${SYNCBASE_E5_MODEL_DIR:-}" ]] || { echo "missing SYNCBASE_E5_MODEL_DIR" >&2; exit 64; }
[[ -n "${SYNCBASE_ORT_LIBRARY_PATH:-}" ]] || { echo "missing SYNCBASE_ORT_LIBRARY_PATH" >&2; exit 64; }
[[ -n "${SYNCBASE_TEST_DB_URL:-}" ]] || { echo "missing SYNCBASE_TEST_DB_URL" >&2; exit 64; }
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
"$project_root/vector-embedding/ops/model/fetch-e5-small.sh" "$SYNCBASE_E5_MODEL_DIR"
export SYNCBASE_TEST_E5_MODEL_PATH="$SYNCBASE_E5_MODEL_DIR/model.onnx"
export SYNCBASE_TEST_E5_TOKENIZER_PATH="$SYNCBASE_E5_MODEL_DIR/tokenizer.json"
export SYNCBASE_TEST_ORT_LIBRARY_PATH="$SYNCBASE_ORT_LIBRARY_PATH"

go -C "$project_root/was" test ./... -count=1
go -C "$project_root/mcp" test ./... -count=1
go -C "$project_root/vector-embedding" test ./... -count=1
npm --prefix "$project_root/frontend" ci
npm --prefix "$project_root/frontend" run check
go -C "$project_root/was/qualification/pdf-gate/go" test ./... -count=1
go -C "$project_root/was" vet ./...
go -C "$project_root/mcp" vet ./...
go -C "$project_root/vector-embedding" vet ./...
go -C "$project_root/was/qualification/pdf-gate/go" vet ./...
go -C "$project_root/was" test -race ./... -count=1
go -C "$project_root/vector-embedding" test -v ./... -count=1
"$project_root/infra/acceptance/run-p0-flow.sh"
"$project_root/infra/acceptance/run-upload-browser.sh"
if [[ "${SYNCBASE_RUN_OPENSQL_FAILOVER:-false}" == "true" ]]; then
  "$project_root/infra/acceptance/run-failover.sh"
else
  echo "OPENSQL_FAILOVER_BLOCKED reason=SYNCBASE_RUN_OPENSQL_FAILOVER_not_enabled"
fi

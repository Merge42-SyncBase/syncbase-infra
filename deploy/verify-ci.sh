#!/usr/bin/env bash
set -euo pipefail

required=(GITHUB_REPOSITORY GITHUB_SHA GH_TOKEN)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "missing $name" >&2; exit 64; }
done

for workflow in platform-ci p0-acceptance; do
  runs="$(gh api "/repos/$GITHUB_REPOSITORY/actions/runs?head_sha=$GITHUB_SHA&status=completed&per_page=100")"
  if ! jq -e --arg workflow "$workflow" --arg sha "$GITHUB_SHA" '
    any(.workflow_runs[]; .name == $workflow and .head_sha == $sha and .conclusion == "success")
  ' >/dev/null <<<"$runs"; then
    echo "required workflow has not succeeded for commit: $workflow $GITHUB_SHA" >&2
    exit 1
  fi
done

printf 'RELEASE_CI_GATE_PASS sha=%s\n' "$GITHUB_SHA"

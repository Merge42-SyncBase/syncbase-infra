#!/usr/bin/env bash
set -euo pipefail

umask 077

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
infra_root="$(cd "$script_dir/.." && pwd -P)"
workspace_root="${1:-$(cd "$infra_root/.." && pwd -P)}"
report_root="${2:-}"
config_file="$script_dir/gitleaks.toml"
protected_policy_file="$script_dir/protected-material-policy.json"

if [[ -z "$report_root" ]]; then
  echo "usage: $0 [workspace-root] /absolute/new-report-directory" >&2
  exit 64
fi
if [[ "$report_root" != /* ]]; then
  echo "report directory must be absolute" >&2
  exit 64
fi
if [[ ! -d "$workspace_root" ]]; then
  echo "workspace root does not exist" >&2
  exit 66
fi
workspace_root="$(cd "$workspace_root" && pwd -P)"
if [[ -e "$report_root" || -L "$report_root" ]]; then
  echo "refusing to overwrite an existing report directory" >&2
  exit 73
fi
report_parent="$(dirname "$report_root")"
if [[ ! -d "$report_parent" ]]; then
  echo "report parent directory does not exist" >&2
  exit 66
fi
report_parent="$(cd "$report_parent" && pwd -P)"
report_name="$(basename "$report_root")"
if [[ "$report_name" == "." || "$report_name" == ".." || -z "$report_name" ]]; then
  echo "invalid report directory name" >&2
  exit 64
fi
report_root="$report_parent/$report_name"
case "$report_root" in
  "$workspace_root"|"$workspace_root"/*)
    echo "report directory must be outside the scanned workspace" >&2
    exit 64
    ;;
esac

for required_command in git gitleaks jq mktemp stat cp find awk wc tr date dirname basename rm; do
  command -v "$required_command" >/dev/null 2>&1 || {
    echo "missing required command: $required_command" >&2
    exit 69
  }
done
if command -v sha256sum >/dev/null 2>&1; then
  hash_backend="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  hash_backend="shasum"
else
  echo "missing required SHA-256 command: sha256sum or shasum" >&2
  exit 69
fi
[[ -f "$config_file" ]] || { echo "missing gitleaks config" >&2; exit 66; }
[[ -f "$protected_policy_file" ]] || {
  echo "missing protected-material policy" >&2
  exit 66
}
jq -e '
  .repository == "syncbase-infra" and
  (.protected_paths | type == "array" and length > 0) and
  all(.protected_paths[];
    (.path | type == "string" and length > 0) and
    (.path | startswith("/") | not) and
    (.path | test("(^|/)\\.\\.(/|$)") | not) and
    .type == "regular_file" and
    (.mode | test("^0[0-7]{3}$")) and
    .required == true
  ) and
  ([.protected_paths[].path] | length == (unique | length)) and
  (.protected_roots | type == "array" and length > 0) and
  all(.protected_roots[];
    type == "string" and length > 0 and
    (startswith("/") | not) and
    (test("(^|/)\\.\\.(/|$)") | not)
  ) and
  (.protected_roots | length == (unique | length)) and
  (.forbidden_paths | type == "array") and
  all(.forbidden_paths[];
    type == "string" and length > 0 and
    (startswith("/") | not) and
    (test("(^|/)\\.\\.(/|$)") | not)
  ) and
  (.forbidden_paths | length == (unique | length))
' "$protected_policy_file" >/dev/null || {
  echo "invalid protected-material policy" >&2
  exit 65
}

repository_specs=(
  "frontend:SyncBase-FE"
  "embedding:syncbase-embedding"
  "was:syncbase-was"
  "infra:syncbase-infra"
  "mcp:syncbase-mcp"
)

hash_file() {
  if [[ "$hash_backend" == "sha256sum" ]]; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

file_mode() {
  local target_file="$1"
  local raw_mode
  raw_mode="$(stat -c '%a' "$target_file" 2>/dev/null || stat -f '%Lp' "$target_file")"
  printf '%04d' "$raw_mode"
}

ensure_json_array_report() {
  local report_file="$1"
  if [[ ! -s "$report_file" ]] || ! jq -e 'type == "array"' "$report_file" >/dev/null 2>&1; then
    printf '[]\n' > "$report_file"
    return 1
  fi
  return 0
}

run_gitleaks_git() {
  local repository_root="$1"
  local output_file="$2"
  (
    cd "$repository_root" || exit 2
    gitleaks git --no-banner --no-color --redact=100 --log-level error \
      --log-opts="--all --full-history" \
      --config "$config_file" --report-format json --report-path "$output_file" \
      . >/dev/null 2>&1
  )
}

run_gitleaks_dir() {
  local directory_root="$1"
  local output_file="$2"
  (
    cd "$directory_root" || exit 2
    gitleaks dir --no-banner --no-color --redact=100 --log-level error \
      --config "$config_file" --report-format json --report-path "$output_file" \
      . >/dev/null 2>&1
  )
}

copy_public_source_set() {
  local repository_root="$1"
  local snapshot_root="$2"
  local member_jsonl="$3"
  local error_jsonl="$4"
  local source_list="$5"
  local relative_path source_file destination_file content_sha mode

  mkdir -p "$snapshot_root"
  : > "$member_jsonl"
  : > "$error_jsonl"
  if ! git -C "$repository_root" ls-files --cached --others --exclude-standard -z \
    > "$source_list"; then
    jq -nc '{error: "GIT_LS_FILES_FAILED"}' >> "$error_jsonl"
    return 1
  fi

  while IFS= read -r -d '' relative_path; do
    case "$relative_path" in
      ""|/*|..|../*|*/..|*/../*)
        jq -nc --arg path "$relative_path" \
          '{path: $path, error: "UNSAFE_SOURCE_PATH"}' >> "$error_jsonl"
        continue
        ;;
    esac

    source_file="$repository_root/$relative_path"
    destination_file="$snapshot_root/$relative_path"
    if [[ -L "$source_file" ]]; then
      jq -nc --arg path "$relative_path" \
        '{path: $path, error: "SYMLINK_NOT_SUPPORTED"}' >> "$error_jsonl"
      continue
    fi
    if [[ -d "$source_file" ]]; then
      jq -nc --arg path "$relative_path" \
        '{path: $path, error: "GITLINK_OR_DIRECTORY_NOT_SUPPORTED"}' \
        >> "$error_jsonl"
      continue
    fi
    if [[ ! -f "$source_file" ]]; then
      # A tracked file deleted in the worktree is intentionally absent from the
      # candidate source set. Other unsupported file types fail closed.
      if [[ ! -e "$source_file" ]]; then
        continue
      fi
      jq -nc --arg path "$relative_path" \
        '{path: $path, error: "UNSUPPORTED_FILE_TYPE"}' >> "$error_jsonl"
      continue
    fi

    mkdir -p "$(dirname "$destination_file")"
    if ! cp -p "$source_file" "$destination_file"; then
      jq -nc --arg path "$relative_path" \
        '{path: $path, error: "COPY_FAILED"}' >> "$error_jsonl"
      continue
    fi
    content_sha="$(hash_file "$source_file")"
    mode="$(file_mode "$source_file")"
    jq -nc --arg path "$relative_path" --arg sha256 "$content_sha" \
      --arg mode "$mode" '{path: $path, sha256: $sha256, mode: $mode}' \
      >> "$member_jsonl"
  done < "$source_list"

  [[ "$(wc -l < "$error_jsonl" | tr -d ' ')" -eq 0 ]]
}

if ! mkdir "$report_root"; then
  echo "failed to create new report directory" >&2
  exit 73
fi
scratch_root="$(mktemp -d /tmp/syncbase-release-secret-scan.XXXXXX)" || exit 70
cleanup() {
  case "$scratch_root" in
    /tmp/syncbase-release-secret-scan.*)
      rm -rf -- "$scratch_root"
      ;;
  esac
}
trap cleanup EXIT

generated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
scanner_version="$(gitleaks version)"

public_report_root="$report_root/public-source"
mkdir -p "$public_report_root"
public_repository_results="$public_report_root/repositories.jsonl"
: > "$public_repository_results"
public_overall_result="PASS"

for repository_spec in "${repository_specs[@]}"; do
  repository_id="${repository_spec%%:*}"
  repository_directory="${repository_spec#*:}"
  repository_root="$workspace_root/$repository_directory"
  repository_report="$public_report_root/$repository_id"
  snapshot_root="$scratch_root/public-source/$repository_id"
  source_list="$scratch_root/$repository_id.public-source.nul"
  mkdir -p "$repository_report"

  if [[ ! -d "$repository_root/.git" ]]; then
    jq -nc --arg id "$repository_id" \
      '{id: $id, result: "FAIL", reason: "MISSING_REPOSITORY"}' \
      >> "$public_repository_results"
    public_overall_result="FAIL"
    continue
  fi

  repository_revision="$(git -C "$repository_root" rev-parse HEAD)"
  repository_shallow="$(git -C "$repository_root" rev-parse --is-shallow-repository)"
  member_jsonl="$repository_report/archive-members.jsonl"
  member_json="$repository_report/archive-members.json"
  error_jsonl="$repository_report/archive-errors.jsonl"
  error_json="$repository_report/archive-errors.json"

  copy_exit=0
  if copy_public_source_set "$repository_root" "$snapshot_root" "$member_jsonl" \
    "$error_jsonl" "$source_list"; then
    :
  else
    copy_exit=$?
  fi
  jq -s '.' "$member_jsonl" > "$member_json"
  jq -s '.' "$error_jsonl" > "$error_json"
  public_file_count="$(jq 'length' "$member_json")"
  snapshot_error_count="$(jq 'length' "$error_json")"
  source_set_sha256="$(hash_file "$member_json")"

  history_report="$repository_report/history.redacted.json"
  archive_report="$repository_report/archive.redacted.json"
  history_exit=0
  if run_gitleaks_git "$repository_root" "$history_report"; then :; else history_exit=$?; fi
  history_report_valid=0
  if ensure_json_array_report "$history_report"; then :; else history_report_valid=$?; fi
  archive_exit=0
  if run_gitleaks_dir "$snapshot_root" "$archive_report"; then :; else archive_exit=$?; fi
  archive_report_valid=0
  if ensure_json_array_report "$archive_report"; then :; else archive_report_valid=$?; fi

  history_count="$(jq 'length' "$history_report")"
  archive_count="$(jq 'length' "$archive_report")"
  jq '[.[] | {
    rule_id: .RuleID,
    file: .File,
    start_line: .StartLine,
    commit: .Commit
  }]' "$history_report" > "$repository_report/history.summary.json"
  jq '[.[] | {
    rule_id: .RuleID,
    file: .File,
    start_line: .StartLine
  }]' "$archive_report" > "$repository_report/archive.summary.json"

  repository_result="PASS"
  if [[ "$repository_shallow" == "true" ]]; then
    repository_result="ERROR"
    public_overall_result="FAIL"
  elif [[ "$copy_exit" -ne 0 || "$snapshot_error_count" -ne 0 || \
        "$history_exit" -gt 1 || "$archive_exit" -gt 1 || \
        "$history_report_valid" -ne 0 || "$archive_report_valid" -ne 0 ]]; then
    repository_result="ERROR"
    public_overall_result="FAIL"
  elif [[ "$history_count" -ne 0 || "$archive_count" -ne 0 ]]; then
    repository_result="FAIL"
    public_overall_result="FAIL"
  fi

  jq -nc \
    --arg id "$repository_id" \
    --arg revision "$repository_revision" \
    --argjson shallow "$repository_shallow" \
    --arg result "$repository_result" \
    --arg source_set_sha256 "$source_set_sha256" \
    --argjson public_file_count "$public_file_count" \
    --argjson snapshot_error_count "$snapshot_error_count" \
    --argjson history_count "$history_count" \
    --argjson archive_count "$archive_count" \
    '{
      id: $id,
      revision: $revision,
      shallow_repository: $shallow,
      result: $result,
      source_set: "TRACKED_AND_UNTRACKED_NONIGNORED",
      source_set_sha256: $source_set_sha256,
      public_file_count: $public_file_count,
      snapshot_error_count: $snapshot_error_count,
      history_findings: $history_count,
      archive_findings: $archive_count
    }' >> "$public_repository_results"
  printf 'PUBLIC_SOURCE_SCAN repo=%s result=%s history_findings=%s archive_findings=%s files=%s\n' \
    "$repository_id" "$repository_result" "$history_count" "$archive_count" \
    "$public_file_count"
done

jq -s \
  --arg schema_version "2.0" \
  --arg generated_at "$generated_at" \
  --arg scanner "gitleaks $scanner_version" \
  --arg overall_result "$public_overall_result" \
  '{
    schema_version: $schema_version,
    generated_at: $generated_at,
    scanner: $scanner,
    scope: "ALL_LOCAL_REFS_NONSHALLOW_HISTORY_PLUS_TRACKED_AND_UNTRACKED_NONIGNORED_WORKTREE",
    ignored_material_included: false,
    overall_result: $overall_result,
    repositories: .
  }' "$public_repository_results" > "$public_report_root/result.json"

protected_report_root="$report_root/protected-material"
mkdir -p "$protected_report_root"
protected_items_jsonl="$protected_report_root/items.jsonl"
forbidden_items_jsonl="$protected_report_root/forbidden-items.jsonl"
observed_paths_jsonl="$protected_report_root/observed-paths.jsonl"
: > "$protected_items_jsonl"
: > "$forbidden_items_jsonl"
: > "$observed_paths_jsonl"

infra_repository_root="$workspace_root/syncbase-infra"
infra_public_members="$public_report_root/infra/archive-members.json"
infra_revision="$(git -C "$infra_repository_root" rev-parse HEAD)"
protected_policy_result="PASS"

while IFS=$'\t' read -r relative_path expected_mode; do
  target_file="$infra_repository_root/$relative_path"
  present=false
  regular_file=false
  symlink=false
  ignored=false
  repository_ignore_rule=false
  tracked=false
  outside_public_source=true
  actual_mode=""
  item_result="FAIL"

  if [[ -e "$target_file" || -L "$target_file" ]]; then
    present=true
  fi
  if [[ -f "$target_file" && ! -L "$target_file" ]]; then
    regular_file=true
    actual_mode="$(file_mode "$target_file")"
  fi
  if [[ -L "$target_file" ]]; then
    symlink=true
  fi
  ignore_detail="$(git -C "$infra_repository_root" check-ignore -v -- "$relative_path" 2>/dev/null || true)"
  if [[ -n "$ignore_detail" ]]; then
    ignored=true
    ignore_source="${ignore_detail%%:*}"
    if [[ "$ignore_source" == ".gitignore" ]]; then
      repository_ignore_rule=true
    fi
  fi
  if git -C "$infra_repository_root" ls-files --error-unmatch -- "$relative_path" \
    >/dev/null 2>&1; then
    tracked=true
  fi
  if jq -e --arg path "$relative_path" 'any(.[]; .path == $path)' \
    "$infra_public_members" >/dev/null; then
    outside_public_source=false
  fi

  if [[ "$present" == true && "$regular_file" == true && "$symlink" == false && \
        "$actual_mode" == "$expected_mode" && "$ignored" == true && \
        "$repository_ignore_rule" == true && "$tracked" == false && \
        "$outside_public_source" == true ]]; then
    item_result="PASS"
  else
    protected_policy_result="FAIL"
  fi

  jq -nc \
    --arg path "$relative_path" \
    --arg expected_mode "$expected_mode" \
    --arg actual_mode "$actual_mode" \
    --arg item_result "$item_result" \
    --argjson present "$present" \
    --argjson regular_file "$regular_file" \
    --argjson symlink "$symlink" \
    --argjson ignored "$ignored" \
    --argjson repository_ignore_rule "$repository_ignore_rule" \
    --argjson tracked "$tracked" \
    --argjson outside_public_source "$outside_public_source" \
    '{
      path: $path,
      state: (if $present then "PRESENT_PROTECTED" else "MISSING" end),
      expected_type: "regular_file",
      regular_file: $regular_file,
      symlink: $symlink,
      expected_mode: $expected_mode,
      actual_mode: $actual_mode,
      git_ignored: $ignored,
      repository_ignore_rule: $repository_ignore_rule,
      git_tracked: $tracked,
      outside_public_source_archive: $outside_public_source,
      result: $item_result
    }' >> "$protected_items_jsonl"
done < <(jq -r '.protected_paths[] | [.path, .mode] | @tsv' "$protected_policy_file")

if [[ -e "$infra_repository_root/environments/prod/.env" || \
      -L "$infra_repository_root/environments/prod/.env" ]]; then
  jq -nc --arg path "environments/prod/.env" '{path: $path}' \
    >> "$observed_paths_jsonl"
fi
if [[ -d "$infra_repository_root/environments/prod/secrets" ]]; then
  while IFS= read -r -d '' observed_file; do
    observed_relative="${observed_file#"$infra_repository_root/"}"
    jq -nc --arg path "$observed_relative" '{path: $path}' \
      >> "$observed_paths_jsonl"
  done < <(find "$infra_repository_root/environments/prod/secrets" \
    -mindepth 1 -maxdepth 1 -print0)
fi

jq -s 'map(.path) | sort' "$observed_paths_jsonl" \
  > "$protected_report_root/observed-paths.json"
jq -n \
  --slurpfile policy "$protected_policy_file" \
  --slurpfile observed "$protected_report_root/observed-paths.json" \
  '($observed[0] - [$policy[0].protected_paths[].path]) | sort' \
  > "$protected_report_root/unexpected-paths.json"
unexpected_path_count="$(jq 'length' "$protected_report_root/unexpected-paths.json")"
if [[ "$unexpected_path_count" -ne 0 ]]; then
  protected_policy_result="FAIL"
fi

while IFS= read -r forbidden_path; do
  forbidden_target="$infra_repository_root/$forbidden_path"
  forbidden_present=false
  forbidden_result="PASS"
  if [[ -e "$forbidden_target" || -L "$forbidden_target" ]]; then
    forbidden_present=true
    forbidden_result="FAIL"
    protected_policy_result="FAIL"
  fi
  jq -nc --arg path "$forbidden_path" --arg result "$forbidden_result" \
    --argjson present "$forbidden_present" \
    '{
      path: $path,
      required_state: "ABSENT",
      present: $present,
      result: $result
    }' >> "$forbidden_items_jsonl"
done < <(jq -r '.forbidden_paths[]' "$protected_policy_file")

protected_overall_result="PRESENT_PROTECTED"
if [[ "$protected_policy_result" != "PASS" ]]; then
  protected_overall_result="FAIL"
fi
protected_path_count="$(jq '.protected_paths | length' "$protected_policy_file")"
observed_path_count="$(jq 'length' "$protected_report_root/observed-paths.json")"

jq -s '.' "$protected_items_jsonl" > "$protected_report_root/items.json"
jq -s '.' "$forbidden_items_jsonl" > "$protected_report_root/forbidden-items.json"
jq -n \
  --arg schema_version "1.0" \
  --arg generated_at "$generated_at" \
  --arg revision "$infra_revision" \
  --arg overall_result "$protected_overall_result" \
  --arg policy_result "$protected_policy_result" \
  --argjson expected_path_count "$protected_path_count" \
  --argjson observed_path_count "$observed_path_count" \
  --argjson unexpected_path_count "$unexpected_path_count" \
  --slurpfile items "$protected_report_root/items.json" \
  --slurpfile forbidden_items "$protected_report_root/forbidden-items.json" \
  --slurpfile unexpected_paths "$protected_report_root/unexpected-paths.json" \
  '{
    schema_version: $schema_version,
    generated_at: $generated_at,
    repository: "syncbase-infra",
    revision: $revision,
    scope: "PATH_MODE_IGNORE_AND_ARCHIVE_MEMBERSHIP_ONLY",
    content_inspection: "NONE_PATH_METADATA_ONLY",
    overall_result: $overall_result,
    policy_result: $policy_result,
    expected_path_count: $expected_path_count,
    observed_path_count: $observed_path_count,
    unexpected_path_count: $unexpected_path_count,
    items: $items[0],
    forbidden_items: $forbidden_items[0],
    unexpected_paths: $unexpected_paths[0]
  }' > "$protected_report_root/result.json"

printf 'PROTECTED_MATERIAL_AUDIT result=%s policy=%s expected_paths=%s observed_paths=%s unexpected_paths=%s\n' \
  "$protected_overall_result" "$protected_policy_result" "$protected_path_count" \
  "$observed_path_count" "$unexpected_path_count"

diagnostic_report_root="$report_root/full-disk-diagnostic"
mkdir -p "$diagnostic_report_root"
diagnostic_repository_results="$diagnostic_report_root/repositories.jsonl"
: > "$diagnostic_repository_results"
diagnostic_expected_total=0
diagnostic_unexpected_total=0
diagnostic_error_count=0

expected_protected_paths_json="$(jq -c '[.protected_paths[].path]' "$protected_policy_file")"
for repository_spec in "${repository_specs[@]}"; do
  repository_id="${repository_spec%%:*}"
  repository_directory="${repository_spec#*:}"
  repository_root="$workspace_root/$repository_directory"
  repository_report="$diagnostic_report_root/$repository_id"
  mkdir -p "$repository_report"
  diagnostic_report="$repository_report/worktree.redacted.json"

  diagnostic_exit=0
  if run_gitleaks_dir "$repository_root" "$diagnostic_report"; then :; else diagnostic_exit=$?; fi
  diagnostic_report_valid=0
  if ensure_json_array_report "$diagnostic_report"; then :; else diagnostic_report_valid=$?; fi
  diagnostic_count="$(jq 'length' "$diagnostic_report")"

  if [[ "$repository_id" == "infra" ]]; then
    jq --argjson expected "$expected_protected_paths_json" '
      [.[] | {
        rule_id: .RuleID,
        file: (.File | sub("^\\./"; "")),
        start_line: .StartLine,
        classification: (
          (.File | sub("^\\./"; "")) as $file |
          if ($expected | index($file)) != null
          then "EXPECTED_PROTECTED_PATH"
          else "UNEXPECTED_PATH"
          end
        )
      }]
    ' "$diagnostic_report" > "$repository_report/worktree.summary.json"
  else
    jq '[.[] | {
      rule_id: .RuleID,
      file: (.File | sub("^\\./"; "")),
      start_line: .StartLine,
      classification: "UNEXPECTED_PATH"
    }]' "$diagnostic_report" > "$repository_report/worktree.summary.json"
  fi

  expected_count="$(jq '[.[] | select(.classification == "EXPECTED_PROTECTED_PATH")] | length' \
    "$repository_report/worktree.summary.json")"
  unexpected_count="$(jq '[.[] | select(.classification == "UNEXPECTED_PATH")] | length' \
    "$repository_report/worktree.summary.json")"
  diagnostic_expected_total=$((diagnostic_expected_total + expected_count))
  diagnostic_unexpected_total=$((diagnostic_unexpected_total + unexpected_count))

  repository_result="NO_FINDINGS"
  if [[ "$diagnostic_exit" -gt 1 || "$diagnostic_report_valid" -ne 0 ]]; then
    repository_result="ERROR"
    diagnostic_error_count=$((diagnostic_error_count + 1))
  elif [[ "$unexpected_count" -ne 0 ]]; then
    repository_result="UNEXPECTED_FINDINGS"
  elif [[ "$expected_count" -ne 0 ]]; then
    repository_result="EXPECTED_PROTECTED_FINDINGS"
  fi

  jq -nc \
    --arg id "$repository_id" \
    --arg result "$repository_result" \
    --argjson findings "$diagnostic_count" \
    --argjson expected_protected_findings "$expected_count" \
    --argjson unexpected_findings "$unexpected_count" \
    '{
      id: $id,
      result: $result,
      findings: $findings,
      expected_protected_findings: $expected_protected_findings,
      unexpected_findings: $unexpected_findings
    }' >> "$diagnostic_repository_results"
  printf 'FULL_DISK_DIAGNOSTIC repo=%s result=%s findings=%s expected_protected=%s unexpected=%s\n' \
    "$repository_id" "$repository_result" "$diagnostic_count" "$expected_count" \
    "$unexpected_count"
done

diagnostic_overall_result="NO_FINDINGS"
diagnostic_release_blocking=false
if [[ "$diagnostic_error_count" -ne 0 || "$diagnostic_unexpected_total" -ne 0 ]]; then
  diagnostic_overall_result="UNEXPECTED_FINDINGS"
  diagnostic_release_blocking=true
elif [[ "$diagnostic_expected_total" -ne 0 ]]; then
  diagnostic_overall_result="NON_PASS_EXPECTED_PROTECTED"
fi

jq -s \
  --arg schema_version "1.0" \
  --arg generated_at "$generated_at" \
  --arg scanner "gitleaks $scanner_version" \
  --arg overall_result "$diagnostic_overall_result" \
  --argjson release_authoritative false \
  --argjson release_blocking "$diagnostic_release_blocking" \
  --argjson expected_protected_findings "$diagnostic_expected_total" \
  --argjson unexpected_findings "$diagnostic_unexpected_total" \
  '{
    schema_version: $schema_version,
    generated_at: $generated_at,
    scanner: $scanner,
    scope: "FULL_ON_DISK_WORKTREE_INCLUDING_IGNORED_AND_UNTRACKED",
    overall_result: $overall_result,
    release_authoritative: $release_authoritative,
    release_blocking: $release_blocking,
    expected_protected_findings: $expected_protected_findings,
    unexpected_findings: $unexpected_findings,
    repositories: .
  }' "$diagnostic_repository_results" > "$diagnostic_report_root/result.json"

release_eligibility="PASS"
if [[ "$public_overall_result" != "PASS" || \
      "$protected_overall_result" != "PRESENT_PROTECTED" || \
      "$diagnostic_release_blocking" == true ]]; then
  release_eligibility="FAIL"
fi

jq -n \
  --arg schema_version "2.0" \
  --arg generated_at "$generated_at" \
  --arg release_eligibility "$release_eligibility" \
  --arg public_source_result "$public_overall_result" \
  --arg protected_material_result "$protected_overall_result" \
  --arg full_disk_diagnostic_result "$diagnostic_overall_result" \
  --argjson full_disk_diagnostic_release_blocking "$diagnostic_release_blocking" \
  --slurpfile repositories "$public_repository_results" \
  '{
    schema_version: $schema_version,
    generated_at: $generated_at,
    overall_result: $release_eligibility,
    release_eligibility: $release_eligibility,
    credential_rotation_evidence: "OUT_OF_SCOPE_SEPARATE_EVIDENCE_REQUIRED",
    public_source_scan: {
      result: $public_source_result,
      evidence: "public-source/result.json"
    },
    protected_material_audit: {
      result: $protected_material_result,
      evidence: "protected-material/result.json"
    },
    full_disk_diagnostic: {
      result: $full_disk_diagnostic_result,
      release_authoritative: false,
      release_blocking: $full_disk_diagnostic_release_blocking,
      evidence: "full-disk-diagnostic/result.json"
    },
    repositories: $repositories
  }' > "$report_root/result.json"

if [[ "$release_eligibility" != "PASS" ]]; then
  echo "RELEASE_SECRET_GATE_FAIL inspect redacted summaries; never publish untriaged findings" >&2
  exit 1
fi

printf 'RELEASE_SECRET_GATE_PASS public_source=%s protected_material=%s full_disk_diagnostic=%s\n' \
  "$public_overall_result" "$protected_overall_result" "$diagnostic_overall_result"

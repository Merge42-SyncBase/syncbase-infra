#!/usr/bin/env bash
set -euo pipefail

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${BUNDLED_PYTHON:-python3}"

"$python_bin" -m unittest "$infra_root/quality/test_verify_repositories.py" -v
"$python_bin" -m unittest "$infra_root/quality/acceptance/test_db_outage_recovery.py" -v
"$python_bin" -m unittest "$infra_root/qualification/opensql-gate/test_capture_blocker.py" -v
"$python_bin" -m unittest "$infra_root/evidence/tools/test_evidence_bundle.py" -v
"$python_bin" -m unittest "$infra_root/evaluation/test_collect_draft_observations.py" -v
"$python_bin" -W error::ResourceWarning -m unittest "$infra_root/evaluation/test_collect_frozen_observations.py" -v
"$python_bin" -m unittest "$infra_root/evaluation/test_evaluate_retrieval.py" -v
"$python_bin" -m unittest "$infra_root/evaluation/test_generate_version_fixtures.py" -v
"$python_bin" -m unittest "$infra_root/evaluation/test_static_no_answer_precheck.py" -v
"$python_bin" -m unittest "$infra_root/evaluation/test_validate_holdout_integrity.py" -v
"$python_bin" -m unittest "$infra_root/qualification/ann/test_assess_ann.py" -v

bash -n \
  "$infra_root/acceptance/run-p0.sh" \
  "$infra_root/acceptance/run-p0-flow.sh" \
  "$infra_root/acceptance/run-upload-browser.sh" \
  "$infra_root/acceptance/run-db-outage-recovery.sh" \
  "$infra_root/acceptance/run-failover.sh" \
  "$infra_root/quality/check-boundaries.sh" \
  "$infra_root/quality/check-environments.sh"

printf 'LANE_C_TOOLING_TESTS_PASS unit_suites=11 shell_syntax=true\n'

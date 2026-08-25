#!/usr/bin/env bash
set -euo pipefail

infra_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 -m unittest "$infra_root/quality/test_verify_repositories.py" -v
python3 -m unittest "$infra_root/qualification/opensql-gate/test_capture_blocker.py" -v
python3 -m unittest "$infra_root/evidence/tools/test_evidence_bundle.py" -v
python3 -m unittest "$infra_root/evaluation/test_evaluate_retrieval.py" -v
python3 -m unittest "$infra_root/qualification/ann/test_assess_ann.py" -v

bash -n \
  "$infra_root/acceptance/run-p0.sh" \
  "$infra_root/acceptance/run-p0-flow.sh" \
  "$infra_root/acceptance/run-upload-browser.sh" \
  "$infra_root/acceptance/run-db-outage-recovery.sh" \
  "$infra_root/acceptance/run-failover.sh" \
  "$infra_root/quality/check-boundaries.sh" \
  "$infra_root/quality/check-environments.sh"

printf 'LANE_C_TOOLING_TESTS_PASS unit_suites=5 shell_syntax=true\n'

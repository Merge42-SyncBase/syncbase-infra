from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("evidence_bundle.py")


def load_module():
    spec = importlib.util.spec_from_file_location("evidence_bundle", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_result(task_id: str = "C0_SOURCE_BASELINE") -> dict:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "run_id": "20260825T000000Z",
        "overall_result": "PASS",
        "evidence_grade": "SOURCE_BASELINE",
        "started_at": "2026-08-25T00:00:00Z",
        "completed_at": "2026-08-25T00:00:01Z",
        "repository_revisions": {
            "frontend": "1" * 40,
            "embedding": "2" * 40,
            "was": "3" * 40,
            "infra": "4" * 40,
            "mcp": "5" * 40,
        },
        "inputs": {},
        "measurements": {},
        "artifact_hashes": {"fixture.txt": "a" * 64},
        "failure_reason": None,
    }


class EvidenceBundleTest(unittest.TestCase):
    def test_validator_accepts_the_shared_result_contract(self) -> None:
        module = load_module()
        self.assertEqual(module.validate_result(valid_result()), [])

    def test_validator_rejects_a_short_revision_and_unknown_result(self) -> None:
        module = load_module()
        result = valid_result()
        result["overall_result"] = "MAYBE"
        result["repository_revisions"]["mcp"] = "deadbeef"
        errors = module.validate_result(result)
        self.assertTrue(any("overall_result" in error for error in errors))
        self.assertTrue(any("repository_revisions.mcp" in error for error in errors))

    def test_validator_rejects_a_tampered_self_hash(self) -> None:
        module = load_module()
        result = valid_result()
        result["result_sha256"] = "0" * 64
        errors = module.validate_result(result)
        self.assertTrue(any("result_sha256" in error for error in errors))

    def test_init_creates_the_lane_layout_and_claim_matrix_header(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_directory = Path(temporary_directory) / "run"
            module.initialize_bundle(run_directory, "20260825T000000Z")

            self.assertEqual(
                {path.name for path in run_directory.iterdir() if path.is_dir()},
                set(module.LANE_DIRECTORIES),
            )
            claim_matrix = run_directory / "99-final/claim-matrix.csv"
            with claim_matrix.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows, [module.CLAIM_COLUMNS])
            manifest = json.loads(
                (run_directory / "00-source/run-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["overall_result"], "BLOCKED")

    def test_finalize_seals_valid_results_but_does_not_promote_blocked_work(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_directory = Path(temporary_directory) / "run"
            module.initialize_bundle(run_directory, "20260825T000000Z")
            result_path = run_directory / "01-repository-checks/result.json"
            result_path.write_text(json.dumps(valid_result()), encoding="utf-8")

            index = module.finalize_bundle(
                run_directory, required_tasks={"C0_SOURCE_BASELINE"}
            )

            self.assertEqual(index["overall_result"], "PASS")
            self.assertTrue((run_directory / "99-final/SHA256SUMS").is_file())
            self.assertTrue((run_directory / "99-final/evidence-index.json").is_file())
            manifest = json.loads(
                (run_directory / "00-source/run-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["overall_result"], "PASS")
            self.assertIsNone(manifest["failure_reason"])

            blocked = valid_result("C3_OPENSQL_SMOKE")
            blocked["overall_result"] = "BLOCKED"
            blocked["evidence_grade"] = "UNAVAILABLE"
            blocked["failure_reason"] = "fixture environment unavailable"
            (run_directory / "03-opensql-smoke/result.json").write_text(
                json.dumps(blocked), encoding="utf-8"
            )
            index = module.finalize_bundle(
                run_directory,
                required_tasks={"C0_SOURCE_BASELINE", "C3_OPENSQL_SMOKE"},
            )
            self.assertEqual(index["overall_result"], "BLOCKED")

    def test_finalize_rejects_duplicate_task_results(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_directory = Path(temporary_directory) / "run"
            module.initialize_bundle(run_directory, "20260825T000000Z")
            for lane in ("01-repository-checks", "02-qualification-schema"):
                (run_directory / lane / "result.json").write_text(
                    json.dumps(valid_result()), encoding="utf-8"
                )
            with self.assertRaisesRegex(ValueError, "duplicate task_id"):
                module.finalize_bundle(
                    run_directory, required_tasks={"C0_SOURCE_BASELINE"}
                )

    def test_finalize_rejects_secret_bearing_evidence_without_echoing_the_value(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_directory = Path(temporary_directory) / "run"
            module.initialize_bundle(run_directory, "20260825T000000Z")
            (run_directory / "01-repository-checks/result.json").write_text(
                json.dumps(valid_result()), encoding="utf-8"
            )
            secret_value = "super-sensitive-fixture-value"
            (run_directory / "01-repository-checks/unsafe.log").write_text(
                f"Authorization: Bearer {secret_value}\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "potential secret") as raised:
                module.finalize_bundle(
                    run_directory, required_tasks={"C0_SOURCE_BASELINE"}
                )
            self.assertNotIn(secret_value, str(raised.exception))


if __name__ == "__main__":
    unittest.main()

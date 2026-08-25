from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("validate_holdout_integrity.py")
CALIBRATION_PATH = Path(__file__).with_name("queries.round1.draft.json")
HOLDOUT_PATH = Path(__file__).with_name("queries.round1.holdout.draft.json")


def load_module():
    spec = importlib.util.spec_from_file_location(
        "validate_holdout_integrity", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HoldoutIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
        self.holdout = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))

    def validate(
        self, calibration: dict, holdout: dict, *, stage: str = "draft"
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calibration_path = root / "calibration.json"
            holdout_path = root / "holdout.json"
            calibration_path.write_text(
                json.dumps(calibration, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            holdout["holdout_integrity"]["calibration_dataset_file_sha256"] = (
                self.module.file_sha256(calibration_path)
            )
            holdout["holdout_integrity"]["calibration_query_text_set_sha256"] = (
                self.module.query_text_set_sha256(calibration)
            )
            holdout_path.write_text(
                json.dumps(holdout, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return self.module.validate_holdout_integrity(
                calibration_path, holdout_path, stage=stage
            )

    def pre_freeze_holdout(self) -> dict:
        holdout = copy.deepcopy(self.holdout)
        holdout["query_exposure"] = "NOT_QUERIED_BEFORE_FREEZE"
        holdout["human_verification"] = {
            "status": "APPROVED",
            "worksheet": "evaluation/holdout-ground-truth-verification.md",
            "reviewer": "unit-test-reviewer",
            "reviewed_at": "2026-08-25T00:00:00Z",
        }
        for position, name in enumerate(
            (
                "corpus_sha256",
                "model_sha256",
                "tokenizer_sha256",
                "profile_sha256",
                "database_identity_sha256",
                "source_release_sha256",
            ),
            start=1,
        ):
            holdout["bindings"][name] = f"{position:064x}"
        holdout["bindings"]["profile_sha256"] = (
            self.module.ACCEPTED_PROFILE_SHA256
        )
        for position, name in enumerate(
            ("frontend", "embedding", "was", "infra", "mcp"), start=1
        ):
            holdout["bindings"]["repository_revisions"][name] = f"{position:040x}"
        return holdout

    def test_checked_in_calibration_and_holdout_pass(self) -> None:
        result = self.module.validate_holdout_integrity(
            CALIBRATION_PATH, HOLDOUT_PATH
        )

        self.assertEqual(result, [])

    def test_normalized_calibration_query_reuse_is_rejected(self) -> None:
        holdout = copy.deepcopy(self.holdout)
        holdout["queries"][0]["query"] = (
            "  " + self.calibration["queries"][0]["query"].upper() + "  "
        )

        errors = self.validate(self.calibration, holdout)

        self.assertTrue(
            any("normalized query text overlap" in error for error in errors), errors
        )

    def test_factual_identifier_evidence_reuse_is_rejected(self) -> None:
        holdout = copy.deepcopy(self.holdout)
        calibration_evidence = copy.deepcopy(
            next(
                query
                for query in self.calibration["queries"]
                if query["category"] == "factual_paraphrase"
            )["candidate_evidence"][0]
        )
        holdout["queries"][0]["candidate_evidence"][0] = calibration_evidence

        errors = self.validate(self.calibration, holdout)

        self.assertTrue(
            any("factual/identifier evidence overlap" in error for error in errors),
            errors,
        )

    def test_reused_dataset_identity_and_bad_calibration_binding_are_rejected(self) -> None:
        holdout = copy.deepcopy(self.holdout)
        holdout["dataset_id"] = self.calibration["dataset_id"]
        holdout["holdout_integrity"]["calibration_dataset_id"] = "wrong-id"

        errors = self.validate(self.calibration, holdout)

        self.assertIn("calibration and holdout dataset_id must differ", errors)
        self.assertIn(
            "holdout_integrity.calibration_dataset_id does not match calibration",
            errors,
        )

    def test_threshold_profile_or_metric_contract_drift_is_rejected(self) -> None:
        holdout = copy.deepcopy(self.holdout)
        holdout["thresholds"]["mrr_min"] = 0.74
        holdout["bindings"]["profile_sha256"] = "a" * 64
        holdout["metric_contract"]["retrieval_limit"] = 4

        errors = self.validate(self.calibration, holdout)

        self.assertIn("holdout thresholds differ from calibration", errors)
        self.assertIn("holdout profile binding differs from calibration", errors)
        self.assertIn("holdout metric_contract differs from calibration", errors)

    def test_exposed_or_release_bound_draft_is_rejected(self) -> None:
        holdout = copy.deepcopy(self.holdout)
        holdout["query_exposure"] = "QUERIED"
        holdout["holdout_integrity"]["runtime_exposure_status"] = "QUERIED"
        holdout["human_verification"]["status"] = "APPROVED"
        holdout["human_verification"]["reviewer"] = "reviewer"
        holdout["human_verification"]["reviewed_at"] = "2026-08-25T00:00:00Z"
        holdout["bindings"]["corpus_sha256"] = "b" * 64

        errors = self.validate(self.calibration, holdout)

        self.assertIn(
            "holdout query_exposure must be NOT_QUERIED_AT_DRAFT_CREATION", errors
        )
        self.assertIn(
            "holdout runtime_exposure_status must be NOT_QUERIED", errors
        )
        self.assertIn("holdout human verification must remain PENDING", errors)
        self.assertIn("holdout release bindings must all remain null", errors)

    def test_pre_freeze_stage_accepts_approved_bound_unqueried_draft(self) -> None:
        holdout = self.pre_freeze_holdout()

        errors = self.validate(
            self.calibration, holdout, stage="pre-freeze"
        )

        self.assertEqual(errors, [])

    def test_pre_freeze_stage_rejects_pending_null_or_initial_exposure(self) -> None:
        holdout = copy.deepcopy(self.holdout)

        errors = self.validate(
            self.calibration, holdout, stage="pre-freeze"
        )

        self.assertIn(
            "pre-freeze query_exposure must be NOT_QUERIED_BEFORE_FREEZE", errors
        )
        self.assertIn("pre-freeze human verification must be APPROVED", errors)
        self.assertIn("pre-freeze release bindings must be complete", errors)

    def test_pre_freeze_stage_rejects_a_different_runtime_profile(self) -> None:
        holdout = self.pre_freeze_holdout()
        holdout["bindings"]["profile_sha256"] = "9" * 64

        errors = self.validate(self.calibration, holdout, stage="pre-freeze")

        self.assertIn(
            "pre-freeze profile binding differs from the accepted 0.93 profile",
            errors,
        )

    def test_default_stage_still_rejects_pre_freeze_mutations(self) -> None:
        holdout = self.pre_freeze_holdout()

        errors = self.validate(self.calibration, holdout)

        self.assertIn(
            "holdout query_exposure must be NOT_QUERIED_AT_DRAFT_CREATION", errors
        )
        self.assertIn("holdout human verification must remain PENDING", errors)
        self.assertIn("holdout release bindings must all remain null", errors)


if __name__ == "__main__":
    unittest.main()

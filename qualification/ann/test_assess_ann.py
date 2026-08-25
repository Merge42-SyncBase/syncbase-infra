from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("assess_ann.py")


def load_module():
    spec = importlib.util.spec_from_file_location("assess_ann", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "fixture-run",
        "repository_revisions": {
            "frontend": "1" * 40,
            "embedding": "2" * 40,
            "was": "3" * 40,
            "infra": "4" * 40,
            "mcp": "5" * 40,
        },
        "database": {
            "identity_sha256": "a" * 64,
            "extension": "vector",
            "extension_version": "fixture",
            "supported_methods": ["hnsw", "ivfflat"],
        },
        "selected_method": "hnsw",
        "index": {
            "exists": True,
            "name": "search_chunk_embedding_hnsw_idx",
            "access_method": "hnsw",
        },
        "plan": {
            "explain_analyze_buffers": True,
            "application_equivalent": True,
            "planner_settings_natural": True,
            "enable_seqscan_forced_off": False,
            "index_names": ["search_chunk_embedding_hnsw_idx"],
            "node_types": ["Limit", "Index Scan"],
        },
        "metrics": {
            "corpus_chunk_count": 10000,
            "exact_recall_at_5": 0.90,
            "ann_recall_at_5": 0.89,
        },
        "artifact_hashes": {
            "explain.json": "b" * 64,
            "evaluation.json": "c" * 64,
        },
    }


class AssessAnnTest(unittest.TestCase):
    def test_supported_natural_hnsw_plan_within_recall_budget_passes(self) -> None:
        module = load_module()
        result = module.assess(
            capture(),
            started_at="2026-08-25T00:00:00Z",
            completed_at="2026-08-25T00:00:01Z",
        )
        self.assertEqual(result["overall_result"], "PASS")
        self.assertEqual(result["measurements"]["recall_at_5_degradation"], 0.01)

    def test_forcing_sequential_scan_off_cannot_prove_ann_usage(self) -> None:
        module = load_module()
        evidence = capture()
        evidence["plan"]["enable_seqscan_forced_off"] = True
        result = module.assess(
            evidence,
            started_at="2026-08-25T00:00:00Z",
            completed_at="2026-08-25T00:00:01Z",
        )
        self.assertEqual(result["overall_result"], "FAIL")
        self.assertIn("natural_planner_choice", result["failed_gates"])

    def test_absent_ann_capability_is_skipped_not_failed_or_passed(self) -> None:
        module = load_module()
        evidence = capture()
        evidence["database"]["supported_methods"] = []
        evidence["selected_method"] = "exact"
        evidence["index"] = None
        evidence["plan"] = None
        result = module.assess(
            evidence,
            started_at="2026-08-25T00:00:00Z",
            completed_at="2026-08-25T00:00:01Z",
        )
        self.assertEqual(result["overall_result"], "SKIPPED")
        self.assertIn("not supported", result["failure_reason"])

    def test_recall_degradation_above_point_zero_two_fails(self) -> None:
        module = load_module()
        evidence = capture()
        evidence["metrics"]["ann_recall_at_5"] = 0.87
        result = module.assess(
            evidence,
            started_at="2026-08-25T00:00:00Z",
            completed_at="2026-08-25T00:00:01Z",
        )
        self.assertEqual(result["overall_result"], "FAIL")
        self.assertIn("recall_at_5_degradation", result["failed_gates"])

    def test_missing_raw_explain_artifact_hash_cannot_pass(self) -> None:
        module = load_module()
        evidence = capture()
        evidence["artifact_hashes"].pop("explain.json")
        result = module.assess(
            evidence,
            started_at="2026-08-25T00:00:00Z",
            completed_at="2026-08-25T00:00:01Z",
        )
        self.assertEqual(result["overall_result"], "FAIL")
        self.assertIn("raw_artifact_hashes", result["failed_gates"])


if __name__ == "__main__":
    unittest.main()

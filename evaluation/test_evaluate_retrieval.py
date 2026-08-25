from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("evaluate_retrieval.py")
TEMPLATE_PATH = Path(__file__).with_name("queries.template.json")


def load_module():
    spec = importlib.util.spec_from_file_location("evaluate_retrieval", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bindings() -> dict:
    return {
        "corpus_sha256": "a" * 64,
        "model_sha256": "b" * 64,
        "tokenizer_sha256": "c" * 64,
        "profile_sha256": "d" * 64,
        "database_identity_sha256": "e" * 64,
        "source_release_sha256": "f" * 64,
        "repository_revisions": {
            "frontend": "1" * 40,
            "embedding": "2" * 40,
            "was": "3" * 40,
            "infra": "4" * 40,
            "mcp": "5" * 40,
        },
    }


def complete_draft() -> dict:
    queries = []
    category_counts = {
        "factual_paraphrase": 10,
        "exact_identifier": 5,
        "version_sensitive": 5,
        "no_answer": 10,
    }
    prefixes = {
        "factual_paraphrase": "F",
        "exact_identifier": "I",
        "version_sensitive": "V",
        "no_answer": "N",
    }
    for category, count in category_counts.items():
        for number in range(1, count + 1):
            query_id = f"{prefixes[category]}{number:02d}"
            no_answer = category == "no_answer"
            relevant = [] if no_answer else [{
                "source_sha256": f"{number % 10}" * 64,
                "version": 2 if category == "version_sensitive" else 1,
                "pages": [number],
            }]
            forbidden = []
            if category == "version_sensitive":
                forbidden = [{
                    "source_sha256": f"{number % 10}" * 64,
                    "version": 1,
                }]
            queries.append({
                "id": query_id,
                "category": category,
                "query": f"ground-truthed query {query_id}",
                "expected": {
                    "no_answer": no_answer,
                    "relevant": relevant,
                    "forbidden": forbidden,
                },
            })
    return {
        "schema_version": "1.0",
        "dataset_id": "round1-fixture",
        "status": "DRAFT",
        "description": "independently worked evaluator fixture",
        "bindings": bindings(),
        "thresholds": {
            "recall_at_5_min": 0.85,
            "mrr_min": 0.75,
            "citation_page_correctness_min": 1.0,
            "superseded_version_leakage_max": 0,
            "no_answer_false_positive_rate_max": 0.10,
            "ann_recall_at_5_degradation_max": 0.02,
        },
        "queries": queries,
    }


def observations(module, dataset: dict, *, one_no_answer_false_positive: bool = False) -> dict:
    items = []
    for number, query in enumerate(dataset["queries"], start=1):
        expected = query["expected"]
        hits = []
        if expected["relevant"]:
            target = expected["relevant"][0]
            hits.append({
                "source_sha256": target["source_sha256"],
                "version": target["version"],
                "page": target["pages"][0],
                "score": 0.9,
            })
        elif one_no_answer_false_positive and query["id"] == "N01":
            hits.append({
                "source_sha256": "9" * 64,
                "version": 1,
                "page": 1,
                "score": 0.8,
            })
        items.append({
            "id": query["id"],
            "latency_ms": number,
            "grounding_status": "SUPPORTED" if hits else "INSUFFICIENT_EVIDENCE",
            "grounding_reason": None if hits else "NO_HITS_ABOVE_POLICY",
            "results": hits,
        })
    return {
        "schema_version": "1.0",
        "dataset_sha256": module.dataset_sha256(dataset),
        "retrieval_mode": "exact",
        "bindings": dataset["bindings"],
        "queries": items,
    }


class RetrievalEvaluationTest(unittest.TestCase):
    def test_template_has_the_accepted_distribution_and_thresholds(self) -> None:
        template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        counts = Counter(query["category"] for query in template["queries"])
        self.assertEqual(counts, {
            "factual_paraphrase": 10,
            "exact_identifier": 5,
            "version_sensitive": 5,
            "no_answer": 10,
        })
        self.assertEqual(template["thresholds"], complete_draft()["thresholds"])
        self.assertEqual(template["status"], "DRAFT")

    def test_freeze_rejects_the_unworked_template(self) -> None:
        module = load_module()
        template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "placeholder"):
            module.freeze_dataset(template, frozen_at="2026-08-25T00:00:00Z")

    def test_freeze_records_a_content_hash_only_after_ground_truth_is_complete(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        self.assertEqual(dataset["status"], "FROZEN")
        self.assertEqual(len(dataset["dataset_sha256"]), 64)
        self.assertEqual(dataset["dataset_sha256"], module.dataset_sha256(dataset))

    def test_evaluator_passes_at_the_exact_published_thresholds(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        result = module.evaluate(
            dataset,
            observations(module, dataset, one_no_answer_false_positive=True),
            started_at="2026-08-25T00:00:01Z",
            completed_at="2026-08-25T00:00:02Z",
            run_id="fixture-run",
        )
        metrics = result["measurements"]["exact"]
        self.assertEqual(result["overall_result"], "PASS")
        self.assertEqual(metrics["recall_at_5"], 1.0)
        self.assertEqual(metrics["mrr"], 1.0)
        self.assertEqual(metrics["citation_page_correctness"], 1.0)
        self.assertEqual(metrics["superseded_version_leakage"], 0)
        self.assertEqual(metrics["no_answer_false_positive_rate"], 0.1)
        self.assertEqual(metrics["search_latency_ms_p50"], 15)
        self.assertEqual(metrics["search_latency_ms_p95"], 29)

    def test_any_superseded_version_leakage_fails_the_release_gate(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)
        version_query = next(item for item in exact["queries"] if item["id"] == "V01")
        version_query["results"].insert(0, {
            "source_sha256": "1" * 64,
            "version": 1,
            "page": 1,
            "score": 0.95,
        })
        result = module.evaluate(
            dataset,
            exact,
            started_at="2026-08-25T00:00:01Z",
            completed_at="2026-08-25T00:00:02Z",
            run_id="fixture-run",
        )
        self.assertEqual(result["overall_result"], "FAIL")
        self.assertEqual(
            result["measurements"]["exact"]["superseded_version_leakage"], 1
        )

    def test_empty_results_must_use_explicit_insufficient_evidence_status(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)
        no_answer = next(item for item in exact["queries"] if item["id"] == "N01")
        no_answer["grounding_status"] = "SUPPORTED"
        with self.assertRaisesRegex(ValueError, "grounding_status"):
            module.evaluate(
                dataset,
                exact,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="fixture-run",
            )

    def test_insufficient_evidence_requires_an_explicit_reason(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)
        no_answer = next(item for item in exact["queries"] if item["id"] == "N01")
        no_answer.pop("grounding_reason")
        with self.assertRaisesRegex(ValueError, "grounding_reason"):
            module.evaluate(
                dataset,
                exact,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="fixture-run",
            )

    def test_ann_requires_recall_degradation_no_greater_than_point_zero_two(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)
        ann = observations(module, dataset)
        ann["retrieval_mode"] = "ann"
        for query_id in ("F01", "F02"):
            item = next(item for item in ann["queries"] if item["id"] == query_id)
            item["results"] = []
            item["grounding_status"] = "INSUFFICIENT_EVIDENCE"
            item["grounding_reason"] = "NO_HITS_ABOVE_POLICY"
        result = module.evaluate(
            dataset,
            exact,
            ann_observations=ann,
            started_at="2026-08-25T00:00:01Z",
            completed_at="2026-08-25T00:00:02Z",
            run_id="fixture-run",
        )
        self.assertEqual(result["overall_result"], "FAIL")
        self.assertAlmostEqual(
            result["measurements"]["ann_recall_at_5_degradation"], 0.1
        )


if __name__ == "__main__":
    unittest.main()

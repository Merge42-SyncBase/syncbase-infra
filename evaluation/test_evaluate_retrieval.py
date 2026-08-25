from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from collections import Counter
from functools import lru_cache
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("evaluate_retrieval.py")
TEMPLATE_PATH = Path(__file__).with_name("queries.template.json")
DRAFT_PATH = Path(__file__).with_name("queries.round1.draft.json")
HOLDOUT_PATH = Path(__file__).with_name("queries.round1.holdout.draft.json")
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def accepted_metric_contract() -> dict:
    return {
        "version": "round1-citation-provenance-v1",
        "retrieval_limit": 5,
        "citation_page_correctness": {
            "population": "ALL_RETURNED_HITS",
            "empty_population_value": 0.0,
            "required_checks": [
                "RUNTIME_SOURCE_BINDING",
                "RAW_PDF_SHA256",
                "PAGE_IN_RANGE",
                "SNIPPET_ON_CITED_PAGE",
                "SOURCE_URL_TUPLE",
            ],
        },
    }


def fixture_page_text(label: str, page: int) -> str:
    return f"{label} evidence on page {page}"


@lru_cache(maxsize=None)
def fixture_pdf_bytes(label: str, page_count: int) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    pdf = canvas.Canvas(
        output,
        pagesize=letter,
        invariant=1,
        pageCompression=0,
    )
    for page in range(1, page_count + 1):
        pdf.drawString(72, 720, fixture_page_text(label, page))
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def fixture_pdf_sha256(label: str, page_count: int) -> str:
    return hashlib.sha256(fixture_pdf_bytes(label, page_count)).hexdigest()


def load_module():
    spec = importlib.util.spec_from_file_location("evaluate_retrieval", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Most evaluator tests need a structurally frozen in-memory fixture, not a
    # release freeze.  Keep that test-only shortcut explicit while production
    # callers use freeze_dataset(), which revalidates source bytes.
    module.release_freeze_dataset = module.freeze_dataset
    module.freeze_dataset = module._freeze_dataset_after_validation
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
    version_fixture_plans = []
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
    factual_source_sha256 = fixture_pdf_sha256("factual", 20)
    version_v1_source_sha256 = fixture_pdf_sha256("version-v1", 1)
    version_v2_source_sha256 = fixture_pdf_sha256("version-v2", 5)
    for category, count in category_counts.items():
        for number in range(1, count + 1):
            query_id = f"{prefixes[category]}{number:02d}"
            no_answer = category == "no_answer"
            source_sha256 = factual_source_sha256
            relevant_source_sha256 = source_sha256
            if category == "version_sensitive":
                source_sha256 = version_v1_source_sha256
                relevant_source_sha256 = version_v2_source_sha256
            relevant = [] if no_answer else [{
                "source_sha256": relevant_source_sha256,
                "version": 2 if category == "version_sensitive" else 1,
                "pages": [number],
            }]
            forbidden = []
            if category == "version_sensitive":
                forbidden = [{
                    "source_sha256": source_sha256,
                    "version": 1,
                }]
                version_fixture_plans.append({
                    "id": f"VP{number:02d}",
                    "query_id": query_id,
                    "status": "READY",
                    "base_source": {
                        "source_file": f"documents/fixture-v{number}.pdf",
                        "source_sha256": source_sha256,
                        "page": 1,
                        "supporting_excerpt": f"fixture V1 evidence {number}",
                    },
                    "v1_source_sha256": source_sha256,
                    "v2_source_sha256": relevant_source_sha256,
                    "v2_page": number,
                    "v2_marker": f"SYNCBASE-R1-V{number:02d}",
                    "v2_only_text": (
                        f"SYNCBASE-R1-V{number:02d} fixture V2 evidence {number}"
                    ),
                })
            query = {
                "id": query_id,
                "category": category,
                "query": f"ground-truthed query {query_id}",
                "expected": {
                    "no_answer": no_answer,
                    "relevant": relevant,
                    "forbidden": forbidden,
                },
            }
            if category in {"factual_paraphrase", "exact_identifier"}:
                query["candidate_evidence"] = [{
                    "source_file": f"documents/fixture-{query_id}.pdf",
                    "source_sha256": source_sha256,
                    "page": number,
                    "supporting_excerpt": f"fixture evidence {query_id}",
                }]
            if category == "version_sensitive":
                query["fixture_plan_id"] = f"VP{number:02d}"
                query["ground_truth_state"] = "VERIFIED_VERSION_PAIR"
            queries.append(query)
    return {
        "schema_version": "1.0",
        "dataset_id": "round1-fixture",
        "dataset_role": "PROSPECTIVE_HOLDOUT",
        "prospective_holdout": True,
        "benchmark_claim": "NOT_RUN",
        "query_exposure": "NOT_QUERIED_BEFORE_FREEZE",
        "status": "DRAFT",
        "description": "independently worked evaluator fixture",
        "human_verification": {
            "status": "APPROVED",
            "worksheet": "evaluation/fixture-verification.md",
            "reviewer": "unit-test-reviewer",
            "reviewed_at": "2026-08-25T00:00:00Z",
        },
        "bindings": bindings(),
        "thresholds": {
            "recall_at_5_min": 0.85,
            "mrr_min": 0.75,
            "citation_page_correctness_min": 1.0,
            "superseded_version_leakage_max": 0,
            "no_answer_false_positive_rate_max": 0.10,
            "ann_recall_at_5_degradation_max": 0.02,
        },
        "metric_contract": accepted_metric_contract(),
        "version_fixture_protocol": {
            "strategy": "APPEND_ONE_INVARIANT_PDF_PAGE",
            "v1_bytes": "UNCHANGED_PUBLIC_BASE_PDF",
            "v2_bytes": "V1_PLUS_ONE_CANONICAL_MARKER_PAGE",
            "generator_contract": "REPORTLAB_INVARIANT_1_THEN_PYPDF_APPEND",
            "release_gate": "GENERATE_HASH_RENDER_HUMAN_APPROVE_BEFORE_FREEZE",
        },
        "version_fixture_plans": version_fixture_plans,
        "queries": queries,
    }


def observations(
    module,
    dataset: dict,
    evidence_root: Path | None = None,
    *,
    one_no_answer_false_positive: bool = False,
) -> dict:
    source_specs = {
        fixture_pdf_sha256("factual", 20): {
            "document_id": "factual-document",
            "version_id": "factual-version-1",
            "version": 1,
            "active": True,
            "page_count": 20,
            "raw_pdf_artifact": "sources/factual-v1.pdf",
            "label": "factual",
            "bytes": fixture_pdf_bytes("factual", 20),
        },
        fixture_pdf_sha256("version-v1", 1): {
            "document_id": "version-document",
            "version_id": "version-version-1",
            "version": 1,
            "active": False,
            "page_count": 1,
            "raw_pdf_artifact": "sources/version-v1.pdf",
            "label": "version-v1",
            "bytes": fixture_pdf_bytes("version-v1", 1),
        },
        fixture_pdf_sha256("version-v2", 5): {
            "document_id": "version-document",
            "version_id": "version-version-2",
            "version": 2,
            "active": True,
            "page_count": 5,
            "raw_pdf_artifact": "sources/version-v2.pdf",
            "label": "version-v2",
            "bytes": fixture_pdf_bytes("version-v2", 5),
        },
    }
    if evidence_root is not None:
        for spec in source_specs.values():
            artifact = evidence_root / spec["raw_pdf_artifact"]
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(spec["bytes"])

    items = []
    for number, query in enumerate(dataset["queries"], start=1):
        expected = query["expected"]
        hits = []
        if expected["relevant"]:
            target = expected["relevant"][0]
            spec = source_specs[target["source_sha256"]]
            hits.append({
                "rank": 1,
                "document_id": spec["document_id"],
                "version_id": spec["version_id"],
                "source_sha256": target["source_sha256"],
                "version": target["version"],
                "page": target["pages"][0],
                "score": 0.9,
                "snippet": fixture_page_text(spec["label"], target["pages"][0]),
                "source_url": (
                    "https://syncbase.example/sources/"
                    f"{spec['document_id']}/versions/{target['version']}"
                    f"?page={target['pages'][0]}"
                ),
            })
        elif one_no_answer_false_positive and query["id"] == "N01":
            source_sha256 = fixture_pdf_sha256("factual", 20)
            spec = source_specs[source_sha256]
            hits.append({
                "rank": 1,
                "document_id": spec["document_id"],
                "version_id": spec["version_id"],
                "source_sha256": source_sha256,
                "version": 1,
                "page": 1,
                "score": 0.8,
                "snippet": fixture_page_text("factual", 1),
                "source_url": (
                    "https://syncbase.example/sources/"
                    f"{spec['document_id']}/versions/1?page=1"
                ),
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
        "retrieval_limit": 5,
        "source_origin": "https://syncbase.example",
        "bindings": dataset["bindings"],
        "source_bindings": [
            {
                key: value
                for key, value in spec.items()
                if key not in {"label", "bytes"}
            }
            | {"source_sha256": source_sha256, "raw_pdf_sha256": source_sha256}
            for source_sha256, spec in source_specs.items()
        ],
        "queries": items,
    }


def set_results(observation: dict, query_id: str, hits: list[dict]) -> None:
    item = next(query for query in observation["queries"] if query["id"] == query_id)
    item["results"] = hits
    item["grounding_status"] = "SUPPORTED" if hits else "INSUFFICIENT_EVIDENCE"
    item["grounding_reason"] = None if hits else "NO_HITS_ABOVE_POLICY"


def clear_results_except(observation: dict, query_id: str) -> None:
    for item in observation["queries"]:
        if item["id"] != query_id:
            set_results(observation, item["id"], [])


class RetrievalEvaluationTest(unittest.TestCase):
    def test_freeze_rejects_metric_contract_drift(self) -> None:
        module = load_module()
        draft = complete_draft()
        draft["metric_contract"]["retrieval_limit"] = 4

        with self.assertRaisesRegex(
            ValueError, "metric_contract differs from the accepted Round-1 contract"
        ):
            module.freeze_dataset(draft, frozen_at="2026-08-25T00:00:00Z")

    def test_calibration_dataset_cannot_freeze(self) -> None:
        module = load_module()
        draft = complete_draft()
        draft["dataset_role"] = "CALIBRATION"
        draft["prospective_holdout"] = False
        draft["benchmark_claim"] = "CALIBRATION_DIAGNOSTIC_NOT_EVALUATED"
        draft["query_exposure"] = (
            "ALL_30_QUERY_TEXTS_OBSERVED_IN_DRAFT_RUNTIME_DIAGNOSTICS"
        )

        self.assertEqual(
            module.validate_dataset(
                draft, require_frozen=False, allow_pending=False
            ),
            [],
        )

        with self.assertRaisesRegex(
            ValueError, "dataset_role must be PROSPECTIVE_HOLDOUT before freeze"
        ):
            module.freeze_dataset(draft, frozen_at="2026-08-25T00:00:00Z")

    def test_prospective_holdout_must_assert_not_queried_before_freeze(
        self,
    ) -> None:
        module = load_module()
        draft = complete_draft()
        draft["query_exposure"] = "NOT_QUERIED_AT_DRAFT_CREATION"

        self.assertEqual(
            module.validate_dataset(
                draft, require_frozen=False, allow_pending=True
            ),
            [],
        )
        with self.assertRaisesRegex(
            ValueError,
            "query_exposure must be NOT_QUERIED_BEFORE_FREEZE",
        ):
            module.freeze_dataset(draft, frozen_at="2026-08-25T00:00:00Z")

        draft["query_exposure"] = "QUERIED_BEFORE_FREEZE"
        errors = module.validate_dataset(
            draft, require_frozen=False, allow_pending=True
        )
        self.assertIn(
            "prospective holdout query_exposure must record a non-queried state",
            errors,
        )

        frozen = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        self.assertEqual(frozen["benchmark_claim"], "NOT_RUN")
        self.assertEqual(
            frozen["query_exposure"], "NOT_QUERIED_BEFORE_FREEZE"
        )
        frozen["query_exposure"] = "NOT_QUERIED_AT_DRAFT_CREATION"
        frozen["dataset_sha256"] = module.dataset_sha256(frozen)
        self.assertIn(
            "prospective holdout query_exposure must be "
            "NOT_QUERIED_BEFORE_FREEZE",
            module.validate_dataset(
                frozen, require_frozen=True, allow_pending=False
            ),
        )

    def test_prospective_holdout_benchmark_claim_must_remain_not_run(self) -> None:
        module = load_module()
        draft = complete_draft()
        draft["benchmark_claim"] = "PASS"

        errors = module.validate_dataset(
            draft, require_frozen=False, allow_pending=True
        )

        self.assertIn(
            "prospective holdout benchmark_claim must remain NOT_RUN", errors
        )

    def test_freeze_rejects_malformed_and_obviously_future_utc_timestamps(
        self,
    ) -> None:
        module = load_module()
        malformed_review = complete_draft()
        malformed_review["human_verification"]["reviewed_at"] = "Z"
        with self.assertRaisesRegex(ValueError, "parseable RFC3339 UTC timestamp"):
            module.freeze_dataset(
                malformed_review, frozen_at="2026-08-25T00:00:00Z"
            )

        with self.assertRaisesRegex(ValueError, "parseable RFC3339 UTC timestamp"):
            module.freeze_dataset(complete_draft(), frozen_at="Z")

        future_review = complete_draft()
        future_review["human_verification"]["reviewed_at"] = (
            "2999-01-01T00:00:00Z"
        )
        with self.assertRaisesRegex(ValueError, "must not be in the future"):
            module.freeze_dataset(
                future_review, frozen_at="2026-08-25T00:00:00Z"
            )

        with self.assertRaisesRegex(ValueError, "must not be in the future"):
            module.freeze_dataset(
                complete_draft(), frozen_at="2999-01-01T00:00:00Z"
            )

    def test_observations_require_source_bindings_and_provenance_fields(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)
        exact.pop("retrieval_limit")
        exact.pop("source_bindings")
        for item in exact["queries"]:
            for hit in item["results"]:
                for field in (
                    "rank",
                    "document_id",
                    "version_id",
                    "snippet",
                    "source_url",
                ):
                    hit.pop(field)

        errors = module.validate_observations(dataset, exact, "exact")

        self.assertIn("observations.retrieval_limit must be 5", errors)
        self.assertIn("observations.source_bindings must be an array", errors)
        self.assertTrue(
            any("query F01 hit 1 has invalid rank" in error for error in errors),
            errors,
        )
        for field in ("document_id", "version_id", "snippet", "source_url"):
            self.assertTrue(
                any(
                    f"query F01 hit 1 has invalid {field}" in error
                    for error in errors
                ),
                errors,
            )

    def test_observations_reject_duplicate_source_bindings(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)
        exact["retrieval_limit"] = 5
        binding = {
            "document_id": "document-1",
            "version_id": "version-1",
            "source_sha256": "a" * 64,
            "version": 1,
            "active": True,
            "page_count": 1,
            "raw_pdf_artifact": "sources/document-1-v1.pdf",
            "raw_pdf_sha256": "a" * 64,
        }
        exact["source_bindings"] = [binding, copy.deepcopy(binding)]
        for item in exact["queries"]:
            for rank, hit in enumerate(item["results"], start=1):
                hit.update({
                    "rank": rank,
                    "document_id": "document-1",
                    "version_id": "version-1",
                    "snippet": "citation snippet",
                    "source_url": (
                        "https://syncbase.example/sources/document-1/versions/1?page=1"
                    ),
                })

        errors = module.validate_observations(dataset, exact, "exact")

        self.assertIn("duplicate source binding version_id: version-1", errors)
        self.assertIn(
            "duplicate source binding document/version: document-1/1", errors
        )

    def test_observations_reject_boolean_hit_coordinates(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)
        hit = next(
            item for item in exact["queries"] if item["id"] == "F01"
        )["results"][0]
        hit["rank"] = True
        hit["version"] = True
        hit["page"] = True

        errors = module.validate_observations(dataset, exact, "exact")

        self.assertIn("query F01 hit 1 has invalid rank", errors)
        self.assertIn("query F01 hit 1 has invalid version", errors)
        self.assertIn("query F01 hit 1 has invalid page", errors)

    def test_source_binding_artifacts_must_be_portable_safe_relative_pdf_paths(
        self,
    ) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        exact = observations(module, dataset)

        for artifact in (
            "../escape.pdf",
            "/tmp/escape.pdf",
            r"sources\escape.pdf",
        ):
            with self.subTest(artifact=artifact):
                candidate = copy.deepcopy(exact)
                candidate["source_bindings"][0]["raw_pdf_artifact"] = artifact
                errors = module.validate_observations(dataset, candidate, "exact")
                self.assertTrue(
                    any(
                        "raw_pdf_artifact must be a safe relative PDF path"
                        in error
                        for error in errors
                    ),
                    errors,
                )

    def test_citation_page_correctness_counts_every_returned_hit(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            for item in exact["queries"]:
                if item["id"] != "F01":
                    item["results"] = []
                    item["grounding_status"] = "INSUFFICIENT_EVIDENCE"
                    item["grounding_reason"] = "NO_HITS_ABOVE_POLICY"
            f01 = next(item for item in exact["queries"] if item["id"] == "F01")
            wrong_page_snippet = copy.deepcopy(f01["results"][0])
            wrong_page_snippet.update({
                "rank": 2,
                "page": 2,
                "source_url": (
                    "https://syncbase.example/sources/"
                    "factual-document/versions/1?page=2"
                ),
            })
            f01["results"].append(wrong_page_snippet)

            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="fixture-run",
            )

        metrics = result["measurements"]["exact"]
        self.assertEqual(metrics["citation_page_correct_hits"], 1)
        self.assertEqual(metrics["citation_page_evaluated_hits"], 2)
        self.assertEqual(metrics["citation_page_correctness"], 0.5)
        self.assertEqual(metrics["citation_page_failure_count"], 1)
        self.assertEqual(metrics["citation_page_failures"], [{
            "query_id": "F01",
            "rank": 2,
            "reasons": ["SNIPPET_NOT_ON_CITED_PAGE"],
        }])
        self.assertIn("citation_page_correctness", result["failed_gates"])

    def test_runtime_source_binding_must_be_active(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            clear_results_except(exact, "F01")
            factual_binding = next(
                binding
                for binding in exact["source_bindings"]
                if binding["document_id"] == "factual-document"
            )
            factual_binding["active"] = False

            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="fixture-run",
            )

        metrics = result["measurements"]["exact"]
        self.assertEqual(metrics["citation_page_correct_hits"], 0)
        self.assertEqual(metrics["citation_page_evaluated_hits"], 1)
        self.assertEqual(metrics["citation_page_failures"], [{
            "query_id": "F01",
            "rank": 1,
            "reasons": ["SOURCE_BINDING_MISMATCH"],
        }])

    def test_source_url_tuple_accepts_relative_urls_and_rejects_malformed_queries(
        self,
    ) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            clear_results_except(exact, "F01")
            hit = next(
                item for item in exact["queries"] if item["id"] == "F01"
            )["results"][0]
            hit["source_url"] = (
                "/sources/factual-document/versions/1?page=1"
            )

            relative_result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="relative-url-run",
            )
            self.assertEqual(
                relative_result["measurements"]["exact"][
                    "citation_page_correctness"
                ],
                1.0,
            )

            hit["source_url"] = "/sources/factual-document/versions/1?page"
            malformed_result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="malformed-url-run",
            )

        self.assertEqual(
            malformed_result["measurements"]["exact"]["citation_page_failures"],
            [{
                "query_id": "F01",
                "rank": 1,
                "reasons": ["SOURCE_URL_TUPLE_MISMATCH"],
            }],
        )

    def test_absolute_source_url_is_bound_to_the_expected_origin(self) -> None:
        module = load_module()
        hit = {
            "document_id": "factual-document",
            "version": 1,
            "page": 3,
            "source_url": (
                "https://syncbase.example/sources/"
                "factual-document/versions/1?page=3"
            ),
        }
        self.assertTrue(
            module.citation_source_url_matches(hit, "https://syncbase.example")
        )
        hit["source_url"] = "/sources/factual-document/versions/1?page=3"
        self.assertTrue(
            module.citation_source_url_matches(hit, "https://syncbase.example")
        )
        for wrong_url in (
            "https://evil.invalid/sources/factual-document/versions/1?page=3",
            "http://syncbase.example/sources/factual-document/versions/1?page=3",
            "https://syncbase.example:444/sources/factual-document/versions/1?page=3",
            "https://user@syncbase.example/sources/factual-document/versions/1?page=3",
        ):
            with self.subTest(source_url=wrong_url):
                hit["source_url"] = wrong_url
                self.assertFalse(
                    module.citation_source_url_matches(
                        hit, "https://syncbase.example"
                    )
                )

    def test_raw_pdf_hash_check_is_independent_from_pdf_parsing(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            clear_results_except(exact, "F01")
            binding = next(
                binding
                for binding in exact["source_bindings"]
                if binding["document_id"] == "factual-document"
            )
            hit = next(
                item for item in exact["queries"] if item["id"] == "F01"
            )["results"][0]
            invalid_pdf = b"sealed bytes that are not a readable PDF"
            invalid_sha256 = hashlib.sha256(invalid_pdf).hexdigest()
            (evidence_root / binding["raw_pdf_artifact"]).write_bytes(invalid_pdf)
            binding["source_sha256"] = invalid_sha256
            binding["raw_pdf_sha256"] = invalid_sha256
            hit["source_sha256"] = invalid_sha256

            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="unreadable-pdf-run",
            )

        self.assertEqual(
            result["measurements"]["exact"]["citation_page_failures"],
            [{
                "query_id": "F01",
                "rank": 1,
                "reasons": ["PAGE_OUT_OF_RANGE", "SNIPPET_NOT_ON_CITED_PAGE"],
            }],
        )

    def test_evidence_root_rejects_a_symlink_escape_even_when_bytes_match(
        self,
    ) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            exact = observations(module, dataset, evidence_root)
            clear_results_except(exact, "F01")
            binding = next(
                binding
                for binding in exact["source_bindings"]
                if binding["document_id"] == "factual-document"
            )
            outside_pdf = root / "outside.pdf"
            outside_pdf.write_bytes(fixture_pdf_bytes("factual", 20))
            artifact = evidence_root / binding["raw_pdf_artifact"]
            artifact.unlink()
            artifact.symlink_to(outside_pdf)

            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="symlink-escape-run",
            )

        self.assertEqual(
            result["measurements"]["exact"]["citation_page_failures"],
            [{
                "query_id": "F01",
                "rank": 1,
                "reasons": [
                    "RAW_PDF_SHA256_MISMATCH",
                    "PAGE_OUT_OF_RANGE",
                    "SNIPPET_NOT_ON_CITED_PAGE",
                ],
            }],
        )

    def test_each_provenance_check_emits_its_fixed_failure_reason(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        cases = {
            "runtime binding": ["SOURCE_BINDING_MISMATCH"],
            "raw PDF hash": ["RAW_PDF_SHA256_MISMATCH"],
            "page range": ["PAGE_OUT_OF_RANGE", "SNIPPET_NOT_ON_CITED_PAGE"],
            "source URL": ["SOURCE_URL_TUPLE_MISMATCH"],
        }
        for case, expected_reasons in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                evidence_root = Path(directory)
                exact = observations(module, dataset, evidence_root)
                clear_results_except(exact, "F01")
                hit = next(
                    item for item in exact["queries"] if item["id"] == "F01"
                )["results"][0]
                binding = next(
                    item
                    for item in exact["source_bindings"]
                    if item["document_id"] == "factual-document"
                )
                if case == "runtime binding":
                    hit["version_id"] = "unbound-version"
                elif case == "raw PDF hash":
                    binding["raw_pdf_sha256"] = "0" * 64
                elif case == "page range":
                    hit["page"] = 21
                    hit["snippet"] = "no page exists here"
                    hit["source_url"] = (
                        "https://syncbase.example/sources/"
                        "factual-document/versions/1?page=21"
                    )
                elif case == "source URL":
                    hit["source_url"] = (
                        "/sources/factual-document/versions/1?page=1&page=2"
                    )

                result = module.evaluate(
                    dataset,
                    exact,
                    evidence_root=evidence_root,
                    started_at="2026-08-25T00:00:01Z",
                    completed_at="2026-08-25T00:00:02Z",
                    run_id=f"{case}-run",
                )

                self.assertEqual(
                    result["measurements"]["exact"]["citation_page_failures"],
                    [{
                        "query_id": "F01",
                        "rank": 1,
                        "reasons": expected_reasons,
                    }],
                )

    def test_zero_returned_hits_has_non_vacuous_zero_citation_correctness(
        self,
    ) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            for item in list(exact["queries"]):
                set_results(exact, item["id"], [])

            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="zero-hit-run",
            )

        metrics = result["measurements"]["exact"]
        self.assertEqual(metrics["citation_page_correctness"], 0.0)
        self.assertEqual(metrics["citation_page_correct_hits"], 0)
        self.assertEqual(metrics["citation_page_evaluated_hits"], 0)
        self.assertEqual(metrics["citation_page_failure_count"], 0)
        self.assertEqual(metrics["citation_page_failures"], [])
        self.assertIn("citation_page_correctness", result["failed_gates"])

    def test_provenance_correct_irrelevant_hit_is_not_hidden_from_citation_metric(
        self,
    ) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            irrelevant_hit = copy.deepcopy(next(
                item for item in exact["queries"] if item["id"] == "V01"
            )["results"][0])
            for item in list(exact["queries"]):
                set_results(exact, item["id"], [])
            set_results(exact, "F01", [irrelevant_hit])

            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="irrelevant-hit-run",
            )

        metrics = result["measurements"]["exact"]
        self.assertEqual(metrics["citation_page_correctness"], 1.0)
        self.assertEqual(metrics["citation_page_correct_hits"], 1)
        self.assertEqual(metrics["citation_page_evaluated_hits"], 1)
        self.assertEqual(metrics["recall_at_5"], 0.0)
        self.assertEqual(metrics["mrr"], 0.0)
        self.assertNotIn("citation_page_correctness", result["failed_gates"])
        self.assertIn("recall_at_5", result["failed_gates"])
        self.assertIn("mrr", result["failed_gates"])

    def test_legacy_18_of_47_diagnostic_is_preserved_but_not_gating(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        legacy_counts = {
            "F01": (2, 3),
            "F02": (2, 5),
            "F03": (1, 3),
            "F04": (2, 5),
            "F05": (0, 0),
            "F06": (1, 1),
            "F07": (1, 2),
            "F08": (1, 4),
            "F09": (1, 2),
            "F10": (2, 5),
            "I01": (1, 4),
            "I02": (1, 5),
            "I03": (1, 2),
            "I04": (1, 1),
            "I05": (1, 5),
        }
        irrelevant_counts = {
            "F01": 2,
            "F03": 2,
            "F06": 4,
            "F08": 1,
            "I01": 1,
        }

        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            for item in list(exact["queries"]):
                set_results(exact, item["id"], [])
            bindings_by_document_version = {
                (binding["document_id"], binding["version"]): binding
                for binding in exact["source_bindings"]
            }
            factual_binding = bindings_by_document_version[
                ("factual-document", 1)
            ]
            irrelevant_binding = bindings_by_document_version[
                ("version-document", 2)
            ]

            def hit_for(binding: dict, *, page: int, rank: int, label: str) -> dict:
                return {
                    "rank": rank,
                    "document_id": binding["document_id"],
                    "version_id": binding["version_id"],
                    "source_sha256": binding["source_sha256"],
                    "version": binding["version"],
                    "page": page,
                    "score": 0.9,
                    "snippet": fixture_page_text(label, page),
                    "source_url": (
                        f"/sources/{binding['document_id']}/versions/"
                        f"{binding['version']}?page={page}"
                    ),
                }

            expected_pages = {
                query["id"]: query["expected"]["relevant"][0]["pages"][0]
                for query in dataset["queries"]
                if query["id"] in legacy_counts
            }
            for query_id, (correct_count, same_source_count) in legacy_counts.items():
                hits: list[dict] = []
                for _ in range(correct_count):
                    hits.append(hit_for(
                        factual_binding,
                        page=expected_pages[query_id],
                        rank=len(hits) + 1,
                        label="factual",
                    ))
                for _ in range(same_source_count - correct_count):
                    hits.append(hit_for(
                        factual_binding,
                        page=20,
                        rank=len(hits) + 1,
                        label="factual",
                    ))
                for _ in range(irrelevant_counts.get(query_id, 0)):
                    hits.append(hit_for(
                        irrelevant_binding,
                        page=1,
                        rank=len(hits) + 1,
                        label="version-v2",
                    ))
                set_results(exact, query_id, hits)

            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="legacy-golden-run",
            )

        metrics = result["measurements"]["exact"]
        self.assertEqual(
            metrics["legacy_same_source_version_page_correct_hits"], 18
        )
        self.assertEqual(
            metrics["legacy_same_source_version_page_evaluated_hits"], 47
        )
        self.assertEqual(
            metrics["legacy_same_source_version_page_precision_at_5"],
            0.382979,
        )
        self.assertEqual(metrics["citation_page_correct_hits"], 57)
        self.assertEqual(metrics["citation_page_evaluated_hits"], 57)
        self.assertEqual(metrics["citation_page_correctness"], 1.0)
        self.assertEqual(metrics["citation_page_failure_count"], 0)
        self.assertNotIn(
            "legacy_same_source_version_page_precision_at_5",
            result["failed_gates"],
        )
        self.assertNotIn("citation_page_correctness", result["failed_gates"])

    def test_evaluate_cli_requires_and_uses_an_explicit_evidence_root(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            exact = observations(module, dataset, evidence_root)
            dataset_path = root / "dataset.json"
            exact_path = root / "exact.json"
            output_path = root / "result.json"
            dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
            exact_path.write_text(json.dumps(exact), encoding="utf-8")
            common_arguments = [
                "evaluate",
                str(dataset_path),
                str(exact_path),
                "--output",
                str(output_path),
                "--run-id",
                "cli-fixture-run",
            ]

            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    module.parse_args(common_arguments)

            arguments = module.parse_args([
                *common_arguments,
                "--evidence-root",
                str(evidence_root),
            ])
            self.assertEqual(arguments.evidence_root, evidence_root)
            with contextlib.redirect_stdout(io.StringIO()):
                return_code = module.main([
                    *common_arguments,
                    "--evidence-root",
                    str(evidence_root),
                ])

            self.assertEqual(return_code, 0)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["overall_result"], "PASS")
            self.assertEqual(
                result["inputs"]["metric_contract_version"],
                "round1-citation-provenance-v1",
            )

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

    def test_candidate_draft_has_real_factual_and_identifier_ground_truth(self) -> None:
        module = load_module()
        draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            module.validate_dataset(
                draft, require_frozen=False, allow_pending=True
            ),
            [],
        )
        candidate_queries = [
            query
            for query in draft["queries"]
            if query["category"] in {"factual_paraphrase", "exact_identifier"}
        ]
        self.assertEqual(len(candidate_queries), 15)
        for query in candidate_queries:
            target = query["expected"]["relevant"][0]
            self.assertRegex(target["source_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(target["pages"])
            self.assertEqual(len(query["candidate_evidence"]), 1)

    def test_candidate_draft_source_hashes_pages_and_excerpts_match(self) -> None:
        module = load_module()
        draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(module.validate_draft_sources(draft, WORKSPACE_ROOT), [])

    def test_factual_and_identifier_ground_truth_uses_active_ready_v2(self) -> None:
        draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        ready_plans = [
            plan
            for plan in draft["version_fixture_plans"]
            if plan["status"] == "READY"
        ]
        plans_by_v2 = {plan["v2_source_sha256"]: plan for plan in ready_plans}
        ready_v1_hashes = {plan["v1_source_sha256"] for plan in ready_plans}
        rebound_ids: set[str] = set()

        for query in draft["queries"]:
            if query["category"] not in {
                "factual_paraphrase",
                "exact_identifier",
            }:
                continue
            evidence = query["candidate_evidence"][0]
            target = query["expected"]["relevant"][0]
            self.assertNotIn(evidence["source_sha256"], ready_v1_hashes)
            self.assertNotIn(target["source_sha256"], ready_v1_hashes)
            plan = plans_by_v2.get(evidence["source_sha256"])
            if plan is None:
                continue
            rebound_ids.add(query["id"])
            self.assertEqual(evidence["source_file"], plan["v2_source_file"])
            self.assertEqual(target["source_sha256"], plan["v2_source_sha256"])
            self.assertEqual(target["version"], 2)
            self.assertEqual(target["pages"], [evidence["page"]])
            self.assertLess(evidence["page"], plan["v2_page"])
            self.assertEqual(query["expected"]["forbidden"], [])

        self.assertEqual(
            rebound_ids,
            {"F01", "F02", "F03", "F09", "F10", "I01", "I02", "I03", "I04"},
        )

    def test_validate_draft_rejects_factual_target_left_on_ready_v1(self) -> None:
        module = load_module()
        stale = copy.deepcopy(json.loads(DRAFT_PATH.read_text(encoding="utf-8")))
        plan = stale["version_fixture_plans"][0]
        query = next(item for item in stale["queries"] if item["id"] == "F01")
        page = query["candidate_evidence"][0]["page"]
        excerpt = query["candidate_evidence"][0]["supporting_excerpt"]
        query["candidate_evidence"] = [
            {
                "source_file": plan["base_source"]["source_file"],
                "source_sha256": plan["v1_source_sha256"],
                "page": page,
                "supporting_excerpt": excerpt,
            }
        ]
        query["expected"]["relevant"] = [
            {
                "source_sha256": plan["v1_source_sha256"],
                "version": 1,
                "pages": [page],
            }
        ]

        errors = module.validate_dataset(
            stale, require_frozen=False, allow_pending=True
        )
        self.assertTrue(
            any(
                "F01" in error and "READY fixture V1" in error
                for error in errors
            ),
            errors,
        )

    def test_freeze_rejects_pending_rc_bindings_human_review_and_v2(self) -> None:
        module = load_module()
        draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "APPROVED before freeze"):
            module.freeze_dataset(draft, frozen_at="2026-08-25T00:00:00Z")

    def test_release_freeze_revalidates_source_bytes(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "source PDF does not exist"):
                module.release_freeze_dataset(
                    complete_draft(),
                    frozen_at="2026-08-25T00:00:00Z",
                    source_root=Path(directory),
                )

    def test_release_freeze_rejects_changed_source_hash_and_excerpt(self) -> None:
        module = load_module()
        original = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
        factual = next(
            query
            for query in original["queries"]
            if query["category"] == "factual_paraphrase"
        )

        changed_hash = copy.deepcopy(original)
        changed_hash_query = next(
            query
            for query in changed_hash["queries"]
            if query["id"] == factual["id"]
        )
        changed_hash_query["candidate_evidence"][0]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source SHA-256 does not match"):
            module.release_freeze_dataset(
                changed_hash,
                frozen_at="2026-08-25T00:00:00Z",
                source_root=WORKSPACE_ROOT,
            )

        changed_excerpt = copy.deepcopy(original)
        changed_excerpt_query = next(
            query
            for query in changed_excerpt["queries"]
            if query["id"] == factual["id"]
        )
        changed_excerpt_query["candidate_evidence"][0]["supporting_excerpt"] = (
            "THIS EXCERPT DOES NOT EXIST ON THE SEALED PAGE"
        )
        with self.assertRaisesRegex(
            ValueError, "supporting excerpt was not extracted from the page"
        ):
            module.release_freeze_dataset(
                changed_excerpt,
                frozen_at="2026-08-25T00:00:00Z",
                source_root=WORKSPACE_ROOT,
            )

    def test_exclusive_atomic_json_write_refuses_an_existing_output(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "frozen.json"
            output.write_text("do-not-overwrite", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                module.write_json_exclusive_atomic(output, {"status": "FROZEN"})
            self.assertEqual(output.read_text(encoding="utf-8"), "do-not-overwrite")

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
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            result = module.evaluate(
                dataset,
                observations(
                    module,
                    dataset,
                    evidence_root,
                    one_no_answer_false_positive=True,
                ),
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="fixture-run",
            )
        metrics = result["measurements"]["exact"]
        self.assertEqual(result["overall_result"], "PASS")
        self.assertEqual(metrics["recall_at_5"], 1.0)
        self.assertEqual(metrics["mrr"], 1.0)
        self.assertEqual(metrics["citation_page_correctness"], 1.0)
        self.assertEqual(metrics["citation_page_correct_hits"], 21)
        self.assertEqual(metrics["citation_page_evaluated_hits"], 21)
        self.assertEqual(metrics["superseded_version_leakage"], 0)
        self.assertEqual(metrics["no_answer_false_positive_rate"], 0.1)
        self.assertEqual(metrics["search_latency_ms_p50"], 15)
        self.assertEqual(metrics["search_latency_ms_p95"], 29)

    def test_any_superseded_version_leakage_fails_the_release_gate(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            version_query = next(
                item for item in exact["queries"] if item["id"] == "V01"
            )
            forbidden = next(
                query["expected"]["forbidden"][0]
                for query in dataset["queries"]
                if query["id"] == "V01"
            )
            version_query["results"][0]["rank"] = 2
            version_query["results"].insert(0, {
                "rank": 1,
                "document_id": "version-document",
                "version_id": "version-version-1",
                "source_sha256": forbidden["source_sha256"],
                "version": forbidden["version"],
                "page": 1,
                "score": 0.95,
                "snippet": fixture_page_text("version-v1", 1),
                "source_url": (
                    "https://syncbase.example/sources/"
                    "version-document/versions/1?page=1"
                ),
            })
            result = module.evaluate(
                dataset,
                exact,
                evidence_root=evidence_root,
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
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            no_answer = next(
                item for item in exact["queries"] if item["id"] == "N01"
            )
            no_answer["grounding_status"] = "SUPPORTED"
            with self.assertRaisesRegex(ValueError, "grounding_status"):
                module.evaluate(
                    dataset,
                    exact,
                    evidence_root=evidence_root,
                    started_at="2026-08-25T00:00:01Z",
                    completed_at="2026-08-25T00:00:02Z",
                    run_id="fixture-run",
                )

    def test_insufficient_evidence_requires_an_explicit_reason(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            no_answer = next(
                item for item in exact["queries"] if item["id"] == "N01"
            )
            no_answer.pop("grounding_reason")
            with self.assertRaisesRegex(ValueError, "grounding_reason"):
                module.evaluate(
                    dataset,
                    exact,
                    evidence_root=evidence_root,
                    started_at="2026-08-25T00:00:01Z",
                    completed_at="2026-08-25T00:00:02Z",
                    run_id="fixture-run",
                )

    def test_ann_requires_recall_degradation_no_greater_than_point_zero_two(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            ann = observations(module, dataset, evidence_root)
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
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="fixture-run",
            )
        self.assertEqual(result["overall_result"], "FAIL")
        self.assertAlmostEqual(
            result["measurements"]["ann_recall_at_5_degradation"], 0.1
        )

    def test_ann_uses_the_same_citation_provenance_contract_as_exact(self) -> None:
        module = load_module()
        dataset = module.freeze_dataset(
            complete_draft(), frozen_at="2026-08-25T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_root = Path(directory)
            exact = observations(module, dataset, evidence_root)
            ann = observations(module, dataset, evidence_root)
            ann["retrieval_mode"] = "ann"
            ann_hit = next(
                item for item in ann["queries"] if item["id"] == "F01"
            )["results"][0]
            ann_hit["snippet"] = fixture_page_text("factual", 2)

            result = module.evaluate(
                dataset,
                exact,
                ann_observations=ann,
                evidence_root=evidence_root,
                started_at="2026-08-25T00:00:01Z",
                completed_at="2026-08-25T00:00:02Z",
                run_id="ann-citation-run",
            )

        self.assertEqual(
            result["measurements"]["exact"]["citation_page_correctness"], 1.0
        )
        self.assertEqual(
            result["measurements"]["ann"]["citation_page_correctness"], 0.95
        )
        self.assertEqual(
            result["measurements"]["ann"]["citation_page_failures"],
            [{
                "query_id": "F01",
                "rank": 1,
                "reasons": ["SNIPPET_NOT_ON_CITED_PAGE"],
            }],
        )
        self.assertIn("ann.citation_page_correctness", result["failed_gates"])


if __name__ == "__main__":
    unittest.main()

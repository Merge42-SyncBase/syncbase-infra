#!/usr/bin/env python3
"""Freeze and evaluate the evidence-bound 30-query Round-1 retrieval set."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_COUNTS = {
    "factual_paraphrase": 10,
    "exact_identifier": 5,
    "version_sensitive": 5,
    "no_answer": 10,
}
THRESHOLDS = {
    "recall_at_5_min": 0.85,
    "mrr_min": 0.75,
    "citation_page_correctness_min": 1.0,
    "superseded_version_leakage_max": 0,
    "no_answer_false_positive_rate_max": 0.10,
    "ann_recall_at_5_degradation_max": 0.02,
}
HASH = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_IDS = {"frontend", "embedding", "was", "infra", "mcp"}
BINDING_HASHES = {
    "corpus_sha256",
    "model_sha256",
    "tokenizer_sha256",
    "profile_sha256",
    "database_identity_sha256",
    "source_release_sha256",
}
GROUNDING_REASONS = {
    "NO_HITS_ABOVE_POLICY",
    "ONLY_INACTIVE_VERSION_MATCHED",
    "SOURCE_UNAVAILABLE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_sha256(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_sha256(dataset: dict) -> str:
    content = copy.deepcopy(dataset)
    content.pop("dataset_sha256", None)
    return canonical_sha256(content)


def contains_placeholder(value: object) -> bool:
    if isinstance(value, str):
        normalized = value.strip().upper()
        return normalized.startswith(("TODO_", "REQUIRED_", "FILL_ME"))
    if isinstance(value, list):
        return any(contains_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(contains_placeholder(item) for item in value.values())
    return False


def validate_bindings(bindings: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(bindings, dict):
        return ["bindings must be an object"]
    for name in sorted(BINDING_HASHES):
        digest = bindings.get(name)
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            errors.append(f"bindings.{name} must be a SHA-256 digest")
    revisions = bindings.get("repository_revisions")
    if not isinstance(revisions, dict) or set(revisions) != REPOSITORY_IDS:
        errors.append("bindings.repository_revisions must contain the five canonical repositories")
    else:
        for repository_id, revision in revisions.items():
            if not isinstance(revision, str) or not FULL_SHA.fullmatch(revision):
                errors.append(
                    f"bindings.repository_revisions.{repository_id} must be a full SHA"
                )
    return errors


def validate_target(target: object, *, allow_pages: bool) -> list[str]:
    if not isinstance(target, dict):
        return ["target must be an object"]
    errors: list[str] = []
    if not isinstance(target.get("source_sha256"), str) or not HASH.fullmatch(
        target["source_sha256"]
    ):
        errors.append("target.source_sha256 must be a SHA-256 digest")
    if not isinstance(target.get("version"), int) or target["version"] < 1:
        errors.append("target.version must be a positive integer")
    if allow_pages:
        pages = target.get("pages")
        if not isinstance(pages, list) or not pages or any(
            not isinstance(page, int) or page < 1 for page in pages
        ):
            errors.append("target.pages must contain positive page numbers")
    return errors


def validate_dataset(dataset: object, *, require_frozen: bool) -> list[str]:
    if not isinstance(dataset, dict):
        return ["dataset must be a JSON object"]
    errors: list[str] = []
    if dataset.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if not isinstance(dataset.get("dataset_id"), str) or not dataset["dataset_id"]:
        errors.append("dataset_id must be non-empty")
    expected_status = "FROZEN" if require_frozen else "DRAFT"
    if dataset.get("status") != expected_status:
        errors.append(f"status must be {expected_status}")
    if dataset.get("thresholds") != THRESHOLDS:
        errors.append("thresholds differ from the accepted Round-1 contract")
    errors.extend(validate_bindings(dataset.get("bindings")))

    queries = dataset.get("queries")
    if not isinstance(queries, list):
        return errors + ["queries must be an array"]
    counts = Counter(
        query.get("category") for query in queries if isinstance(query, dict)
    )
    if dict(counts) != CATEGORY_COUNTS:
        errors.append(f"query categories must equal {CATEGORY_COUNTS}")
    identifiers: list[str] = []
    for position, query in enumerate(queries):
        label = f"queries[{position}]"
        if not isinstance(query, dict):
            errors.append(f"{label} must be an object")
            continue
        query_id = query.get("id")
        if not isinstance(query_id, str) or not query_id:
            errors.append(f"{label}.id must be non-empty")
        else:
            identifiers.append(query_id)
        if not isinstance(query.get("query"), str) or not query["query"].strip():
            errors.append(f"{label}.query must be non-empty")
        expected = query.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{label}.expected must be an object")
            continue
        no_answer = expected.get("no_answer")
        relevant = expected.get("relevant")
        forbidden = expected.get("forbidden")
        if not isinstance(no_answer, bool):
            errors.append(f"{label}.expected.no_answer must be boolean")
        if not isinstance(relevant, list):
            errors.append(f"{label}.expected.relevant must be an array")
            relevant = []
        if not isinstance(forbidden, list):
            errors.append(f"{label}.expected.forbidden must be an array")
            forbidden = []
        if no_answer and relevant:
            errors.append(f"{label} no-answer query cannot have relevant targets")
        if no_answer is False and not relevant:
            errors.append(f"{label} answerable query needs at least one relevant target")
        for target in relevant:
            errors.extend(f"{label}.{error}" for error in validate_target(target, allow_pages=True))
        for target in forbidden:
            errors.extend(f"{label}.{error}" for error in validate_target(target, allow_pages=False))
        if query.get("category") == "version_sensitive" and not forbidden:
            errors.append(f"{label} version-sensitive query needs a forbidden version")
    if len(identifiers) != len(set(identifiers)):
        errors.append("query ids must be unique")
    if contains_placeholder(dataset):
        errors.append("dataset contains an unworked placeholder")
    if require_frozen:
        if not isinstance(dataset.get("frozen_at"), str) or not dataset["frozen_at"].endswith("Z"):
            errors.append("frozen_at must be a UTC timestamp")
        if dataset.get("dataset_sha256") != dataset_sha256(dataset):
            errors.append("dataset_sha256 does not match the frozen content")
    return errors


def freeze_dataset(dataset: dict, *, frozen_at: str) -> dict:
    errors = validate_dataset(dataset, require_frozen=False)
    if errors:
        raise ValueError("dataset cannot be frozen: " + "; ".join(errors))
    frozen = copy.deepcopy(dataset)
    frozen["status"] = "FROZEN"
    frozen["frozen_at"] = frozen_at
    frozen.pop("dataset_sha256", None)
    frozen["dataset_sha256"] = dataset_sha256(frozen)
    frozen_errors = validate_dataset(frozen, require_frozen=True)
    if frozen_errors:
        raise ValueError("frozen dataset is invalid: " + "; ".join(frozen_errors))
    return frozen


def relevant_hit(hit: dict, target: dict, *, require_page: bool) -> bool:
    if hit.get("source_sha256") != target.get("source_sha256"):
        return False
    if hit.get("version") != target.get("version"):
        return False
    return not require_page or hit.get("page") in target.get("pages", [])


def nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def validate_observations(dataset: dict, observations: object, expected_mode: str) -> list[str]:
    if not isinstance(observations, dict):
        return ["observations must be a JSON object"]
    errors: list[str] = []
    if observations.get("schema_version") != "1.0":
        errors.append("observations.schema_version must be 1.0")
    if observations.get("dataset_sha256") != dataset["dataset_sha256"]:
        errors.append("observations dataset_sha256 does not match")
    if observations.get("retrieval_mode") != expected_mode:
        errors.append(f"retrieval_mode must be {expected_mode}")
    if observations.get("bindings") != dataset["bindings"]:
        errors.append("observation bindings differ from the frozen dataset")
    query_results = observations.get("queries")
    if not isinstance(query_results, list):
        return errors + ["observations.queries must be an array"]
    expected_ids = [query["id"] for query in dataset["queries"]]
    observed_ids = [
        item.get("id") for item in query_results if isinstance(item, dict)
    ]
    if observed_ids != expected_ids:
        errors.append("observations must contain every query exactly once in frozen order")
    for position, item in enumerate(query_results):
        if not isinstance(item, dict):
            errors.append(f"observations.queries[{position}] must be an object")
            continue
        latency = item.get("latency_ms")
        if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
            errors.append(f"observations.queries[{position}].latency_ms must be non-negative")
        hits = item.get("results")
        if not isinstance(hits, list):
            errors.append(f"observations.queries[{position}].results must be an array")
            continue
        grounding_status = item.get("grounding_status")
        expected_grounding_status = "SUPPORTED" if hits else "INSUFFICIENT_EVIDENCE"
        if grounding_status != expected_grounding_status:
            errors.append(
                f"observations.queries[{position}].grounding_status must be "
                f"{expected_grounding_status} when results are "
                f"{'present' if hits else 'empty'}"
            )
        grounding_reason = item.get("grounding_reason")
        if not hits and grounding_reason not in GROUNDING_REASONS:
            errors.append(
                f"observations.queries[{position}].grounding_reason must be one of "
                f"{sorted(GROUNDING_REASONS)} for insufficient evidence"
            )
        if hits and grounding_reason is not None:
            errors.append(
                f"observations.queries[{position}].grounding_reason must be null "
                "when evidence is grounded"
            )
        for rank, hit in enumerate(hits, start=1):
            if not isinstance(hit, dict):
                errors.append(f"query {item.get('id')} hit {rank} must be an object")
                continue
            if not isinstance(hit.get("source_sha256"), str) or not HASH.fullmatch(
                hit["source_sha256"]
            ):
                errors.append(f"query {item.get('id')} hit {rank} has invalid source hash")
            if not isinstance(hit.get("version"), int) or hit["version"] < 1:
                errors.append(f"query {item.get('id')} hit {rank} has invalid version")
            if not isinstance(hit.get("page"), int) or hit["page"] < 1:
                errors.append(f"query {item.get('id')} hit {rank} has invalid page")
            score = hit.get("score")
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not 0.0 <= score <= 1.0
            ):
                errors.append(f"query {item.get('id')} hit {rank} has invalid score")
    return errors


def calculate_metrics(dataset: dict, observations: dict) -> dict:
    by_id = {item["id"]: item for item in observations["queries"]}
    recall_values: list[float] = []
    reciprocal_ranks: list[float] = []
    citation_total = 0
    citation_correct = 0
    superseded_leakage = 0
    no_answer_queries = 0
    no_answer_false_positives = 0
    latencies: list[float] = []

    for query in dataset["queries"]:
        observed = by_id[query["id"]]
        hits = observed["results"]
        top_five = hits[:5]
        latencies.append(observed["latency_ms"])
        expected = query["expected"]
        if expected["no_answer"]:
            no_answer_queries += 1
            if hits:
                no_answer_false_positives += 1
        else:
            targets = expected["relevant"]
            retrieved = sum(
                any(relevant_hit(hit, target, require_page=True) for hit in top_five)
                for target in targets
            )
            recall_values.append(retrieved / len(targets))
            first_rank = next(
                (
                    rank
                    for rank, hit in enumerate(hits, start=1)
                    if any(relevant_hit(hit, target, require_page=True) for target in targets)
                ),
                None,
            )
            reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
            for hit in top_five:
                source_version_targets = [
                    target
                    for target in targets
                    if relevant_hit(hit, target, require_page=False)
                ]
                if source_version_targets:
                    citation_total += 1
                    if any(
                        relevant_hit(hit, target, require_page=True)
                        for target in source_version_targets
                    ):
                        citation_correct += 1
        for hit in hits:
            if any(
                relevant_hit(hit, forbidden, require_page=False)
                for forbidden in expected["forbidden"]
            ):
                superseded_leakage += 1

    citation_correctness = (
        citation_correct / citation_total if citation_total else 0.0
    )
    return {
        "query_count": len(dataset["queries"]),
        "recall_at_5": round(sum(recall_values) / len(recall_values), 6),
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6),
        "citation_page_correctness": round(citation_correctness, 6),
        "superseded_version_leakage": superseded_leakage,
        "no_answer_false_positive_rate": round(
            no_answer_false_positives / no_answer_queries, 6
        ),
        "search_latency_ms_p50": nearest_rank(latencies, 0.50),
        "search_latency_ms_p95": nearest_rank(latencies, 0.95),
    }


def metric_failures(metrics: dict, thresholds: dict) -> list[str]:
    failures: list[str] = []
    if metrics["recall_at_5"] < thresholds["recall_at_5_min"]:
        failures.append("recall_at_5")
    if metrics["mrr"] < thresholds["mrr_min"]:
        failures.append("mrr")
    if metrics["citation_page_correctness"] < thresholds["citation_page_correctness_min"]:
        failures.append("citation_page_correctness")
    if metrics["superseded_version_leakage"] > thresholds["superseded_version_leakage_max"]:
        failures.append("superseded_version_leakage")
    if (
        metrics["no_answer_false_positive_rate"]
        > thresholds["no_answer_false_positive_rate_max"]
    ):
        failures.append("no_answer_false_positive_rate")
    return failures


def evaluate(
    dataset: dict,
    exact_observations: dict,
    *,
    ann_observations: dict | None = None,
    started_at: str,
    completed_at: str,
    run_id: str,
    artifact_hashes: dict[str, str] | None = None,
) -> dict:
    errors = validate_dataset(dataset, require_frozen=True)
    errors.extend(validate_observations(dataset, exact_observations, "exact"))
    if ann_observations is not None:
        errors.extend(validate_observations(dataset, ann_observations, "ann"))
    if errors:
        raise ValueError("evaluation inputs are invalid: " + "; ".join(errors))

    exact_metrics = calculate_metrics(dataset, exact_observations)
    failures = metric_failures(exact_metrics, dataset["thresholds"])
    measurements: dict[str, object] = {
        "thresholds": dataset["thresholds"],
        "exact": exact_metrics,
    }
    if ann_observations is not None:
        ann_metrics = calculate_metrics(dataset, ann_observations)
        degradation = round(
            exact_metrics["recall_at_5"] - ann_metrics["recall_at_5"], 6
        )
        measurements["ann"] = ann_metrics
        measurements["ann_recall_at_5_degradation"] = degradation
        failures.extend(f"ann.{name}" for name in metric_failures(ann_metrics, dataset["thresholds"]))
        if degradation > dataset["thresholds"]["ann_recall_at_5_degradation_max"]:
            failures.append("ann_recall_at_5_degradation")

    bindings = dataset["bindings"]
    overall_result = "PASS" if not failures else "FAIL"
    return {
        "schema_version": "1.0",
        "task_id": "C6_RETRIEVAL_EVALUATION",
        "run_id": run_id,
        "overall_result": overall_result,
        "evidence_grade": "BENCHMARK",
        "started_at": started_at,
        "completed_at": completed_at,
        "repository_revisions": bindings["repository_revisions"],
        "inputs": {
            "dataset_sha256": dataset["dataset_sha256"],
            **{name: bindings[name] for name in sorted(BINDING_HASHES)},
            "exact_retrieval_mode": exact_observations["retrieval_mode"],
            "ann_retrieval_mode": ann_observations["retrieval_mode"]
            if ann_observations is not None
            else None,
        },
        "measurements": measurements,
        "artifact_hashes": artifact_hashes or {},
        "failed_gates": failures,
        "failure_reason": None
        if overall_result == "PASS"
        else "benchmark thresholds failed: " + ", ".join(failures),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze_parser = subparsers.add_parser("freeze", help="freeze completed ground truth")
    freeze_parser.add_argument("draft", type=Path)
    freeze_parser.add_argument("--output", type=Path, required=True)
    freeze_parser.add_argument("--frozen-at", default=None)
    evaluate_parser = subparsers.add_parser("evaluate", help="score captured retrieval results")
    evaluate_parser.add_argument("dataset", type=Path)
    evaluate_parser.add_argument("exact", type=Path)
    evaluate_parser.add_argument("--ann", type=Path)
    evaluate_parser.add_argument("--output", type=Path, required=True)
    evaluate_parser.add_argument("--run-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        if arguments.command == "freeze":
            draft = json.loads(arguments.draft.read_text(encoding="utf-8"))
            frozen = freeze_dataset(draft, frozen_at=arguments.frozen_at or utc_now())
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(
                json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(arguments.output)
            return 0

        started_at = utc_now()
        dataset = json.loads(arguments.dataset.read_text(encoding="utf-8"))
        exact = json.loads(arguments.exact.read_text(encoding="utf-8"))
        ann = json.loads(arguments.ann.read_text(encoding="utf-8")) if arguments.ann else None
        artifacts = {
            "frozen-dataset.json": file_sha256(arguments.dataset),
            "exact-observations.json": file_sha256(arguments.exact),
        }
        if arguments.ann:
            artifacts["ann-observations.json"] = file_sha256(arguments.ann)
        result = evaluate(
            dataset,
            exact,
            ann_observations=ann,
            started_at=started_at,
            completed_at=utc_now(),
            run_id=arguments.run_id,
            artifact_hashes=artifacts,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(arguments.output)
        return 0 if result["overall_result"] == "PASS" else 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

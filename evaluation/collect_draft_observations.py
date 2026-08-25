#!/usr/bin/env python3
"""Collect local-only REST observations for the unfinished Round-1 draft.

This program deliberately does not calculate benchmark metrics or emit a PASS/FAIL
result.  Frozen/release evidence must be collected by a separate, release-bound
runner only after the human, fixture, and release-binding gates are complete.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPCookieProcessor,
    Request,
    build_opener,
)


ARTIFACT_STATUS = "DRAFT_LOCAL_ONLY_NOT_RELEASE_EVIDENCE"
SELECTED_CATEGORIES = {"factual_paraphrase", "exact_identifier", "no_answer"}
VERSION_CATEGORY = "version_sensitive"
DEFAULT_DIAGNOSTIC_SCOPE = "25_QUERY_DRAFT_DIAGNOSTIC"
READY_VERSION_DIAGNOSTIC_SCOPE = "30_QUERY_DRAFT_DIAGNOSTIC"
GROUNDING_STATUSES = {"SUPPORTED", "INSUFFICIENT_EVIDENCE"}
GROUNDING_REASONS = {
    "NO_HITS_ABOVE_POLICY",
    "ONLY_INACTIVE_VERSION_MATCHED",
    "SOURCE_UNAVAILABLE",
}
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024


class CollectionError(ValueError):
    """A safely reportable collection failure that never contains credentials."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        _request: Request,
        _file_pointer: Any,
        _code: int,
        _message: str,
        _headers: Any,
        _new_url: str,
    ) -> None:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_evaluator() -> Any:
    path = Path(__file__).with_name("evaluate_retrieval.py")
    spec = importlib.util.spec_from_file_location("round1_retrieval_evaluator", path)
    if spec is None or spec.loader is None:
        raise CollectionError("draft validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise CollectionError(
            "base URL must be an uncredentialed loopback HTTP origin"
        )
    try:
        _ = parsed.port
    except ValueError as error:
        raise CollectionError("base URL port is invalid") from error
    return value.rstrip("/")


def infer_source_root(dataset_path: Path, dataset: dict[str, Any]) -> Path:
    source_files = [
        evidence.get("source_file")
        for query in dataset.get("queries", [])
        if isinstance(query, dict)
        for evidence in query.get("candidate_evidence", []) or []
        if isinstance(evidence, dict) and isinstance(evidence.get("source_file"), str)
    ]
    for parent in [dataset_path.resolve().parent, *dataset_path.resolve().parents]:
        if source_files and all((parent / source).is_file() for source in source_files):
            return parent
    raise CollectionError("source root could not be inferred from the draft")


def release_blockers(dataset: dict[str, Any]) -> list[str]:
    blockers = ["dataset_status:DRAFT"]
    if dataset.get("dataset_role") == "CALIBRATION":
        blockers.append("dataset_role:CALIBRATION_ONLY")
    human = dataset.get("human_verification")
    if not isinstance(human, dict) or human.get("status") != "APPROVED":
        blockers.append("human_verification:PENDING")

    bindings = dataset.get("bindings")
    bindings_pending = not isinstance(bindings, dict)
    if isinstance(bindings, dict):
        required = {
            "corpus_sha256",
            "model_sha256",
            "tokenizer_sha256",
            "profile_sha256",
            "database_identity_sha256",
            "source_release_sha256",
        }
        bindings_pending = any(bindings.get(name) is None for name in required)
        revisions = bindings.get("repository_revisions")
        bindings_pending = bindings_pending or not isinstance(revisions, dict) or any(
            revisions.get(name) is None
            for name in {"frontend", "embedding", "was", "infra", "mcp"}
        )
    if bindings_pending:
        blockers.append("release_bindings:PENDING")

    plans = dataset.get("version_fixture_plans")
    if not isinstance(plans, list) or any(
        not isinstance(plan, dict) or plan.get("status") != "READY"
        for plan in plans
    ):
        blockers.append("version_fixtures:PENDING")
    return blockers


def select_diagnostic_queries(
    dataset: dict[str, Any], *, include_ready_version_cases: bool
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, int], str]:
    """Select the fixed DRAFT scope without converting observations to evidence."""

    queries = dataset["queries"]
    version_queries = [
        query for query in queries if query["category"] == VERSION_CATEGORY
    ]
    if include_ready_version_cases:
        plans = {
            plan["id"]: plan
            for plan in dataset["version_fixture_plans"]
            if isinstance(plan, dict) and isinstance(plan.get("id"), str)
        }
        readiness_errors: list[str] = []
        for query in version_queries:
            plan = plans.get(query.get("fixture_plan_id"))
            if query.get("ground_truth_state") != "VERIFIED_VERSION_PAIR":
                readiness_errors.append(
                    f"{query['id']}:ground_truth_state="
                    f"{query.get('ground_truth_state', 'MISSING')}"
                )
            if plan is None or plan.get("status") != "READY":
                readiness_errors.append(
                    f"{query['id']}:fixture_plan_status="
                    f"{plan.get('status', 'MISSING') if plan else 'MISSING'}"
                )
        if len(version_queries) != 5:
            readiness_errors.append(
                f"version_query_count={len(version_queries)}"
            )
        if readiness_errors:
            raise CollectionError(
                "--include-ready-version-cases requires all five V queries to use "
                "VERIFIED_VERSION_PAIR with READY fixture plans; "
                + ", ".join(readiness_errors)
            )
        selected_categories = SELECTED_CATEGORIES | {VERSION_CATEGORY}
        skipped: list[dict[str, str]] = []
        scope = READY_VERSION_DIAGNOSTIC_SCOPE
        expected_counts = {
            "exact_identifier": 5,
            "factual_paraphrase": 10,
            "no_answer": 10,
            "version_sensitive": 5,
        }
    else:
        selected_categories = SELECTED_CATEGORIES
        skipped = [
            {
                "id": query["id"],
                "category": query["category"],
                "reason": "VERSION_CASE_EXCLUDED_FROM_DRAFT_DIAGNOSTIC",
            }
            for query in version_queries
        ]
        scope = DEFAULT_DIAGNOSTIC_SCOPE
        expected_counts = {
            "exact_identifier": 5,
            "factual_paraphrase": 10,
            "no_answer": 10,
        }

    selected = [
        query for query in queries if query["category"] in selected_categories
    ]
    selected_counts = {
        category: sum(query["category"] == category for query in selected)
        for category in sorted(selected_categories)
    }
    if selected_counts != expected_counts or (
        not include_ready_version_cases and len(skipped) != 5
    ):
        raise CollectionError(
            "draft diagnostic selection must be 10/5/10 by default or "
            "10/5/5/10 with explicitly included READY V cases"
        )
    return selected, skipped, selected_counts, scope


class LocalRestClient:
    def __init__(self, base_url: str, cookie_file: Path, timeout_seconds: float):
        self.base_url = normalize_local_base_url(base_url)
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(str(cookie_file), ignore_discard=True, ignore_expires=True)
        except (OSError, http.cookiejar.LoadError) as error:
            raise CollectionError("session cookie jar is unavailable or invalid") from error
        if not any(cookie.name == "syncbase_session" for cookie in jar):
            raise CollectionError("session cookie jar lacks a SyncBase session")
        self.opener = build_opener(HTTPCookieProcessor(jar), NoRedirect())
        self.timeout_seconds = timeout_seconds

    def _get(self, endpoint: str, *, label: str, maximum_bytes: int) -> bytes:
        request = Request(
            self.base_url + endpoint,
            headers={
                "Accept": "application/json, application/pdf",
                "User-Agent": "syncbase-round1-draft-diagnostic/1.0",
            },
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                content = response.read(maximum_bytes + 1)
                if len(content) > maximum_bytes:
                    raise CollectionError(f"{label} response exceeded the size limit")
                return content
        except HTTPError as error:
            raise CollectionError(f"{label} failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise CollectionError(f"{label} request failed") from error

    def json(self, endpoint: str, *, label: str) -> dict[str, Any]:
        content = self._get(endpoint, label=label, maximum_bytes=MAX_JSON_BYTES)
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CollectionError(f"{label} returned invalid JSON") from error
        if not isinstance(value, dict):
            raise CollectionError(f"{label} returned a non-object JSON value")
        return value

    def pdf(self, endpoint: str, *, label: str) -> bytes:
        return self._get(endpoint, label=label, maximum_bytes=MAX_PDF_BYTES)


def list_active_source_bindings(
    client: LocalRestClient,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    summaries: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while True:
        response = client.json(
            f"/api/v1/documents?{urlencode({'limit': limit, 'offset': offset})}",
            label="document listing",
        )
        documents = response.get("documents")
        if not isinstance(documents, list):
            raise CollectionError("document listing omitted the documents array")
        if any(not isinstance(document, dict) for document in documents):
            raise CollectionError("document listing contained an invalid document")
        summaries.extend(documents)
        if len(documents) < limit:
            break
        offset += len(documents)
        if offset > 10_000:
            raise CollectionError("document listing exceeded the diagnostic safety limit")

    bindings: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for summary in summaries:
        document_id = summary.get("id")
        active_version = summary.get("activeVersion")
        if not isinstance(document_id, str) or not document_id:
            errors.append({"document_id": "UNKNOWN", "error": "INVALID_DOCUMENT_ID"})
            continue
        if not isinstance(active_version, int) or isinstance(active_version, bool):
            continue
        encoded_id = quote(document_id, safe="")
        detail = client.json(
            f"/api/v1/documents/{encoded_id}",
            label="document detail",
        )
        versions = detail.get("versions")
        if not isinstance(versions, list):
            errors.append({"document_id": document_id, "error": "VERSIONS_NOT_ARRAY"})
            continue
        version = next(
            (
                item
                for item in versions
                if isinstance(item, dict)
                and item.get("versionNumber") == active_version
                and item.get("active") is True
            ),
            None,
        )
        if version is None:
            errors.append({"document_id": document_id, "error": "ACTIVE_VERSION_NOT_FOUND"})
            continue
        version_id = version.get("id")
        if not isinstance(version_id, str) or not version_id:
            errors.append({"document_id": document_id, "error": "INVALID_VERSION_ID"})
            continue
        try:
            content = client.pdf(
                f"/api/v1/documents/{encoded_id}/versions/{active_version}/raw.pdf",
                label="active Original",
            )
        except CollectionError as error:
            errors.append(
                {
                    "document_id": document_id,
                    "error": str(error).replace(" ", "_").upper(),
                }
            )
            continue
        bindings.append(
            {
                "source_sha256": sha256_bytes(content),
                "document_id": document_id,
                "document_name": str(detail.get("name", summary.get("name", ""))),
                "version_id": version_id,
                "version": active_version,
                "active": True,
                "page_count": version.get("pageCount"),
                "raw_pdf_path": (
                    f"/api/v1/documents/{encoded_id}/versions/"
                    f"{active_version}/raw.pdf"
                ),
            }
        )
    bindings.sort(
        key=lambda item: (
            item["source_sha256"], item["document_id"], item["version"]
        )
    )
    return bindings, errors


def search_contract_errors(
    response: dict[str, Any], requested_query: str
) -> list[str]:
    errors: list[str] = []
    if response.get("query") != requested_query:
        errors.append("QUERY_ECHO_MISMATCH")
    status = response.get("grounding_status")
    reason = response.get("grounding_reason")
    results = response.get("results")
    if status not in GROUNDING_STATUSES:
        errors.append("INVALID_GROUNDING_STATUS")
    if not isinstance(results, list):
        errors.append("RESULTS_NOT_ARRAY")
        return errors
    if status == "SUPPORTED" and (not results or reason is not None):
        errors.append("SUPPORTED_CONTRACT_VIOLATION")
    if status == "INSUFFICIENT_EVIDENCE" and (
        results or reason not in GROUNDING_REASONS
    ):
        errors.append("INSUFFICIENT_EVIDENCE_CONTRACT_VIOLATION")
    return errors


def normalize_hit(
    hit: object,
    source_lookup: dict[tuple[str, int], dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(hit, dict):
        return {"source_mapping_status": "INVALID_HIT"}, ["HIT_NOT_OBJECT"]
    errors: list[str] = []
    document_id = hit.get("document_id")
    version = hit.get("document_version")
    version_id = hit.get("version_id")
    mapping = (
        source_lookup.get((document_id, version))
        if isinstance(document_id, str)
        and isinstance(version, int)
        and not isinstance(version, bool)
        else None
    )
    if mapping is None:
        mapping_status = "UNMAPPED"
        source_sha256 = None
    elif mapping.get("version_id") != version_id:
        mapping_status = "VERSION_ID_MISMATCH"
        source_sha256 = mapping["source_sha256"]
    else:
        mapping_status = "MAPPED"
        source_sha256 = mapping["source_sha256"]

    rank = hit.get("rank")
    score = hit.get("score")
    page = hit.get("page_number")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        errors.append("INVALID_RANK")
    if (
        not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not 0 <= score <= 1
    ):
        errors.append("INVALID_SCORE")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("INVALID_VERSION")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        errors.append("INVALID_PAGE")
    if mapping_status != "MAPPED":
        errors.append(f"SOURCE_{mapping_status}")

    return (
        {
            "rank": rank,
            "score": score,
            "document_id": document_id,
            "document_name": hit.get("document_name"),
            "version_id": version_id,
            "version": version,
            "page": page,
            "snippet": hit.get("snippet"),
            "source_url": hit.get("source_url"),
            "source_sha256": source_sha256,
            "source_mapping_status": mapping_status,
        },
        errors,
    )


def collect_queries(
    client: LocalRestClient,
    queries: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_lookup = {
        (binding["document_id"], binding["version"]): binding
        for binding in bindings
    }
    observations: list[dict[str, Any]] = []
    for query in queries:
        started = time.perf_counter_ns()
        response = client.json(
            f"/api/v1/search?{urlencode({'q': query['query'], 'limit': 20})}",
            label=f"search {query['id']}",
        )
        latency_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        contract_errors = search_contract_errors(response, query["query"])
        results_value = response.get("results")
        raw_results = results_value if isinstance(results_value, list) else []
        normalized_results: list[dict[str, Any]] = []
        for position, raw_hit in enumerate(raw_results, start=1):
            hit, hit_errors = normalize_hit(raw_hit, source_lookup)
            normalized_results.append(hit)
            contract_errors.extend(
                f"HIT_{position}_{error}" for error in hit_errors
            )
        observations.append(
            {
                "id": query["id"],
                "category": query["category"],
                "query": query["query"],
                "expected_no_answer": query["expected"]["no_answer"],
                "http_status": 200,
                "latency_ms": latency_ms,
                "grounding_status": response.get("grounding_status"),
                "grounding_reason": response.get("grounding_reason"),
                "contract_errors": contract_errors,
                "results": normalized_results,
            }
        )
    return observations


def expected_source_hashes(queries: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            target["source_sha256"]
            for query in queries
            for target in query["expected"]["relevant"]
            if isinstance(target, dict)
            and isinstance(target.get("source_sha256"), str)
        }
    )


def expected_source_version_requirements(
    queries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], set[str]] = {}
    for query in queries:
        for target in query["expected"]["relevant"]:
            if not isinstance(target, dict):
                continue
            source_sha256 = target.get("source_sha256")
            version = target.get("version")
            if isinstance(source_sha256, str) and isinstance(version, int):
                grouped.setdefault((source_sha256, version), set()).add(query["id"])
    return [
        {
            "source_sha256": source_sha256,
            "expected_version": version,
            "query_ids": sorted(query_ids),
        }
        for (source_sha256, version), query_ids in sorted(grouped.items())
    ]


def expected_binding_status(
    query: dict[str, Any],
    binding_pairs: set[tuple[str, int]],
    mapped_hashes: set[str],
) -> str:
    if query["expected"]["no_answer"]:
        return "NOT_APPLICABLE_NO_ANSWER"
    targets = query["expected"]["relevant"]
    if any(target["source_sha256"] not in mapped_hashes for target in targets):
        return "SOURCE_UNMAPPED"
    if any(
        (target["source_sha256"], target["version"]) not in binding_pairs
        for target in targets
    ):
        return "SOURCE_PRESENT_WRONG_VERSION"
    return "BOUND"


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(value, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        Path(temporary_name).replace(path)
    except Exception:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session-cookie-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--purpose", choices=("diagnostic", "release"), default="diagnostic"
    )
    parser.add_argument(
        "--include-ready-version-cases",
        action="store_true",
        help=(
            "include V01-V05 only when every V query is VERIFIED_VERSION_PAIR "
            "and every linked fixture plan is READY"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        if arguments.timeout_seconds <= 0 or arguments.timeout_seconds > 60:
            raise CollectionError("timeout must be greater than zero and at most 60 seconds")
        base_url = normalize_local_base_url(arguments.base_url)
        try:
            dataset_bytes = arguments.dataset.read_bytes()
            dataset = json.loads(dataset_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CollectionError("draft dataset is unavailable or invalid JSON") from error
        if not isinstance(dataset, dict):
            raise CollectionError("draft dataset must be a JSON object")
        if dataset.get("status") != "DRAFT":
            raise CollectionError("collector accepts DRAFT input only; frozen use is refused")
        if dataset.get("dataset_role") == "PROSPECTIVE_HOLDOUT":
            raise CollectionError(
                "prospective holdout collection refused: freeze and use the separate "
                "release-bound runner; this DRAFT diagnostic collector is calibration-only"
            )
        if dataset.get("dataset_role") != "CALIBRATION":
            raise CollectionError(
                "collector accepts dataset_role=CALIBRATION only"
            )

        evaluator = load_evaluator()
        validation_errors = evaluator.validate_dataset(
            dataset, require_frozen=False, allow_pending=True
        )
        if validation_errors:
            raise CollectionError(
                "draft validation failed: " + "; ".join(validation_errors)
            )

        blockers = release_blockers(dataset)
        if arguments.purpose == "release":
            raise CollectionError(
                "release collection refused: this collector is DRAFT/local-only; "
                + ", ".join(blockers)
            )

        source_root = arguments.source_root or infer_source_root(
            arguments.dataset, dataset
        )
        validation_errors.extend(evaluator.validate_draft_sources(dataset, source_root))
        if validation_errors:
            raise CollectionError(
                "draft validation failed: " + "; ".join(validation_errors)
            )

        selected, skipped, selected_counts, diagnostic_scope = (
            select_diagnostic_queries(
                dataset,
                include_ready_version_cases=arguments.include_ready_version_cases,
            )
        )

        client = LocalRestClient(
            base_url, arguments.session_cookie_file, arguments.timeout_seconds
        )
        started_at = utc_now()
        source_bindings, source_mapping_errors = list_active_source_bindings(client)
        observations = collect_queries(client, selected, source_bindings)
        expected_hashes = expected_source_hashes(selected)
        mapped_hashes = {binding["source_sha256"] for binding in source_bindings}
        binding_pairs = {
            (binding["source_sha256"], binding["version"])
            for binding in source_bindings
        }
        versions_by_hash: dict[str, set[int]] = {}
        for binding in source_bindings:
            versions_by_hash.setdefault(binding["source_sha256"], set()).add(
                binding["version"]
            )
        requirements = expected_source_version_requirements(selected)
        missing_source_versions = [
            requirement
            for requirement in requirements
            if (
                requirement["source_sha256"],
                requirement["expected_version"],
            )
            not in binding_pairs
        ]
        version_mismatches = [
            {
                **requirement,
                "observed_active_versions": sorted(
                    versions_by_hash[requirement["source_sha256"]]
                ),
            }
            for requirement in missing_source_versions
            if requirement["source_sha256"] in versions_by_hash
        ]
        for query, observation in zip(selected, observations, strict=True):
            observation["expected_binding_status"] = expected_binding_status(
                query, binding_pairs, mapped_hashes
            )
        unmapped_expected = sorted(set(expected_hashes) - mapped_hashes)
        contract_mismatches = sum(
            bool(observation["contract_errors"]) for observation in observations
        )
        unmapped_hits = sum(
            result["source_mapping_status"] != "MAPPED"
            for observation in observations
            for result in observation["results"]
        )
        supported_count = sum(
            observation["grounding_status"] == "SUPPORTED"
            for observation in observations
        )
        insufficient_count = sum(
            observation["grounding_status"] == "INSUFFICIENT_EVIDENCE"
            for observation in observations
        )
        no_answer_supported_count = sum(
            observation["expected_no_answer"]
            and observation["grounding_status"] == "SUPPORTED"
            for observation in observations
        )
        answerable_insufficient_count = sum(
            not observation["expected_no_answer"]
            and observation["grounding_status"] == "INSUFFICIENT_EVIDENCE"
            for observation in observations
        )
        collection_incomplete = bool(
            source_mapping_errors
            or unmapped_expected
            or missing_source_versions
            or contract_mismatches
            or unmapped_hits
        )
        artifact = {
            "schema_version": "1.0",
            "artifact_kind": "DRAFT_RETRIEVAL_OBSERVATIONS",
            "diagnostic_scope": diagnostic_scope,
            "evidence_grade": "DIAGNOSTIC",
            "artifact_status": ARTIFACT_STATUS,
            "benchmark_result": "NOT_EVALUATED",
            "claim_eligible": False,
            "release_eligible": False,
            "collection_status": "INCOMPLETE" if collection_incomplete else "COMPLETE",
            "release_blockers": blockers,
            "started_at": started_at,
            "completed_at": utc_now(),
            "input": {
                "draft_dataset_id": dataset["dataset_id"],
                "draft_dataset_role": dataset["dataset_role"],
                "draft_dataset_status": dataset["status"],
                "draft_dataset_file_sha256": sha256_bytes(dataset_bytes),
                "draft_dataset_canonical_sha256": evaluator.dataset_sha256(dataset),
            },
            "retrieval": {
                "transport": "rest",
                "endpoint_origin": base_url,
                "configured_retrieval_mode": "exact",
                "limit": 20,
                "timeout_seconds": arguments.timeout_seconds,
            },
            "collection_scope": {
                "transport": "REST",
                "target": "LOOPBACK_ONLY",
                "query_categories": selected_counts,
                "selected_query_count": len(selected),
                "skipped_version_query_count": len(skipped),
                "ready_version_cases_included": (
                    arguments.include_ready_version_cases
                ),
            },
            "dataset": {
                "dataset_id": dataset["dataset_id"],
                "dataset_role": dataset["dataset_role"],
                "status": dataset["status"],
                "draft_sha256": evaluator.dataset_sha256(dataset),
                "human_verification_status": dataset["human_verification"]["status"],
            },
            "source_bindings": source_bindings,
            "source_mapping_errors": source_mapping_errors,
            "expected_source_sha256": expected_hashes,
            "unmapped_expected_source_sha256": unmapped_expected,
            "expected_source_version_requirements": requirements,
            "unmapped_expected_source_versions": missing_source_versions,
            "source_binding_version_mismatches": version_mismatches,
            "skipped_queries": skipped,
            "queries": observations,
            "summary": {
                "query_count": len(observations),
                "transport_failures": 0,
                "contract_mismatches": contract_mismatches,
                "unmapped_hits": unmapped_hits,
                "missing_expected_source_count": len(unmapped_expected),
                "missing_expected_source_version_count": len(missing_source_versions),
                "source_binding_version_mismatch_count": len(version_mismatches),
                "source_mapping_error_count": len(source_mapping_errors),
                "supported_count": supported_count,
                "insufficient_evidence_count": insufficient_count,
                "no_answer_supported_count": no_answer_supported_count,
                "answerable_insufficient_count": answerable_insufficient_count,
            },
            "claim_warning": (
                f"{diagnostic_scope} raw observations only; benchmark result is "
                "NOT_EVALUATED. Do not quote metrics, PASS, release, OpenSQL, ANN, "
                "or frozen-benchmark claims from this artifact."
            ),
        }
        write_json_atomic(arguments.output, artifact)
        print(f"DRAFT_OBSERVATIONS_WRITTEN {arguments.output}")
        return 0
    except (CollectionError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

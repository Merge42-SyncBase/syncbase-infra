#!/usr/bin/env python3
"""Freeze and evaluate the evidence-bound 30-query Round-1 retrieval set."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit


CATEGORY_COUNTS = {
    "factual_paraphrase": 10,
    "exact_identifier": 5,
    "version_sensitive": 5,
    "no_answer": 10,
}
DATASET_ROLES = {"CALIBRATION", "PROSPECTIVE_HOLDOUT"}
CALIBRATION_BENCHMARK_CLAIM = "CALIBRATION_DIAGNOSTIC_NOT_EVALUATED"
CALIBRATION_QUERY_EXPOSURE = (
    "ALL_30_QUERY_TEXTS_OBSERVED_IN_DRAFT_RUNTIME_DIAGNOSTICS"
)
PROSPECTIVE_BENCHMARK_CLAIM = "NOT_RUN"
PROSPECTIVE_DRAFT_QUERY_EXPOSURE = "NOT_QUERIED_AT_DRAFT_CREATION"
PROSPECTIVE_FREEZE_QUERY_EXPOSURE = "NOT_QUERIED_BEFORE_FREEZE"
THRESHOLDS = {
    "recall_at_5_min": 0.85,
    "mrr_min": 0.75,
    "citation_page_correctness_min": 1.0,
    "superseded_version_leakage_max": 0,
    "no_answer_false_positive_rate_max": 0.10,
    "ann_recall_at_5_degradation_max": 0.02,
}
METRIC_CONTRACT = {
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
HASH = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
RFC3339_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
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
HUMAN_VERIFICATION_STATUSES = {"PENDING", "APPROVED"}
VERSION_FIXTURE_STATUSES = {"PLANNED_NOT_GENERATED", "READY"}
VERSION_FIXTURE_PROTOCOL = {
    "strategy": "APPEND_ONE_INVARIANT_PDF_PAGE",
    "v1_bytes": "UNCHANGED_PUBLIC_BASE_PDF",
    "v2_bytes": "V1_PLUS_ONE_CANONICAL_MARKER_PAGE",
    "generator_contract": "REPORTLAB_INVARIANT_1_THEN_PYPDF_APPEND",
    "release_gate": "GENERATE_HASH_RENDER_HUMAN_APPROVE_BEFORE_FREEZE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_utc_timestamp(value: object, *, label: str) -> list[str]:
    if not isinstance(value, str) or not RFC3339_UTC.fullmatch(value):
        return [f"{label} must be a parseable RFC3339 UTC timestamp"]
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return [f"{label} must be a parseable RFC3339 UTC timestamp"]
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        return [f"{label} must not be in the future"]
    return []


def canonical_sha256(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_exclusive_atomic(path: Path, value: object) -> None:
    """Create a 0600 JSON artifact atomically and refuse any existing target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        # link(2) is atomic and fails with EEXIST; unlike replace(), it cannot
        # silently overwrite an earlier freeze/evaluation artifact.
        os.link(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def dataset_sha256(dataset: dict) -> str:
    content = copy.deepcopy(dataset)
    content.pop("dataset_sha256", None)
    return canonical_sha256(content)


def contains_placeholder(value: object) -> bool:
    if isinstance(value, str):
        normalized = value.strip().upper()
        return any(
            placeholder in normalized
            for placeholder in ("TODO_", "REQUIRED_", "FILL_ME")
        )
    if isinstance(value, list):
        return any(contains_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(contains_placeholder(item) for item in value.values())
    return False


def validate_bindings(bindings: object, *, allow_pending: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(bindings, dict):
        return ["bindings must be an object"]
    for name in sorted(BINDING_HASHES):
        digest = bindings.get(name)
        if allow_pending and digest is None:
            continue
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            errors.append(f"bindings.{name} must be a SHA-256 digest")
    revisions = bindings.get("repository_revisions")
    if not isinstance(revisions, dict) or set(revisions) != REPOSITORY_IDS:
        errors.append("bindings.repository_revisions must contain the five canonical repositories")
    else:
        for repository_id, revision in revisions.items():
            if allow_pending and revision is None:
                continue
            if not isinstance(revision, str) or not FULL_SHA.fullmatch(revision):
                errors.append(
                    f"bindings.repository_revisions.{repository_id} must be a full SHA"
                )
    return errors


def validate_human_verification(
    human_verification: object, *, allow_pending: bool
) -> list[str]:
    if not isinstance(human_verification, dict):
        return ["human_verification must be an object"]
    errors: list[str] = []
    status = human_verification.get("status")
    if status not in HUMAN_VERIFICATION_STATUSES:
        errors.append(
            "human_verification.status must be PENDING or APPROVED"
        )
    worksheet = human_verification.get("worksheet")
    if not isinstance(worksheet, str) or not worksheet.strip():
        errors.append("human_verification.worksheet must be non-empty")
    reviewer = human_verification.get("reviewer")
    reviewed_at = human_verification.get("reviewed_at")
    if status == "PENDING":
        if not allow_pending:
            errors.append("human_verification.status must be APPROVED before freeze")
        if reviewer is not None or reviewed_at is not None:
            errors.append(
                "pending human verification must not name a reviewer or review time"
            )
    elif status == "APPROVED":
        if not isinstance(reviewer, str) or not reviewer.strip():
            errors.append("approved human verification requires a reviewer")
        errors.extend(
            validate_utc_timestamp(
                reviewed_at,
                label="approved human_verification.reviewed_at",
            )
        )
    return errors


def validate_metric_contract(
    metric_contract: object, *, allow_missing_calibration_contract: bool
) -> list[str]:
    if allow_missing_calibration_contract and metric_contract is None:
        return []
    if metric_contract != METRIC_CONTRACT:
        return ["metric_contract differs from the accepted Round-1 contract"]
    return []


def validate_evidence_item(item: object, *, label: str) -> list[str]:
    if not isinstance(item, dict):
        return [f"{label} must be an object"]
    errors: list[str] = []
    source_file = item.get("source_file")
    if not isinstance(source_file, str) or not source_file.strip():
        errors.append(f"{label}.source_file must be non-empty")
    else:
        source_path = PurePosixPath(source_file)
        if source_path.is_absolute() or ".." in source_path.parts:
            errors.append(f"{label}.source_file must be a safe relative path")
        if source_path.suffix.lower() != ".pdf":
            errors.append(f"{label}.source_file must name a PDF")
    source_sha256 = item.get("source_sha256")
    if not isinstance(source_sha256, str) or not HASH.fullmatch(source_sha256):
        errors.append(f"{label}.source_sha256 must be a SHA-256 digest")
    page = item.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        errors.append(f"{label}.page must be a positive integer")
    excerpt = item.get("supporting_excerpt")
    if not isinstance(excerpt, str) or not excerpt.strip():
        errors.append(f"{label}.supporting_excerpt must be non-empty")
    elif len(excerpt) > 240:
        errors.append(f"{label}.supporting_excerpt must be at most 240 characters")
    return errors


def validate_candidate_evidence(
    candidate_evidence: object, relevant: list[object], *, label: str
) -> list[str]:
    if not isinstance(candidate_evidence, list) or not candidate_evidence:
        return [f"{label}.candidate_evidence must be a non-empty array"]
    errors: list[str] = []
    evidence_pairs: set[tuple[str, int]] = set()
    for position, item in enumerate(candidate_evidence):
        item_label = f"{label}.candidate_evidence[{position}]"
        errors.extend(validate_evidence_item(item, label=item_label))
        if isinstance(item, dict):
            source_sha256 = item.get("source_sha256")
            page = item.get("page")
            if isinstance(source_sha256, str) and isinstance(page, int):
                evidence_pairs.add((source_sha256, page))

    target_pairs: set[tuple[str, int]] = set()
    for target in relevant:
        if not isinstance(target, dict):
            continue
        source_sha256 = target.get("source_sha256")
        pages = target.get("pages")
        if isinstance(source_sha256, str) and isinstance(pages, list):
            for page in pages:
                if isinstance(page, int):
                    target_pairs.add((source_sha256, page))
    if evidence_pairs != target_pairs:
        errors.append(
            f"{label}.candidate_evidence must exactly cover relevant source/page pairs"
        )
    return errors


def validate_version_fixture_plans(
    plans: object, *, allow_pending: bool
) -> tuple[list[str], dict[str, dict]]:
    if not isinstance(plans, list):
        return ["version_fixture_plans must be an array"], {}
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    query_ids: list[str] = []
    if len(plans) != CATEGORY_COUNTS["version_sensitive"]:
        errors.append("version_fixture_plans must contain exactly five plans")
    for position, plan in enumerate(plans):
        label = f"version_fixture_plans[{position}]"
        if not isinstance(plan, dict):
            errors.append(f"{label} must be an object")
            continue
        plan_id = plan.get("id")
        query_id = plan.get("query_id")
        if not isinstance(plan_id, str) or not plan_id:
            errors.append(f"{label}.id must be non-empty")
        elif plan_id in by_id:
            errors.append(f"duplicate version fixture plan id: {plan_id}")
        else:
            by_id[plan_id] = plan
        if not isinstance(query_id, str) or not query_id:
            errors.append(f"{label}.query_id must be non-empty")
        else:
            query_ids.append(query_id)
        if isinstance(plan_id, str) and isinstance(query_id, str):
            if (
                not re.fullmatch(r"VP\d{2}", plan_id)
                or not re.fullmatch(r"V\d{2}", query_id)
                or plan_id != f"VP{query_id[1:]}"
            ):
                errors.append(f"{label}.id must correspond to its VNN query id")
        status = plan.get("status")
        if status not in VERSION_FIXTURE_STATUSES:
            errors.append(
                f"{label}.status must be PLANNED_NOT_GENERATED or READY"
            )
        if not allow_pending and status != "READY":
            errors.append(f"{label}.status must be READY before freeze")
        errors.extend(
            validate_evidence_item(plan.get("base_source"), label=f"{label}.base_source")
        )
        base_source = plan.get("base_source")
        v1_source_sha256 = plan.get("v1_source_sha256")
        if not isinstance(base_source, dict) or (
            v1_source_sha256 != base_source.get("source_sha256")
        ):
            errors.append(f"{label}.v1_source_sha256 must equal the base source digest")
        marker = plan.get("v2_marker")
        if not isinstance(marker, str) or not re.fullmatch(r"SYNCBASE-R1-V\d{2}", marker):
            errors.append(f"{label}.v2_marker must use SYNCBASE-R1-VNN")
        elif isinstance(query_id, str) and marker != f"SYNCBASE-R1-{query_id}":
            errors.append(f"{label}.v2_marker must correspond to query_id")
        v2_text = plan.get("v2_only_text")
        if not isinstance(v2_text, str) or not v2_text.strip():
            errors.append(f"{label}.v2_only_text must be non-empty")
        elif isinstance(marker, str) and marker not in v2_text:
            errors.append(f"{label}.v2_only_text must contain its marker")
        planned_page = plan.get("v2_page")
        if not isinstance(planned_page, int) or isinstance(planned_page, bool) or planned_page < 1:
            errors.append(f"{label}.v2_page must be a positive integer")
        v2_source_sha256 = plan.get("v2_source_sha256")
        if status == "PLANNED_NOT_GENERATED":
            if v2_source_sha256 is not None:
                errors.append(
                    f"{label}.v2_source_sha256 must remain null until generated"
                )
        elif not isinstance(v2_source_sha256, str) or not HASH.fullmatch(
            v2_source_sha256
        ):
            errors.append(f"{label}.v2_source_sha256 must be a SHA-256 digest")
        elif v2_source_sha256 == v1_source_sha256:
            errors.append(f"{label}.V2 digest must differ from V1")
    if len(query_ids) != len(set(query_ids)):
        errors.append("version fixture query ids must be unique")
    return errors, by_id


def validate_target(target: object, *, allow_pages: bool) -> list[str]:
    if not isinstance(target, dict):
        return ["target must be an object"]
    errors: list[str] = []
    if not isinstance(target.get("source_sha256"), str) or not HASH.fullmatch(
        target["source_sha256"]
    ):
        errors.append("target.source_sha256 must be a SHA-256 digest")
    if (
        not isinstance(target.get("version"), int)
        or isinstance(target.get("version"), bool)
        or target["version"] < 1
    ):
        errors.append("target.version must be a positive integer")
    if allow_pages:
        pages = target.get("pages")
        if not isinstance(pages, list) or not pages or any(
            not isinstance(page, int) or isinstance(page, bool) or page < 1
            for page in pages
        ):
            errors.append("target.pages must contain positive page numbers")
    return errors


def validate_ready_fixture_active_links(dataset: object) -> list[str]:
    """Reject active F/I ground truth that still names a superseded fixture V1."""

    if not isinstance(dataset, dict):
        return []
    plans = dataset.get("version_fixture_plans")
    queries = dataset.get("queries")
    if not isinstance(plans, list) or not isinstance(queries, list):
        return []

    ready_by_v1: dict[str, dict] = {}
    ready_by_v2: dict[str, dict] = {}
    for plan in plans:
        if not isinstance(plan, dict) or plan.get("status") != "READY":
            continue
        v1_sha256 = plan.get("v1_source_sha256")
        v2_sha256 = plan.get("v2_source_sha256")
        if isinstance(v1_sha256, str):
            ready_by_v1[v1_sha256] = plan
        if isinstance(v2_sha256, str):
            ready_by_v2[v2_sha256] = plan

    errors: list[str] = []
    for query in queries:
        if not isinstance(query, dict) or query.get("category") not in {
            "factual_paraphrase",
            "exact_identifier",
        }:
            continue
        query_id = query.get("id", "UNKNOWN")
        expected = query.get("expected")
        relevant = expected.get("relevant", []) if isinstance(expected, dict) else []
        forbidden = expected.get("forbidden", []) if isinstance(expected, dict) else []
        evidence_items = query.get("candidate_evidence", [])
        if not isinstance(evidence_items, list):
            evidence_items = []

        for position, evidence in enumerate(evidence_items):
            if not isinstance(evidence, dict):
                continue
            source_sha256 = evidence.get("source_sha256")
            if source_sha256 in ready_by_v1:
                plan = ready_by_v1[source_sha256]
                errors.append(
                    f"query {query_id} candidate_evidence[{position}] references "
                    f"READY fixture V1 {plan.get('id')}; bind the active V2"
                )
                continue
            plan = ready_by_v2.get(source_sha256)
            if plan is None:
                continue
            if evidence.get("source_file") != plan.get("v2_source_file"):
                errors.append(
                    f"query {query_id} candidate_evidence[{position}] must use "
                    f"READY fixture V2 source_file from {plan.get('id')}"
                )
            page = evidence.get("page")
            marker_page = plan.get("v2_page")
            if (
                isinstance(page, int)
                and not isinstance(page, bool)
                and isinstance(marker_page, int)
                and page >= marker_page
            ):
                errors.append(
                    f"query {query_id} factual/identifier evidence must remain on "
                    f"an original page before {plan.get('id')} marker page"
                )

        if isinstance(relevant, list):
            for position, target in enumerate(relevant):
                if not isinstance(target, dict):
                    continue
                source_sha256 = target.get("source_sha256")
                if source_sha256 in ready_by_v1:
                    plan = ready_by_v1[source_sha256]
                    errors.append(
                        f"query {query_id} relevant[{position}] references READY "
                        f"fixture V1 {plan.get('id')}; bind the active V2"
                    )
                plan = ready_by_v2.get(source_sha256)
                if plan is not None and target.get("version") != 2:
                    errors.append(
                        f"query {query_id} relevant[{position}] must use version 2 "
                        f"for READY fixture V2 {plan.get('id')}"
                    )
        if isinstance(forbidden, list) and forbidden:
            errors.append(
                f"query {query_id} factual/identifier forbidden targets must remain "
                "empty; fixture V1 is forbidden only by its V query"
            )
    return errors


def validate_dataset(
    dataset: object, *, require_frozen: bool, allow_pending: bool = False
) -> list[str]:
    if not isinstance(dataset, dict):
        return ["dataset must be a JSON object"]
    if require_frozen and allow_pending:
        return ["frozen validation cannot allow pending fields"]
    errors: list[str] = []
    if dataset.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if not isinstance(dataset.get("dataset_id"), str) or not dataset["dataset_id"]:
        errors.append("dataset_id must be non-empty")
    dataset_role = dataset.get("dataset_role")
    if dataset_role not in DATASET_ROLES:
        errors.append(
            "dataset_role must be CALIBRATION or PROSPECTIVE_HOLDOUT"
        )
    prospective_holdout = dataset.get("prospective_holdout")
    if not isinstance(prospective_holdout, bool):
        errors.append("prospective_holdout must be boolean")
    elif dataset_role in DATASET_ROLES and prospective_holdout != (
        dataset_role == "PROSPECTIVE_HOLDOUT"
    ):
        errors.append("prospective_holdout must agree with dataset_role")
    benchmark_claim = dataset.get("benchmark_claim")
    query_exposure = dataset.get("query_exposure")
    if dataset_role == "CALIBRATION":
        if benchmark_claim != CALIBRATION_BENCHMARK_CLAIM:
            errors.append(
                "calibration benchmark_claim must record a non-evaluated diagnostic"
            )
        if query_exposure != CALIBRATION_QUERY_EXPOSURE:
            errors.append(
                "calibration query_exposure must record the observed query set"
            )
    elif dataset_role == "PROSPECTIVE_HOLDOUT":
        if benchmark_claim != PROSPECTIVE_BENCHMARK_CLAIM:
            errors.append(
                "prospective holdout benchmark_claim must remain NOT_RUN"
            )
        accepted_exposure = {
            PROSPECTIVE_DRAFT_QUERY_EXPOSURE,
            PROSPECTIVE_FREEZE_QUERY_EXPOSURE,
        }
        if query_exposure not in accepted_exposure:
            errors.append(
                "prospective holdout query_exposure must record a non-queried state"
            )
        elif (require_frozen or not allow_pending) and (
            query_exposure != PROSPECTIVE_FREEZE_QUERY_EXPOSURE
        ):
            errors.append(
                "prospective holdout query_exposure must be "
                "NOT_QUERIED_BEFORE_FREEZE"
            )
    if require_frozen and dataset_role != "PROSPECTIVE_HOLDOUT":
        errors.append("dataset_role must be PROSPECTIVE_HOLDOUT before freeze")
    expected_status = "FROZEN" if require_frozen else "DRAFT"
    if dataset.get("status") != expected_status:
        errors.append(f"status must be {expected_status}")
    if dataset.get("thresholds") != THRESHOLDS:
        errors.append("thresholds differ from the accepted Round-1 contract")
    errors.extend(
        validate_metric_contract(
            dataset.get("metric_contract"),
            allow_missing_calibration_contract=(
                allow_pending and dataset_role == "CALIBRATION"
            ),
        )
    )
    if dataset.get("version_fixture_protocol") != VERSION_FIXTURE_PROTOCOL:
        errors.append("version_fixture_protocol differs from the accepted draft contract")
    errors.extend(
        validate_bindings(dataset.get("bindings"), allow_pending=allow_pending)
    )
    errors.extend(
        validate_human_verification(
            dataset.get("human_verification"), allow_pending=allow_pending
        )
    )
    plan_errors, version_plans = validate_version_fixture_plans(
        dataset.get("version_fixture_plans"), allow_pending=allow_pending
    )
    errors.extend(plan_errors)
    errors.extend(validate_ready_fixture_active_links(dataset))

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
        category = query.get("category")
        fixture_plan = None
        if category == "version_sensitive":
            fixture_plan_id = query.get("fixture_plan_id")
            if not isinstance(fixture_plan_id, str) or not fixture_plan_id:
                errors.append(f"{label}.fixture_plan_id must be non-empty")
            else:
                fixture_plan = version_plans.get(fixture_plan_id)
                if fixture_plan is None:
                    errors.append(f"{label}.fixture_plan_id is not defined")
                elif fixture_plan.get("query_id") != query_id:
                    errors.append(f"{label}.fixture_plan_id maps to another query")

        planned_version_fixture = (
            category == "version_sensitive"
            and isinstance(fixture_plan, dict)
            and fixture_plan.get("status") == "PLANNED_NOT_GENERATED"
        )
        if no_answer is False and not relevant and not (
            allow_pending and planned_version_fixture
        ):
            errors.append(f"{label} answerable query needs at least one relevant target")
        for target in relevant:
            errors.extend(
                f"{label}.{error}"
                for error in validate_target(target, allow_pages=True)
            )
        for target in forbidden:
            errors.extend(
                f"{label}.{error}"
                for error in validate_target(target, allow_pages=False)
            )
        if category == "version_sensitive" and len(forbidden) != 1:
            errors.append(
                f"{label} version-sensitive query needs exactly one forbidden V1"
            )
        if category in {"factual_paraphrase", "exact_identifier"}:
            errors.extend(
                validate_candidate_evidence(
                    query.get("candidate_evidence"), relevant, label=label
                )
            )
        if category == "version_sensitive" and isinstance(fixture_plan, dict):
            if len(forbidden) == 1 and isinstance(forbidden[0], dict):
                if forbidden[0].get("source_sha256") != fixture_plan.get(
                    "v1_source_sha256"
                ) or forbidden[0].get("version") != 1:
                    errors.append(
                        f"{label}.expected.forbidden must identify fixture V1"
                    )
            if planned_version_fixture:
                if query.get("ground_truth_state") != (
                    "HUMAN_GATED_V2_NOT_GENERATED"
                ):
                    errors.append(
                        f"{label}.ground_truth_state must keep the ungenerated V2 human-gated"
                    )
                if query.get("candidate_evidence_role") != (
                    "V1_BASE_ONLY_NOT_V2_GROUND_TRUTH"
                ):
                    errors.append(
                        f"{label}.candidate_evidence_role must identify V1-only evidence"
                    )
                candidate_evidence = query.get("candidate_evidence")
                if not isinstance(candidate_evidence, list) or len(candidate_evidence) != 1:
                    errors.append(
                        f"{label}.candidate_evidence must contain the verified V1 base"
                    )
                else:
                    errors.extend(
                        validate_evidence_item(
                            candidate_evidence[0],
                            label=f"{label}.candidate_evidence[0]",
                        )
                    )
                    if candidate_evidence[0] != fixture_plan.get("base_source"):
                        errors.append(
                            f"{label}.candidate_evidence must equal the fixture V1 base"
                        )
                if relevant:
                    errors.append(
                        f"{label}.expected.relevant must remain empty until V2 exists"
                    )
            elif fixture_plan.get("status") == "READY":
                if query.get("ground_truth_state") != "VERIFIED_VERSION_PAIR":
                    errors.append(
                        f"{label}.ground_truth_state must be VERIFIED_VERSION_PAIR"
                    )
                if len(relevant) != 1:
                    errors.append(
                        f"{label}.expected.relevant must identify the generated fixture V2"
                    )
                else:
                    target = relevant[0]
                    if not isinstance(target, dict) or (
                        target.get("source_sha256")
                        != fixture_plan.get("v2_source_sha256")
                        or target.get("version") != 2
                        or target.get("pages") != [fixture_plan.get("v2_page")]
                    ):
                        errors.append(
                            f"{label}.expected.relevant must identify the generated fixture V2"
                        )
    if len(identifiers) != len(set(identifiers)):
        errors.append("query ids must be unique")
    version_query_ids = {
        query.get("id")
        for query in queries
        if isinstance(query, dict) and query.get("category") == "version_sensitive"
    }
    fixture_query_ids = {
        plan.get("query_id")
        for plan in version_plans.values()
        if isinstance(plan, dict)
    }
    if version_query_ids != fixture_query_ids:
        errors.append("version fixture plans must map one-to-one to version queries")
    if contains_placeholder(dataset):
        errors.append("dataset contains an unworked placeholder")
    if require_frozen:
        errors.extend(
            validate_utc_timestamp(dataset.get("frozen_at"), label="frozen_at")
        )
        if dataset.get("dataset_sha256") != dataset_sha256(dataset):
            errors.append("dataset_sha256 does not match the frozen content")
    return errors


def _freeze_dataset_after_validation(dataset: dict, *, frozen_at: str) -> dict:
    """Finalize a dataset only after the caller has validated its source files.

    This is intentionally private.  Release callers must use freeze_dataset(),
    which couples source-byte/page/excerpt validation to the freeze operation.
    """

    errors = validate_dataset(dataset, require_frozen=False, allow_pending=False)
    if errors:
        raise ValueError("dataset cannot be frozen: " + "; ".join(errors))
    frozen = copy.deepcopy(dataset)
    frozen["status"] = "FROZEN"
    frozen["frozen_at"] = frozen_at
    frozen.pop("dataset_sha256", None)
    frozen["dataset_sha256"] = dataset_sha256(frozen)
    frozen_errors = validate_dataset(frozen, require_frozen=True, allow_pending=False)
    if frozen_errors:
        raise ValueError("frozen dataset is invalid: " + "; ".join(frozen_errors))
    return frozen


def normalized_excerpt(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def draft_evidence_items(dataset: dict) -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    for query in dataset.get("queries", []):
        if not isinstance(query, dict):
            continue
        for position, evidence in enumerate(query.get("candidate_evidence", [])):
            if isinstance(evidence, dict):
                items.append(
                    (f"query {query.get('id')} candidate_evidence[{position}]", evidence)
                )
    for plan in dataset.get("version_fixture_plans", []):
        if isinstance(plan, dict) and isinstance(plan.get("base_source"), dict):
            items.append((f"version fixture {plan.get('id')} base_source", plan["base_source"]))
    return items


def validate_draft_sources(dataset: dict, source_root: Path) -> list[str]:
    """Verify draft source bytes/pages/excerpts without treating extraction as approval."""

    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError:
        return ["pdfplumber is required for draft source validation"]

    errors: list[str] = validate_ready_fixture_active_links(dataset)
    root = source_root.resolve()
    hash_cache: dict[Path, str] = {}
    page_cache: dict[tuple[Path, int], tuple[int, str]] = {}

    for label, evidence in draft_evidence_items(dataset):
        source_file = evidence.get("source_file")
        source_sha256 = evidence.get("source_sha256")
        page_number = evidence.get("page")
        excerpt = evidence.get("supporting_excerpt")
        if not isinstance(source_file, str) or not isinstance(page_number, int):
            continue
        source_path = (root / source_file).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            errors.append(f"{label}: source path escapes source root")
            continue
        if not source_path.is_file():
            errors.append(f"{label}: source PDF does not exist")
            continue
        if source_path not in hash_cache:
            hash_cache[source_path] = file_sha256(source_path)
        if hash_cache[source_path] != source_sha256:
            errors.append(f"{label}: source SHA-256 does not match")
        cache_key = (source_path, page_number)
        if cache_key not in page_cache:
            try:
                with pdfplumber.open(source_path) as pdf:
                    page_count = len(pdf.pages)
                    page_text = (
                        pdf.pages[page_number - 1].extract_text() or ""
                        if 1 <= page_number <= page_count
                        else ""
                    )
            except Exception as error:  # pdf parser failures are evidence failures
                errors.append(f"{label}: PDF extraction failed: {type(error).__name__}")
                continue
            page_cache[cache_key] = (page_count, page_text)
        page_count, page_text = page_cache[cache_key]
        if not 1 <= page_number <= page_count:
            errors.append(f"{label}: page is outside the PDF page range")
            continue
        if isinstance(excerpt, str) and normalized_excerpt(excerpt) not in normalized_excerpt(
            page_text
        ):
            errors.append(f"{label}: supporting excerpt was not extracted from the page")

    page_counts: dict[Path, int] = {}
    for plan in dataset.get("version_fixture_plans", []):
        if not isinstance(plan, dict) or not isinstance(plan.get("base_source"), dict):
            continue
        source_file = plan["base_source"].get("source_file")
        if not isinstance(source_file, str):
            continue
        source_path = (root / source_file).resolve()
        if source_path not in page_counts and source_path.is_file():
            try:
                with pdfplumber.open(source_path) as pdf:
                    page_counts[source_path] = len(pdf.pages)
            except Exception as error:
                errors.append(
                    f"version fixture {plan.get('id')}: PDF page count failed: "
                    f"{type(error).__name__}"
                )
                continue
        if source_path in page_counts and plan.get("v2_page") != page_counts[source_path] + 1:
            errors.append(
                f"version fixture {plan.get('id')}: V2 page must be appended after V1"
            )
    return errors


def freeze_dataset(dataset: dict, *, frozen_at: str, source_root: Path) -> dict:
    """Validate the exact source tree and then freeze the approved dataset."""

    source_errors = validate_draft_sources(dataset, source_root)
    if source_errors:
        raise ValueError(
            "dataset sources cannot be frozen: " + "; ".join(source_errors)
        )
    return _freeze_dataset_after_validation(dataset, frozen_at=frozen_at)


def validate_pre_freeze_pair(calibration_path: Path, holdout_path: Path) -> None:
    """Re-run the cross-dataset pre-freeze gate on the exact input files."""

    validator_path = Path(__file__).with_name("validate_holdout_integrity.py")
    spec = importlib.util.spec_from_file_location(
        "syncbase_validate_holdout_integrity", validator_path
    )
    if spec is None or spec.loader is None:
        raise ValueError("pre-freeze holdout validator is unavailable")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    errors = validator.validate_holdout_integrity(
        calibration_path,
        holdout_path,
        stage="pre-freeze",
    )
    if errors:
        raise ValueError(
            "pre-freeze holdout integrity failed: " + "; ".join(errors)
        )


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


def validate_source_bindings(source_bindings: object) -> list[str]:
    if not isinstance(source_bindings, list):
        return ["observations.source_bindings must be an array"]
    errors: list[str] = []
    version_ids: set[str] = set()
    document_versions: set[tuple[str, int]] = set()
    for position, binding in enumerate(source_bindings):
        label = f"observations.source_bindings[{position}]"
        if not isinstance(binding, dict):
            errors.append(f"{label} must be an object")
            continue
        document_id = binding.get("document_id")
        version_id = binding.get("version_id")
        if not isinstance(document_id, str) or not document_id.strip():
            errors.append(f"{label}.document_id must be non-empty")
        if not isinstance(version_id, str) or not version_id.strip():
            errors.append(f"{label}.version_id must be non-empty")
        elif version_id in version_ids:
            errors.append(f"duplicate source binding version_id: {version_id}")
        else:
            version_ids.add(version_id)
        source_sha256 = binding.get("source_sha256")
        if not isinstance(source_sha256, str) or not HASH.fullmatch(source_sha256):
            errors.append(f"{label}.source_sha256 must be a SHA-256 digest")
        version = binding.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            errors.append(f"{label}.version must be a positive integer")
        elif isinstance(document_id, str) and document_id.strip():
            key = (document_id, version)
            if key in document_versions:
                errors.append(
                    f"duplicate source binding document/version: {document_id}/{version}"
                )
            else:
                document_versions.add(key)
        if not isinstance(binding.get("active"), bool):
            errors.append(f"{label}.active must be boolean")
        page_count = binding.get("page_count")
        if (
            not isinstance(page_count, int)
            or isinstance(page_count, bool)
            or page_count < 1
        ):
            errors.append(f"{label}.page_count must be a positive integer")
        artifact = binding.get("raw_pdf_artifact")
        if not isinstance(artifact, str) or not artifact.strip():
            errors.append(f"{label}.raw_pdf_artifact must be non-empty")
        else:
            artifact_path = PurePosixPath(artifact)
            if (
                artifact_path.is_absolute()
                or ".." in artifact_path.parts
                or "\\" in artifact
                or artifact_path.suffix.lower() != ".pdf"
            ):
                errors.append(
                    f"{label}.raw_pdf_artifact must be a safe relative PDF path"
                )
        raw_pdf_sha256 = binding.get("raw_pdf_sha256")
        if not isinstance(raw_pdf_sha256, str) or not HASH.fullmatch(raw_pdf_sha256):
            errors.append(f"{label}.raw_pdf_sha256 must be a SHA-256 digest")
    return errors


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
    if observations.get("retrieval_limit") != METRIC_CONTRACT["retrieval_limit"]:
        errors.append("observations.retrieval_limit must be 5")
    if source_origin_tuple(observations.get("source_origin")) is None:
        errors.append(
            "observations.source_origin must be an uncredentialed HTTP(S) origin"
        )
    source_bindings = observations.get("source_bindings")
    errors.extend(validate_source_bindings(source_bindings))
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
        if len(hits) > METRIC_CONTRACT["retrieval_limit"]:
            errors.append(
                f"observations.queries[{position}].results exceeds retrieval_limit"
            )
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
            observed_rank = hit.get("rank")
            if (
                not isinstance(observed_rank, int)
                or isinstance(observed_rank, bool)
                or observed_rank != rank
            ):
                errors.append(f"query {item.get('id')} hit {rank} has invalid rank")
            for field in ("document_id", "version_id", "snippet", "source_url"):
                value = hit.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        f"query {item.get('id')} hit {rank} has invalid {field}"
                    )
            if not isinstance(hit.get("source_sha256"), str) or not HASH.fullmatch(
                hit["source_sha256"]
            ):
                errors.append(f"query {item.get('id')} hit {rank} has invalid source hash")
            if (
                not isinstance(hit.get("version"), int)
                or isinstance(hit["version"], bool)
                or hit["version"] < 1
            ):
                errors.append(f"query {item.get('id')} hit {rank} has invalid version")
            if (
                not isinstance(hit.get("page"), int)
                or isinstance(hit["page"], bool)
                or hit["page"] < 1
            ):
                errors.append(f"query {item.get('id')} hit {rank} has invalid page")
            score = hit.get("score")
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not 0.0 <= score <= 1.0
            ):
                errors.append(f"query {item.get('id')} hit {rank} has invalid score")
    return errors


def source_origin_tuple(value: object) -> tuple[str, str, int] | None:
    """Return a normalized HTTP(S) origin tuple or fail closed."""

    if not isinstance(value, str) or not value or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.casefold(), effective_port


def citation_source_url_matches(hit: dict, source_origin: str) -> bool:
    source_url = hit.get("source_url")
    document_id = hit.get("document_id")
    version = hit.get("version")
    page = hit.get("page")
    if (
        not isinstance(source_url, str)
        or source_url != source_url.strip()
        or not isinstance(document_id, str)
    ):
        return False
    try:
        parsed = urlsplit(source_url)
        parsed.port
    except ValueError:
        return False
    is_relative = not parsed.scheme and not parsed.netloc
    expected_origin = source_origin_tuple(source_origin)
    actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    actual_origin = (
        (parsed.scheme, parsed.hostname.casefold(), actual_port)
        if parsed.scheme in {"http", "https"} and parsed.hostname
        else None
    )
    is_http_absolute = (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.netloc.endswith(":")
        and actual_origin == expected_origin
    )
    if (
        expected_origin is None
        or not (is_relative or is_http_absolute)
        or parsed.fragment
    ):
        return False
    path_parts = parsed.path.split("/")
    if len(path_parts) != 5 or path_parts[1] != "sources" or path_parts[3] != "versions":
        return False
    if unquote(path_parts[2]) != document_id or path_parts[4] != str(version):
        return False
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return query == {"page": [str(page)]}


def safe_evidence_path(evidence_root: Path, relative_path: str) -> Path | None:
    root = evidence_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def verify_citation_pages(observations: dict, evidence_root: Path) -> dict:
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as error:
        raise ValueError(
            "pdfplumber is required for citation-page verification"
        ) from error

    root = evidence_root.resolve()
    if not root.is_dir():
        raise ValueError("evidence_root must be an existing directory")

    bindings = {
        (
            binding["document_id"],
            binding["version_id"],
            binding["source_sha256"],
            binding["version"],
        ): binding
        for binding in observations["source_bindings"]
    }
    artifact_cache: dict[Path, tuple[str | None, int | None, object | None]] = {}
    page_text_cache: dict[tuple[Path, int], str | None] = {}
    failures: list[dict] = []
    correct_hits = 0
    evaluated_hits = 0

    for query in observations["queries"]:
        for hit in query["results"]:
            evaluated_hits += 1
            reasons: list[str] = []
            binding = bindings.get(
                (
                    hit["document_id"],
                    hit["version_id"],
                    hit["source_sha256"],
                    hit["version"],
                )
            )
            if binding is None or binding["active"] is not True:
                reasons.append("SOURCE_BINDING_MISMATCH")
            if binding is not None:
                artifact_path = safe_evidence_path(
                    root, binding["raw_pdf_artifact"]
                )
                digest: str | None = None
                page_count: int | None = None
                pdf: object | None = None
                if artifact_path is not None:
                    if artifact_path not in artifact_cache:
                        try:
                            digest = file_sha256(artifact_path)
                        except OSError:
                            digest = None
                        if digest is not None:
                            try:
                                pdf = pdfplumber.open(artifact_path)
                                page_count = len(pdf.pages)
                            except Exception:
                                if pdf is not None:
                                    try:
                                        pdf.close()
                                    except Exception:
                                        pass
                                pdf = None
                                page_count = None
                        else:
                            page_count = None
                            pdf = None
                        artifact_cache[artifact_path] = (digest, page_count, pdf)
                    else:
                        digest, page_count, pdf = artifact_cache[artifact_path]

                if digest is None or digest != binding["raw_pdf_sha256"] or digest != hit[
                    "source_sha256"
                ]:
                    reasons.append("RAW_PDF_SHA256_MISMATCH")

                page = hit["page"]
                page_in_range = (
                    page_count is not None
                    and page_count == binding["page_count"]
                    and 1 <= page <= page_count
                )
                if not page_in_range:
                    reasons.append("PAGE_OUT_OF_RANGE")

                page_text: str | None = None
                if artifact_path is not None and page_in_range and pdf is not None:
                    page_key = (artifact_path, page)
                    if page_key not in page_text_cache:
                        try:
                            page_text_cache[page_key] = pdf.pages[page - 1].extract_text() or ""
                        except Exception:
                            page_text_cache[page_key] = None
                    page_text = page_text_cache[page_key]
                if page_text is None or normalized_excerpt(hit["snippet"]) not in normalized_excerpt(
                    page_text
                ):
                    reasons.append("SNIPPET_NOT_ON_CITED_PAGE")

            if not citation_source_url_matches(hit, observations["source_origin"]):
                reasons.append("SOURCE_URL_TUPLE_MISMATCH")

            if reasons:
                failures.append({
                    "query_id": query["id"],
                    "rank": hit["rank"],
                    "reasons": reasons,
                })
            else:
                correct_hits += 1

    for _, _, pdf in artifact_cache.values():
        if pdf is not None:
            try:
                pdf.close()
            except Exception:
                pass

    correctness = (
        correct_hits / evaluated_hits
        if evaluated_hits
        else METRIC_CONTRACT["citation_page_correctness"]["empty_population_value"]
    )
    return {
        "citation_page_correctness": round(correctness, 6),
        "citation_page_correct_hits": correct_hits,
        "citation_page_evaluated_hits": evaluated_hits,
        "citation_page_failure_count": len(failures),
        "citation_page_failures": failures,
    }


def calculate_metrics(
    dataset: dict, observations: dict, *, evidence_root: Path
) -> dict:
    by_id = {item["id"]: item for item in observations["queries"]}
    recall_values: list[float] = []
    reciprocal_ranks: list[float] = []
    legacy_citation_total = 0
    legacy_citation_correct = 0
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
                    legacy_citation_total += 1
                    if any(
                        relevant_hit(hit, target, require_page=True)
                        for target in source_version_targets
                    ):
                        legacy_citation_correct += 1
        for hit in hits:
            if any(
                relevant_hit(hit, forbidden, require_page=False)
                for forbidden in expected["forbidden"]
            ):
                superseded_leakage += 1

    legacy_citation_precision = (
        legacy_citation_correct / legacy_citation_total
        if legacy_citation_total
        else 0.0
    )
    citation_metrics = verify_citation_pages(observations, evidence_root)
    return {
        "query_count": len(dataset["queries"]),
        "recall_at_5": round(sum(recall_values) / len(recall_values), 6),
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6),
        **citation_metrics,
        "legacy_same_source_version_page_precision_at_5": round(
            legacy_citation_precision, 6
        ),
        "legacy_same_source_version_page_correct_hits": legacy_citation_correct,
        "legacy_same_source_version_page_evaluated_hits": legacy_citation_total,
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
    evidence_root: Path,
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

    exact_metrics = calculate_metrics(
        dataset, exact_observations, evidence_root=evidence_root
    )
    failures = metric_failures(exact_metrics, dataset["thresholds"])
    measurements: dict[str, object] = {
        "thresholds": dataset["thresholds"],
        "exact": exact_metrics,
    }
    if ann_observations is not None:
        ann_metrics = calculate_metrics(
            dataset, ann_observations, evidence_root=evidence_root
        )
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
            "metric_contract_version": METRIC_CONTRACT["version"],
            "metric_contract_sha256": canonical_sha256(METRIC_CONTRACT),
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
    draft_parser = subparsers.add_parser(
        "validate-draft",
        help="validate candidate ground truth while keeping release gates pending",
    )
    draft_parser.add_argument("draft", type=Path)
    draft_parser.add_argument("--source-root", type=Path, required=True)
    freeze_parser = subparsers.add_parser("freeze", help="freeze completed ground truth")
    freeze_parser.add_argument("draft", type=Path)
    freeze_parser.add_argument("--calibration", type=Path, required=True)
    freeze_parser.add_argument("--source-root", type=Path, required=True)
    freeze_parser.add_argument("--output", type=Path, required=True)
    freeze_parser.add_argument("--frozen-at", default=None)
    evaluate_parser = subparsers.add_parser("evaluate", help="score captured retrieval results")
    evaluate_parser.add_argument("dataset", type=Path)
    evaluate_parser.add_argument("exact", type=Path)
    evaluate_parser.add_argument("--ann", type=Path)
    evaluate_parser.add_argument("--evidence-root", type=Path, required=True)
    evaluate_parser.add_argument("--output", type=Path, required=True)
    evaluate_parser.add_argument("--run-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        if arguments.command == "validate-draft":
            draft = json.loads(arguments.draft.read_text(encoding="utf-8"))
            errors = validate_dataset(
                draft, require_frozen=False, allow_pending=True
            )
            errors.extend(validate_draft_sources(draft, arguments.source_root))
            if errors:
                raise ValueError("draft is invalid: " + "; ".join(errors))
            summary = {
                "schema_version": "1.0",
                "status": "DRAFT_VALID",
                "benchmark_result": "NOT_RUN",
                "freeze_ready": False,
                "human_verification": draft["human_verification"]["status"],
                "release_bindings": "PENDING",
                "candidate_ground_truth_cases": sum(
                    query["category"] in {"factual_paraphrase", "exact_identifier"}
                    for query in draft["queries"]
                ),
                "planned_version_fixture_cases": sum(
                    query["category"] == "version_sensitive"
                    for query in draft["queries"]
                ),
                "no_answer_cases": sum(
                    query["category"] == "no_answer" for query in draft["queries"]
                ),
            }
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0

        if arguments.command == "freeze":
            validate_pre_freeze_pair(arguments.calibration, arguments.draft)
            draft = json.loads(arguments.draft.read_text(encoding="utf-8"))
            frozen = freeze_dataset(
                draft,
                frozen_at=arguments.frozen_at or utc_now(),
                source_root=arguments.source_root,
            )
            write_json_exclusive_atomic(arguments.output, frozen)
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
            evidence_root=arguments.evidence_root,
            started_at=started_at,
            completed_at=utc_now(),
            run_id=arguments.run_id,
            artifact_hashes=artifacts,
        )
        write_json_exclusive_atomic(arguments.output, result)
        print(arguments.output)
        return 0 if result["overall_result"] == "PASS" else 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

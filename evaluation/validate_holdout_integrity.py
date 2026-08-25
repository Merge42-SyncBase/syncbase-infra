#!/usr/bin/env python3
"""Validate that a prospective Round-1 holdout is still separate and unexposed.

This command performs only static DRAFT integrity checks.  It never calls the
retrieval service, computes benchmark metrics, freezes a dataset, or emits a
PASS claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


NORMALIZATION = "NFKC_CASEFOLD_COLLAPSE_WHITESPACE_V1"
CATEGORY_COUNTS = {
    "factual_paraphrase": 10,
    "exact_identifier": 5,
    "version_sensitive": 5,
    "no_answer": 10,
}
RELEASE_HASH_BINDINGS = {
    "corpus_sha256",
    "model_sha256",
    "tokenizer_sha256",
    "profile_sha256",
    "database_identity_sha256",
    "source_release_sha256",
}
REPOSITORY_BINDINGS = {"frontend", "embedding", "was", "infra", "mcp"}
ACCEPTED_METRIC_CONTRACT = {
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
ACCEPTED_PROFILE_SHA256 = (
    "7ad8a410ab8e1e9d869b116f774bea160bd7b9630fa145582d27297181edcf26"
)


class IntegrityError(ValueError):
    """An input or static integrity error safe to print."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalized_query_texts(dataset: dict[str, Any]) -> list[str]:
    return [normalize_text(query["query"]) for query in dataset["queries"]]


def query_text_set_sha256(dataset: dict[str, Any]) -> str:
    serialized = json.dumps(
        normalized_query_texts(dataset),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def factual_identifier_evidence_fingerprints(
    dataset: dict[str, Any],
) -> set[tuple[str, int, str]]:
    fingerprints: set[tuple[str, int, str]] = set()
    for query in dataset.get("queries", []):
        if query.get("category") not in {"factual_paraphrase", "exact_identifier"}:
            continue
        for evidence in query.get("candidate_evidence", []):
            source_sha256 = evidence.get("source_sha256")
            page = evidence.get("page")
            excerpt = evidence.get("supporting_excerpt")
            if (
                isinstance(source_sha256, str)
                and isinstance(page, int)
                and isinstance(excerpt, str)
            ):
                fingerprints.add((source_sha256, page, normalize_text(excerpt)))
    return fingerprints


def load_dataset(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrityError(f"{label} dataset is unavailable or invalid JSON") from error
    if not isinstance(value, dict):
        raise IntegrityError(f"{label} dataset must be a JSON object")
    return value


def null_release_bindings(bindings: object) -> bool:
    if not isinstance(bindings, dict):
        return False
    if set(bindings) != RELEASE_HASH_BINDINGS | {"repository_revisions"}:
        return False
    if any(bindings.get(name) is not None for name in RELEASE_HASH_BINDINGS):
        return False
    revisions = bindings.get("repository_revisions")
    return (
        isinstance(revisions, dict)
        and set(revisions) == REPOSITORY_BINDINGS
        and all(revisions.get(name) is None for name in REPOSITORY_BINDINGS)
    )


def is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def complete_release_bindings(bindings: object) -> bool:
    if not isinstance(bindings, dict):
        return False
    if set(bindings) != RELEASE_HASH_BINDINGS | {"repository_revisions"}:
        return False
    if any(not is_lower_hex(bindings.get(name), 64) for name in RELEASE_HASH_BINDINGS):
        return False
    revisions = bindings.get("repository_revisions")
    return (
        isinstance(revisions, dict)
        and set(revisions) == REPOSITORY_BINDINGS
        and all(is_lower_hex(revisions.get(name), 40) for name in REPOSITORY_BINDINGS)
    )


def pending_human_verification(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("status") == "PENDING"
        and isinstance(value.get("worksheet"), str)
        and bool(value["worksheet"].strip())
        and value.get("reviewer") is None
        and value.get("reviewed_at") is None
    )


def parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed


def approved_human_verification(value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") != "APPROVED":
        return False
    worksheet = value.get("worksheet")
    reviewer = value.get("reviewer")
    reviewed_at = parse_utc_timestamp(value.get("reviewed_at"))
    return (
        isinstance(worksheet, str)
        and bool(worksheet.strip())
        and isinstance(reviewer, str)
        and bool(reviewer.strip())
        and reviewed_at is not None
        and reviewed_at <= datetime.now(timezone.utc) + timedelta(minutes=5)
    )


def validate_query_shape(dataset: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    queries = dataset.get("queries")
    if not isinstance(queries, list):
        return [f"{label} queries must be an array"]
    categories = Counter(
        query.get("category") for query in queries if isinstance(query, dict)
    )
    if categories != Counter(CATEGORY_COUNTS):
        errors.append(f"{label} category counts must be 10/5/5/10")
    if len(queries) != 30:
        errors.append(f"{label} must contain exactly 30 queries")
    if any(
        not isinstance(query, dict)
        or not isinstance(query.get("query"), str)
        or not query["query"].strip()
        for query in queries
    ):
        errors.append(f"{label} query texts must be non-empty strings")
        return errors
    normalized = normalized_query_texts(dataset)
    if len(normalized) != len(set(normalized)):
        errors.append(f"{label} normalized query texts must be unique")
    return errors


def validate_holdout_integrity(
    calibration_path: Path, holdout_path: Path, *, stage: str = "draft"
) -> list[str]:
    if stage not in {"draft", "pre-freeze"}:
        raise IntegrityError("stage must be draft or pre-freeze")
    calibration = load_dataset(calibration_path, "calibration")
    holdout = load_dataset(holdout_path, "holdout")
    errors = validate_query_shape(calibration, "calibration")
    errors.extend(validate_query_shape(holdout, "holdout"))

    if calibration.get("dataset_role") != "CALIBRATION":
        errors.append("calibration dataset_role must be CALIBRATION")
    if calibration.get("prospective_holdout") is not False:
        errors.append("calibration prospective_holdout must be false")
    if holdout.get("dataset_role") != "PROSPECTIVE_HOLDOUT":
        errors.append("holdout dataset_role must be PROSPECTIVE_HOLDOUT")
    if holdout.get("prospective_holdout") is not True:
        errors.append("holdout prospective_holdout must be true")
    if calibration.get("dataset_id") == holdout.get("dataset_id"):
        errors.append("calibration and holdout dataset_id must differ")
    if file_sha256(calibration_path) == file_sha256(holdout_path):
        errors.append("calibration and holdout files must differ")

    if holdout.get("status") != "DRAFT":
        errors.append("holdout status must remain DRAFT")
    if holdout.get("benchmark_claim") != "NOT_RUN":
        errors.append("holdout benchmark_claim must remain NOT_RUN")
    if stage == "draft":
        if holdout.get("query_exposure") != "NOT_QUERIED_AT_DRAFT_CREATION":
            errors.append(
                "holdout query_exposure must be NOT_QUERIED_AT_DRAFT_CREATION"
            )
        if not pending_human_verification(holdout.get("human_verification")):
            errors.append("holdout human verification must remain PENDING")
        if not null_release_bindings(holdout.get("bindings")):
            errors.append("holdout release bindings must all remain null")
    else:
        if holdout.get("query_exposure") != "NOT_QUERIED_BEFORE_FREEZE":
            errors.append(
                "pre-freeze query_exposure must be NOT_QUERIED_BEFORE_FREEZE"
            )
        if not approved_human_verification(holdout.get("human_verification")):
            errors.append("pre-freeze human verification must be APPROVED")
        if not complete_release_bindings(holdout.get("bindings")):
            errors.append("pre-freeze release bindings must be complete")

    integrity = holdout.get("holdout_integrity")
    if not isinstance(integrity, dict):
        errors.append("holdout_integrity must be an object")
        integrity = {}
    if integrity.get("calibration_dataset_id") != calibration.get("dataset_id"):
        errors.append(
            "holdout_integrity.calibration_dataset_id does not match calibration"
        )
    if integrity.get("calibration_dataset_file_sha256") != file_sha256(
        calibration_path
    ):
        errors.append(
            "holdout_integrity.calibration_dataset_file_sha256 does not match calibration"
        )
    if integrity.get("calibration_query_text_set_sha256") != query_text_set_sha256(
        calibration
    ):
        errors.append(
            "holdout_integrity.calibration_query_text_set_sha256 does not match calibration"
        )
    if integrity.get("normalization") != NORMALIZATION:
        errors.append("holdout_integrity.normalization is unsupported")
    if integrity.get("required_exact_query_text_overlap") != 0:
        errors.append("holdout requires zero exact query-text overlap")
    if integrity.get("required_factual_identifier_evidence_overlap") != 0:
        errors.append("holdout requires zero factual/identifier evidence overlap")
    if integrity.get("runtime_exposure_status") != "NOT_QUERIED":
        errors.append("holdout runtime_exposure_status must be NOT_QUERIED")

    calibration_queries = set(normalized_query_texts(calibration))
    holdout_queries = set(normalized_query_texts(holdout))
    overlap = calibration_queries & holdout_queries
    if overlap:
        errors.append(
            f"normalized query text overlap must be zero; observed {len(overlap)}"
        )

    calibration_evidence = factual_identifier_evidence_fingerprints(calibration)
    holdout_evidence = factual_identifier_evidence_fingerprints(holdout)
    evidence_overlap = calibration_evidence & holdout_evidence
    if evidence_overlap:
        errors.append(
            "factual/identifier evidence overlap must be zero; "
            f"observed {len(evidence_overlap)}"
        )

    if holdout.get("thresholds") != calibration.get("thresholds"):
        errors.append("holdout thresholds differ from calibration")
    if holdout.get("version_fixture_protocol") != calibration.get(
        "version_fixture_protocol"
    ):
        errors.append("holdout version fixture protocol differs from calibration")
    calibration_bindings = calibration.get("bindings")
    holdout_bindings = holdout.get("bindings")
    calibration_profile = (
        calibration_bindings.get("profile_sha256")
        if isinstance(calibration_bindings, dict)
        else None
    )
    holdout_profile = (
        holdout_bindings.get("profile_sha256")
        if isinstance(holdout_bindings, dict)
        else None
    )
    if stage == "draft" and holdout_profile != calibration_profile:
        errors.append("holdout profile binding differs from calibration")
    if stage == "pre-freeze" and holdout_profile != ACCEPTED_PROFILE_SHA256:
        errors.append(
            "pre-freeze profile binding differs from the accepted 0.93 profile"
        )
    if (
        stage == "pre-freeze"
        and calibration_profile is not None
        and calibration_profile != ACCEPTED_PROFILE_SHA256
    ):
        errors.append("calibration profile binding is not the accepted 0.93 profile")
    if holdout.get("metric_contract") != ACCEPTED_METRIC_CONTRACT:
        errors.append("holdout metric_contract differs from calibration")

    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("draft", "pre-freeze"), default="draft"
    )
    parser.add_argument("calibration", type=Path)
    parser.add_argument("holdout", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        errors = validate_holdout_integrity(
            arguments.calibration,
            arguments.holdout,
            stage=arguments.stage,
        )
    except IntegrityError as error:
        print(str(error), file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    print(
        "HOLDOUT_PRE_FREEZE_INTEGRITY_VALID"
        if arguments.stage == "pre-freeze"
        else "HOLDOUT_DRAFT_INTEGRITY_VALID"
    )
    print(f"validation_stage={arguments.stage.upper().replace('-', '_')}")
    print(f"calibration_file_sha256={file_sha256(arguments.calibration)}")
    print(
        "calibration_query_text_set_sha256="
        f"{query_text_set_sha256(load_dataset(arguments.calibration, 'calibration'))}"
    )
    print(f"holdout_file_sha256={file_sha256(arguments.holdout)}")
    print("runtime_exposure_status=NOT_QUERIED")
    print("benchmark_result=NOT_RUN")
    print("claim_eligible=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

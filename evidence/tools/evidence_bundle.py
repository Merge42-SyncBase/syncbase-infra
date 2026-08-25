#!/usr/bin/env python3
"""Initialize, validate, and seal sanitized Round-1 evidence bundles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


LANE_DIRECTORIES = (
    "00-source",
    "01-repository-checks",
    "02-qualification-schema",
    "03-opensql-smoke",
    "04-ann",
    "05-outage-recovery",
    "06-evaluation",
    "07-grounding",
    "99-final",
)
CLAIM_COLUMNS = [
    "claim_id",
    "report_wording",
    "video_wording",
    "repository_tag_sha",
    "evidence_file",
    "reproduction_command",
    "expected_result",
    "observed_result",
    "timestamp",
    "verifier",
    "status",
]
RESULTS = {"PASS", "FAIL", "BLOCKED", "TIMEBOX_EXPIRED", "SKIPPED"}
REPOSITORY_IDS = {"frontend", "embedding", "was", "infra", "mcp"}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(
        r"(?i)(?:password|passwd|client_secret|api[_-]?key|access[_-]?token)"
        r"\s*[=:]\s*['\"]?[A-Za-z0-9_./+=-]{12,}"
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_result(result: object) -> list[str]:
    if not isinstance(result, dict):
        return ["result must be a JSON object"]
    errors: list[str] = []
    required = {
        "schema_version",
        "task_id",
        "run_id",
        "overall_result",
        "evidence_grade",
        "started_at",
        "completed_at",
        "repository_revisions",
        "inputs",
        "measurements",
        "artifact_hashes",
        "failure_reason",
    }
    for key in sorted(required - set(result)):
        errors.append(f"missing required field: {key}")
    if errors:
        return errors
    if result["schema_version"] != "1.0":
        errors.append("schema_version must be 1.0")
    if not isinstance(result["task_id"], str) or not result["task_id"]:
        errors.append("task_id must be a non-empty string")
    if not isinstance(result["run_id"], str) or not result["run_id"]:
        errors.append("run_id must be a non-empty string")
    if result["overall_result"] not in RESULTS:
        errors.append(f"overall_result must be one of {sorted(RESULTS)}")
    if not isinstance(result["evidence_grade"], str) or not result["evidence_grade"]:
        errors.append("evidence_grade must be a non-empty string")
    for timestamp in ("started_at", "completed_at"):
        if not isinstance(result[timestamp], str) or not result[timestamp].endswith("Z"):
            errors.append(f"{timestamp} must be a UTC timestamp ending in Z")

    revisions = result["repository_revisions"]
    if not isinstance(revisions, dict) or set(revisions) != REPOSITORY_IDS:
        errors.append("repository_revisions must contain exactly the five canonical repository ids")
    else:
        for repository_id, revision in revisions.items():
            if not isinstance(revision, str) or not FULL_SHA.fullmatch(revision):
                errors.append(f"repository_revisions.{repository_id} must be a full 40-character SHA")

    if not isinstance(result["inputs"], dict):
        errors.append("inputs must be an object")
    if not isinstance(result["measurements"], dict):
        errors.append("measurements must be an object")
    artifacts = result["artifact_hashes"]
    if not isinstance(artifacts, dict):
        errors.append("artifact_hashes must be an object")
    else:
        for artifact, digest in artifacts.items():
            if not isinstance(artifact, str) or not artifact:
                errors.append("artifact_hashes keys must be non-empty paths")
            if not isinstance(digest, str) or not HASH.fullmatch(digest):
                errors.append(f"artifact_hashes.{artifact} must be a SHA-256 digest")

    failure_reason = result["failure_reason"]
    if result["overall_result"] == "PASS" and failure_reason is not None:
        errors.append("failure_reason must be null for PASS")
    if result["overall_result"] != "PASS" and (
        not isinstance(failure_reason, str) or not failure_reason
    ):
        errors.append("failure_reason must explain every non-PASS result")
    if "result_sha256" in result:
        claimed_hash = result["result_sha256"]
        content = dict(result)
        content.pop("result_sha256", None)
        expected_hash = hashlib.sha256(
            json.dumps(
                content, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        if not isinstance(claimed_hash, str) or claimed_hash != expected_hash:
            errors.append("result_sha256 does not match the canonical result content")
    return errors


def initialize_bundle(run_directory: Path, run_id: str) -> None:
    run_directory.mkdir(parents=True, exist_ok=True)
    for directory in LANE_DIRECTORIES:
        (run_directory / directory).mkdir(exist_ok=True)
    manifest_path = run_directory / "00-source/run-manifest.json"
    if not manifest_path.exists():
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "overall_result": "BLOCKED",
            "created_at": utc_now(),
            "purpose": "Round-1 Lane C evidence",
            "lane_directories": list(LANE_DIRECTORIES),
            "failure_reason": "Evidence bundle has not been finalized.",
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    claim_matrix = run_directory / "99-final/claim-matrix.csv"
    if not claim_matrix.exists():
        with claim_matrix.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(CLAIM_COLUMNS)


def evidence_files(run_directory: Path) -> list[Path]:
    excluded = {
        run_directory / "99-final/SHA256SUMS",
        run_directory / "99-final/evidence-index.json",
    }
    return sorted(
        path
        for path in run_directory.rglob("*")
        if path.is_file() and path not in excluded
    )


def assert_sanitized(run_directory: Path) -> None:
    for path in evidence_files(run_directory):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            relative = path.relative_to(run_directory)
            raise ValueError(f"potential secret detected in evidence file: {relative}")


def aggregate_result(statuses: list[str], missing_tasks: set[str]) -> str:
    if missing_tasks:
        return "BLOCKED"
    for candidate in ("FAIL", "TIMEBOX_EXPIRED", "BLOCKED", "SKIPPED"):
        if candidate in statuses:
            return candidate
    return "PASS" if statuses else "BLOCKED"


def finalize_bundle(run_directory: Path, *, required_tasks: set[str]) -> dict:
    if not run_directory.is_dir():
        raise ValueError("evidence run directory does not exist")
    assert_sanitized(run_directory)
    result_paths = sorted(
        path
        for path in run_directory.rglob("result.json")
        if "99-final" not in path.parts
    )
    results: list[tuple[Path, dict]] = []
    for path in result_paths:
        result = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_result(result)
        if errors:
            relative = path.relative_to(run_directory)
            raise ValueError(f"invalid evidence result {relative}: {'; '.join(errors)}")
        results.append((path, result))

    task_ids = {result["task_id"] for _, result in results}
    if len(task_ids) != len(results):
        raise ValueError("duplicate task_id evidence results are not allowed")
    missing_tasks = required_tasks - task_ids
    revision_sets = {
        json.dumps(result["repository_revisions"], sort_keys=True)
        for _, result in results
    }
    if len(revision_sets) > 1:
        raise ValueError("evidence results refer to different repository revisions")
    revisions = results[0][1]["repository_revisions"] if results else {
        repository_id: "0" * 40 for repository_id in sorted(REPOSITORY_IDS)
    }
    statuses = [result["overall_result"] for _, result in results]
    overall_result = aggregate_result(statuses, missing_tasks)
    started_at = utc_now()
    artifact_hashes = {
        str(path.relative_to(run_directory)): sha256(path) for path, _ in results
    }
    failure_reason = None
    if overall_result != "PASS":
        reasons: list[str] = []
        if missing_tasks:
            reasons.append("missing required tasks: " + ", ".join(sorted(missing_tasks)))
        non_pass = [
            f"{result['task_id']}={result['overall_result']}"
            for _, result in results
            if result["overall_result"] != "PASS"
        ]
        if non_pass:
            reasons.append("non-PASS evidence: " + ", ".join(non_pass))
        failure_reason = "; ".join(reasons) or "no evidence result files were found"
    manifest_path = run_directory / "00-source/run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["overall_result"] = overall_result
    manifest["finalized_at"] = utc_now()
    manifest["failure_reason"] = failure_reason
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    index = {
        "schema_version": "1.0",
        "task_id": "C8_SEAL_EVIDENCE",
        "run_id": run_directory.name,
        "overall_result": overall_result,
        "evidence_grade": "EVIDENCE_INTEGRITY",
        "started_at": started_at,
        "completed_at": utc_now(),
        "repository_revisions": revisions,
        "inputs": {"required_tasks": sorted(required_tasks)},
        "measurements": {
            "result_count": len(results),
            "claim_count": count_claims(run_directory / "99-final/claim-matrix.csv"),
            "secret_scan": "PASS",
        },
        "artifact_hashes": artifact_hashes,
        "results": [
            {
                "task_id": result["task_id"],
                "overall_result": result["overall_result"],
                "path": str(path.relative_to(run_directory)),
                "sha256": sha256(path),
            }
            for path, result in results
        ],
        "failure_reason": failure_reason,
    }
    index_path = run_directory / "99-final/evidence-index.json"
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_path = run_directory / "99-final/SHA256SUMS"
    checksum_entries = sorted(
        path
        for path in run_directory.rglob("*")
        if path.is_file() and path != checksum_path
    )
    checksum_path.write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(run_directory)}\n"
            for path in checksum_entries
        ),
        encoding="utf-8",
    )
    return index


def count_claims(claim_matrix: Path) -> int:
    if not claim_matrix.is_file():
        return 0
    with claim_matrix.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != CLAIM_COLUMNS:
        raise ValueError("claim matrix header does not match the Round-1 contract")
    return max(0, len(rows) - 1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="create a Lane-C run directory")
    init_parser.add_argument("run_directory", type=Path)
    init_parser.add_argument("--run-id", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate one result JSON")
    validate_parser.add_argument("result", type=Path)
    finalize_parser = subparsers.add_parser("finalize", help="seal a sanitized run")
    finalize_parser.add_argument("run_directory", type=Path)
    finalize_parser.add_argument("--required-task", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    if arguments.command == "init":
        initialize_bundle(arguments.run_directory, arguments.run_id)
        print(arguments.run_directory)
        return 0
    if arguments.command == "validate":
        result = json.loads(arguments.result.read_text(encoding="utf-8"))
        errors = validate_result(result)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("RESULT_SCHEMA_PASS")
        return 0
    index = finalize_bundle(
        arguments.run_directory, required_tasks=set(arguments.required_task)
    )
    print(arguments.run_directory / "99-final/evidence-index.json")
    return 0 if index["overall_result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

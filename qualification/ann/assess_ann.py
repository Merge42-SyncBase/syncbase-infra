#!/usr/bin/env python3
"""Assess captured ANN capability, natural EXPLAIN usage, and recall evidence.

The command is intentionally read-only: it consumes already captured JSON and
never connects to or mutates a database.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SUPPORTED_METHODS = {"hnsw", "ivfflat"}
MAX_RECALL_DEGRADATION = 0.02
HASH = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_IDS = {"frontend", "embedding", "was", "infra", "mcp"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def assess(capture: dict, *, started_at: str, completed_at: str) -> dict:
    database = capture.get("database", {})
    supported = set(database.get("supported_methods", [])) & SUPPORTED_METHODS
    selected = capture.get("selected_method")
    common = {
        "schema_version": "1.0",
        "task_id": "C4_ANN_EXPLAIN",
        "run_id": capture.get("run_id", "UNSPECIFIED"),
        "evidence_grade": "ANN_ASSERTION",
        "started_at": started_at,
        "completed_at": completed_at,
        "repository_revisions": capture.get("repository_revisions", {}),
        "inputs": {
            "database_identity_sha256": database.get("identity_sha256"),
            "extension": database.get("extension"),
            "extension_version": database.get("extension_version"),
            "supported_methods": sorted(supported),
            "selected_method": selected,
        },
        "artifact_hashes": capture.get("artifact_hashes", {}),
    }
    if selected == "exact" and not supported:
        return common | {
            "overall_result": "SKIPPED",
            "measurements": {
                "recall_at_5_degradation_limit": MAX_RECALL_DEGRADATION,
                "capability": "UNSUPPORTED",
            },
            "failed_gates": [],
            "failure_reason": "ANN is not supported by the captured database capability set.",
        }

    failed_gates: list[str] = []
    if not isinstance(database.get("identity_sha256"), str) or not HASH.fullmatch(
        database["identity_sha256"]
    ):
        failed_gates.append("database_identity_hash")
    if database.get("extension") != "vector":
        failed_gates.append("vector_extension_identity")
    revisions = capture.get("repository_revisions")
    if not isinstance(revisions, dict) or set(revisions) != REPOSITORY_IDS or any(
        not isinstance(revision, str) or not FULL_SHA.fullmatch(revision)
        for revision in revisions.values()
    ):
        failed_gates.append("repository_revisions")
    artifact_hashes = capture.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict) or any(
        not isinstance(artifact_hashes.get(name), str)
        or not HASH.fullmatch(artifact_hashes[name])
        for name in ("explain.json", "evaluation.json")
    ):
        failed_gates.append("raw_artifact_hashes")
    if selected not in SUPPORTED_METHODS or selected not in supported:
        failed_gates.append("supported_capability")
    index = capture.get("index")
    if not isinstance(index, dict) or not index.get("exists"):
        failed_gates.append("catalog_index_exists")
        index = {}
    elif index.get("access_method") != selected:
        failed_gates.append("catalog_access_method")

    plan = capture.get("plan")
    if not isinstance(plan, dict):
        failed_gates.append("explain_plan_captured")
        plan = {}
    else:
        if not plan.get("explain_analyze_buffers"):
            failed_gates.append("explain_analyze_buffers")
        if not plan.get("application_equivalent"):
            failed_gates.append("application_equivalent_query")
        if (
            not plan.get("planner_settings_natural")
            or plan.get("enable_seqscan_forced_off") is not False
        ):
            failed_gates.append("natural_planner_choice")
        index_name = index.get("name")
        if not index_name or index_name not in plan.get("index_names", []):
            failed_gates.append("selected_index_in_plan")

    metrics = capture.get("metrics", {})
    exact_recall = metrics.get("exact_recall_at_5")
    ann_recall = metrics.get("ann_recall_at_5")
    if not isinstance(exact_recall, (int, float)) or isinstance(exact_recall, bool):
        failed_gates.append("exact_recall_at_5")
        exact_recall = 0.0
    if not isinstance(ann_recall, (int, float)) or isinstance(ann_recall, bool):
        failed_gates.append("ann_recall_at_5")
        ann_recall = 0.0
    if not 0.0 <= float(exact_recall) <= 1.0:
        failed_gates.append("exact_recall_at_5_range")
    if not 0.0 <= float(ann_recall) <= 1.0:
        failed_gates.append("ann_recall_at_5_range")
    corpus_chunk_count = metrics.get("corpus_chunk_count")
    if (
        not isinstance(corpus_chunk_count, int)
        or isinstance(corpus_chunk_count, bool)
        or corpus_chunk_count < 1
    ):
        failed_gates.append("corpus_chunk_count")
    degradation = round(float(exact_recall) - float(ann_recall), 6)
    if degradation > MAX_RECALL_DEGRADATION:
        failed_gates.append("recall_at_5_degradation")

    measurements = {
        "corpus_chunk_count": corpus_chunk_count,
        "exact_recall_at_5": exact_recall,
        "ann_recall_at_5": ann_recall,
        "recall_at_5_degradation": degradation,
        "recall_at_5_degradation_limit": MAX_RECALL_DEGRADATION,
        "index_name": index.get("name"),
        "plan_node_types": plan.get("node_types", []),
        "natural_planner_choice": "natural_planner_choice" not in failed_gates,
    }
    overall_result = "PASS" if not failed_gates else "FAIL"
    return common | {
        "overall_result": overall_result,
        "measurements": measurements,
        "failed_gates": failed_gates,
        "failure_reason": None
        if overall_result == "PASS"
        else "ANN evidence gates failed: " + ", ".join(failed_gates),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        started_at = utc_now()
        captured = json.loads(arguments.capture.read_text(encoding="utf-8"))
        result = assess(captured, started_at=started_at, completed_at=utc_now())
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(arguments.output)
        if result["overall_result"] == "PASS":
            return 0
        if result["overall_result"] == "SKIPPED":
            return 2
        return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

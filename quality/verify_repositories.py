#!/usr/bin/env python3
"""Verify the canonical five-repository Round-1 checkout without leaking paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORIES = {
    "frontend": "SyncBase-FE",
    "embedding": "syncbase-embedding",
    "was": "syncbase-was",
    "infra": "syncbase-infra",
    "mcp": "syncbase-mcp",
}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def inspect_repository(repository_id: str, directory: str, workspace_root: Path) -> dict:
    repository = workspace_root / directory
    if not repository.is_dir():
        return {
            "id": repository_id,
            "directory": directory,
            "revision": "MISSING",
            "worktree": "UNKNOWN",
            "result": "FAIL",
            "reason": "repository directory is missing",
        }

    revision_result = run_git(repository, "rev-parse", "HEAD")
    revision = revision_result.stdout.strip()
    if revision_result.returncode != 0 or not FULL_SHA.fullmatch(revision):
        return {
            "id": repository_id,
            "directory": directory,
            "revision": "NO_VCS",
            "worktree": "UNKNOWN",
            "result": "FAIL",
            "reason": "repository has no resolvable full Git revision",
        }

    status_result = run_git(repository, "status", "--porcelain=v1", "--untracked-files=normal")
    if status_result.returncode != 0:
        return {
            "id": repository_id,
            "directory": directory,
            "revision": revision,
            "worktree": "UNKNOWN",
            "result": "FAIL",
            "reason": "Git worktree status could not be read",
        }
    worktree = "DIRTY" if status_result.stdout else "CLEAN"
    return {
        "id": repository_id,
        "directory": directory,
        "revision": revision,
        "worktree": worktree,
        "result": "PASS" if worktree == "CLEAN" else "FAIL",
        "reason": None if worktree == "CLEAN" else "uncommitted release content is present",
    }


def collect(
    workspace_root: Path,
    *,
    allow_dirty: bool = False,
    run_id: str | None = None,
) -> dict:
    started_at = utc_now()
    repositories = [
        inspect_repository(repository_id, directory, workspace_root)
        for repository_id, directory in REPOSITORIES.items()
    ]
    if allow_dirty:
        for repository in repositories:
            if repository["worktree"] == "DIRTY":
                repository["result"] = "PASS"
                repository["reason"] = "dirty worktree explicitly allowed for development inventory"
    overall_result = "PASS" if all(item["result"] == "PASS" for item in repositories) else "FAIL"
    script_path = Path(__file__).resolve()
    return {
        "schema_version": "1.0",
        "task_id": "C0_DEVELOPMENT_INVENTORY" if allow_dirty else "C0_SOURCE_BASELINE",
        "run_id": run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "overall_result": overall_result,
        "evidence_grade": "DEVELOPMENT_ONLY" if allow_dirty else "SOURCE_BASELINE",
        "started_at": started_at,
        "completed_at": utc_now(),
        "repository_revisions": {
            item["id"]: item["revision"] for item in repositories
        },
        "repositories": repositories,
        "inputs": {
            "canonical_directories": REPOSITORIES,
            "allow_dirty": allow_dirty,
        },
        "measurements": {
            "repository_count": len(repositories),
            "clean_worktree_count": sum(
                item["worktree"] == "CLEAN" for item in repositories
            ),
            "dirty_worktree_count": sum(
                item["worktree"] == "DIRTY" for item in repositories
            ),
        },
        "artifact_hashes": {
            "syncbase-infra/quality/verify_repositories.py": hashlib.sha256(
                script_path.read_bytes()
            ).hexdigest()
        },
        "failure_reason": None
        if overall_result == "PASS"
        else "one or more canonical repositories are missing, invalid, or dirty",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_workspace = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=default_workspace)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="inventory development revisions without treating dirty worktrees as a failure",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    result = collect(
        arguments.workspace_root.resolve(),
        allow_dirty=arguments.allow_dirty,
        run_id=arguments.run_id,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if result["overall_result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

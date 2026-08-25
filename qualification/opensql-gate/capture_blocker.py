#!/usr/bin/env python3
"""Capture a truthful BLOCKED OpenSQL qualification result.

This command records why an environment cannot be promoted to actual OpenSQL
single-node evidence. It deliberately cannot emit PASS; successful product
qualification needs a separate smoke run that proves identity and behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


INFRA_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = INFRA_ROOT.parent
VM = "syncbase-opensql-ubuntu"
RESULTS = {"PASS", "FAIL", "BLOCKED", "TIMEBOX_EXPIRED", "SKIPPED"}
EVIDENCE_GRADES = {
    "ACTUAL_OPENSQL_SINGLE_NODE",
    "POSTGRES_REFERENCE",
    "UNAVAILABLE",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def unavailable_snapshot(reason: str) -> dict:
    return {
        "os": "UNAVAILABLE",
        "arch": "UNAVAILABLE",
        "kernel": "UNAVAILABLE",
        "vm_id_sha256": "UNAVAILABLE",
        "logical_cpus": 0,
        "memory_kib": 0,
        "package_files": 0,
        "executables": {
            executable: "MISSING"
            for executable in (
                "opensql",
                "postgres",
                "pg_ctl",
                "psql",
                "patroni",
                "etcd",
                "openproxy",
            )
        },
        "matching_processes": 0,
        "db_listeners": 0,
        "snapshot_error": reason,
    }


def snapshot() -> dict:
    command = r'''
set -u
. /etc/os-release
package_files=$(find /opt/opensql/packages -maxdepth 1 -type f 2>/dev/null | wc -l)
executables='{}'
for executable in opensql postgres pg_ctl psql patroni etcd openproxy; do
  if command -v "$executable" >/dev/null 2>&1; then
    value=$(command -v "$executable")
  else
    value=MISSING
  fi
  executables=$(jq --arg key "$executable" --arg value "$value" '. + {($key): $value}' <<<"$executables")
done
matching_processes=$(ps -eo comm= | grep -E '^(postgres|patroni|etcd|openproxy)$' | wc -l)
db_listeners=$(ss -lnt 2>/dev/null | awk 'NR>1 && ($4 ~ /:5432$/ || $4 ~ /:5433$/ || $4 ~ /:6432$/){count++} END{print count+0}')
jq -n \
  --arg os "$PRETTY_NAME" \
  --arg arch "$(uname -m)" \
  --arg kernel "$(uname -r)" \
  --arg vm_id_sha256 "$(sha256sum /etc/machine-id | cut -d' ' -f1)" \
  --argjson logical_cpus "$(nproc)" \
  --argjson memory_kib "$(awk '/MemTotal/{print $2}' /proc/meminfo)" \
  --argjson package_files "$package_files" \
  --argjson executables "$executables" \
  --argjson matching_processes "$matching_processes" \
  --argjson db_listeners "$db_listeners" \
  '{os:$os, arch:$arch, kernel:$kernel, vm_id_sha256:$vm_id_sha256, logical_cpus:$logical_cpus, memory_kib:$memory_kib, package_files:$package_files, executables:$executables, matching_processes:$matching_processes, db_listeners:$db_listeners}'
'''
    try:
        completed = subprocess.run(
            ["orbctl", "run", "-m", VM, "bash", "-lc", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(completed.stdout)
    except FileNotFoundError:
        return unavailable_snapshot("orbctl is not installed")
    except subprocess.TimeoutExpired:
        return unavailable_snapshot("OrbStack snapshot timed out")
    except subprocess.CalledProcessError as error:
        return unavailable_snapshot(f"orbctl exited with status {error.returncode}")
    except json.JSONDecodeError:
        return unavailable_snapshot("OrbStack snapshot returned invalid JSON")


def load_repository_verifier():
    module_path = INFRA_ROOT / "quality/verify_repositories.py"
    specification = importlib.util.spec_from_file_location("verify_repositories", module_path)
    if not specification or not specification.loader:
        raise RuntimeError("repository verifier could not be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def repository_revisions(workspace_root: Path = WORKSPACE_ROOT) -> dict[str, str]:
    verifier = load_repository_verifier()
    inventory = verifier.collect(workspace_root, allow_dirty=True)
    return inventory["repository_revisions"]


def artifact_paths(workspace_root: Path = WORKSPACE_ROOT) -> tuple[Path, ...]:
    return (
        workspace_root / "syncbase-was/internal/adapters/postgres/store.go",
        workspace_root / "syncbase-was/internal/adapters/postgres/migrate.go",
        workspace_root / "syncbase-infra/acceptance/run-db-outage-recovery.sh",
        workspace_root / "syncbase-infra/quality/verify_repositories.py",
        workspace_root / "syncbase-infra/qualification/opensql-gate/capture_blocker.py",
        workspace_root / "syncbase-infra/evidence/schemas/result.schema.json",
    )


def classify_evidence_grade(observed: dict) -> str:
    executables = observed.get("executables", {})
    postgres_present = any(
        executables.get(executable, "MISSING") != "MISSING"
        for executable in ("postgres", "pg_ctl", "psql")
    )
    if postgres_present or int(observed.get("db_listeners", 0)) > 0:
        return "POSTGRES_REFERENCE"
    return "UNAVAILABLE"


def build_result(
    observed: dict,
    revisions: dict[str, str],
    *,
    run_id: str,
    started_at: str,
    completed_at: str,
    workspace_root: Path = WORKSPACE_ROOT,
) -> dict:
    grade = classify_evidence_grade(observed)
    artifact_hashes = {
        str(path.relative_to(workspace_root)): sha256(path)
        for path in artifact_paths(workspace_root)
    }
    result = {
        "schema_version": "1.0",
        "task_id": "C3_OPENSQL_SMOKE",
        "run_id": run_id,
        "overall_result": "BLOCKED",
        "evidence_grade": grade,
        "started_at": started_at,
        "completed_at": completed_at,
        "repository_revisions": revisions,
        "inputs": {
            "machine_profile": VM,
            "capture_kind": "qualification-blocker",
        },
        "measurements": {
            "package_files": int(observed.get("package_files", 0)),
            "matching_processes": int(observed.get("matching_processes", 0)),
            "db_listeners": int(observed.get("db_listeners", 0)),
        },
        "artifact_hashes": artifact_hashes,
        "checks": [
            {
                "id": "vendor-package",
                "expected": ">=1 vendor OpenSQL package",
                "actual": int(observed.get("package_files", 0)),
                "result": "BLOCKED",
            },
            {
                "id": "product-identity",
                "expected": "authoritative OpenSQL product and version identity",
                "actual": "NOT_PROVEN",
                "result": "BLOCKED",
            },
            {
                "id": "database-listener",
                "expected": ">=1 qualified database listener",
                "actual": int(observed.get("db_listeners", 0)),
                "result": "BLOCKED",
            },
            {
                "id": "smoke-flow",
                "expected": "migrations, roles, pgvector, ingestion, active search, source",
                "actual": "NOT_EXECUTED",
                "result": "BLOCKED",
            },
        ],
        "environment": observed | {"machine": VM, "opensql_version": "NOT_PROVEN"},
        "failure_reason": (
            "Actual OpenSQL product identity and the single-node application smoke flow "
            "have not been proven; PostgreSQL-compatible evidence cannot be promoted."
        ),
    }
    canonical = json.dumps(
        result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    result["result_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-json", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=WORKSPACE_ROOT)
    parser.add_argument("--run-id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    started_at = now()
    observed = (
        json.loads(arguments.snapshot_json.read_text(encoding="utf-8"))
        if arguments.snapshot_json
        else snapshot()
    )
    run_id = arguments.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = build_result(
        observed,
        repository_revisions(arguments.workspace_root),
        run_id=run_id,
        started_at=started_at,
        completed_at=now(),
        workspace_root=arguments.workspace_root,
    )
    output = arguments.output or (
        INFRA_ROOT
        / "evidence/round1/lane-c"
        / run_id
        / "03-opensql-smoke/result.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VM = "syncbase-opensql-ubuntu"


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
            for executable in ("opensql", "postgres", "pg_ctl", "psql", "patroni", "etcd", "openproxy")
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


def source_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "NO_VCS"
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else "NO_VCS"


def artifact_paths() -> tuple[Path, ...]:
    return (
        ROOT / "was/internal/adapters/postgres/store.go",
        ROOT / "was/internal/adapters/postgres/migrate.go",
        ROOT / "infra/acceptance/run-failover.sh",
        ROOT / "docs/backend-architecture.md",
    )


def main() -> None:
    started_at = now()
    observed = snapshot()
    result = {
        "artifacts": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for path in artifact_paths()
        ],
        "candidates": {
            "go": {
                "client_gate": "POSTGRES_REFERENCE_PASS_OPENSQL_EXECUTION_BLOCKED",
                "verdict": "BLOCKED",
            },
        },
        "checks": [
            {"actual": observed["package_files"], "expected": ">=1 vendor OpenSQL 3 package", "id": "vendor-package", "verdict": "BLOCKED"},
            {"actual": observed["executables"], "expected": "OpenSQL server and client executables", "id": "executables", "verdict": "BLOCKED"},
            {"actual": observed["db_listeners"], "expected": ">=1 database listener", "id": "database-listener", "verdict": "BLOCKED"},
            {"actual": observed["matching_processes"], "expected": ">=1 OpenSQL topology process", "id": "database-process", "verdict": "BLOCKED"},
            {"actual": "MISSING", "expected": "approved configtree credentials", "id": "datasource-config", "verdict": "BLOCKED"},
        ],
        "ended_at": now(),
        "environment": observed | {"machine": VM, "opensql_version": "NOT_INSTALLED"},
        "gate_id": "COMMON-OPENSQL-G0",
        "overall_verdict": "BLOCKED",
        "reason": "Current OpenSQL 3 Ubuntu package, running topology, listener, and approved datasource configuration are absent; PostgreSQL compatibility evidence cannot be promoted to OpenSQL PASS.",
        "run_id": f"{datetime.now(timezone.utc).date()}-orbstack-ubuntu-go",
        "schema_version": 2,
        "source_revision": source_revision(),
        "started_at": started_at,
    }
    canonical = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    result["result_sha256"] = hashlib.sha256(canonical).hexdigest()
    output = ROOT / f"output/evidence/g0/COMMON-OPENSQL-G0/{result['run_id']}/result.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

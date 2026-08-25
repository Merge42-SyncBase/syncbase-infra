#!/usr/bin/env python3
"""Strict local invariants for the aggregate Round-1 CycloneDX SBOM.

This complements, but does not replace, validation against the official
CycloneDX JSON Schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_SBOM = Path("evidence/round1/99-final/sbom/syncbase-round1-DRAFT.cdx.json")
REPOSITORIES = ("SyncBase-FE", "syncbase-embedding", "syncbase-was", "syncbase-infra", "syncbase-mcp")
EXPECTED_MODEL_HASH = "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665"
EXPECTED_TOKENIZER_HASH = "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"


def fail(message: str) -> None:
    raise ValueError(message)


def property_map(item: dict[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for prop in item.get("properties", []):
        name = str(prop.get("name", ""))
        value = str(prop.get("value", ""))
        if not name or not value:
            fail("empty property name/value")
        if name in result:
            fail(f"duplicate property name on {item.get('bom-ref', item.get('name'))}: {name}")
        result[name] = value
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sbom", nargs="?", type=Path, default=DEFAULT_SBOM)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    infra_root = Path(__file__).resolve().parents[1]
    workspace = infra_root.parent
    sbom_path = args.sbom if args.sbom.is_absolute() else infra_root / args.sbom
    bom = json.loads(sbom_path.read_text(encoding="utf-8"))

    if bom.get("$schema") != "http://cyclonedx.org/schema/bom-1.5.schema.json":
        fail("CycloneDX 1.5 schema URI is required")
    if bom.get("bomFormat") != "CycloneDX" or bom.get("specVersion") != "1.5":
        fail("CycloneDX 1.5 identity is required")
    if not re.fullmatch(r"urn:uuid:[0-9a-f-]{36}", str(bom.get("serialNumber", ""))):
        fail("valid UUID serialNumber is required")

    metadata_component = bom.get("metadata", {}).get("component", {})
    components = bom.get("components", [])
    if not components:
        fail("components must be nonempty")
    refs = [str(metadata_component.get("bom-ref", ""))] + [str(item.get("bom-ref", "")) for item in components]
    if any(not ref for ref in refs):
        fail("every component requires a nonempty bom-ref")
    if len(refs) != len(set(refs)):
        duplicates = sorted(ref for ref in set(refs) if refs.count(ref) > 1)
        fail(f"duplicate bom-refs: {duplicates}")
    ref_set = set(refs)

    dependency_nodes = bom.get("dependencies", [])
    if not dependency_nodes:
        fail("dependency graph must be nonempty")
    dependency_refs = [str(node.get("ref", "")) for node in dependency_nodes]
    if len(dependency_refs) != len(set(dependency_refs)):
        fail("dependency graph contains duplicate ref nodes")
    if set(dependency_refs) != ref_set:
        fail("dependency graph must contain exactly one node for every component ref")
    edge_count = 0
    for node in dependency_nodes:
        source = str(node["ref"])
        targets = [str(target) for target in node.get("dependsOn", [])]
        if len(targets) != len(set(targets)):
            fail(f"duplicate dependency target under {source}")
        if source in targets:
            fail(f"self dependency under {source}")
        unknown = sorted(set(targets) - ref_set)
        if unknown:
            fail(f"unknown dependency refs under {source}: {unknown}")
        edge_count += len(targets)
    if edge_count == 0:
        fail("dependency graph must have at least one edge")

    by_ref = {str(component["bom-ref"]): component for component in components}
    first_party = [
        component
        for component in components
        if property_map(component).get("syncbase:first-party-repository") == "true"
    ]
    if len(first_party) != 5:
        fail(f"exactly five first-party repository components required, got {len(first_party)}")
    for component in first_party:
        ids = {
            choice.get("license", {}).get("id")
            for choice in component.get("licenses", [])
        }
        if "Apache-2.0" not in ids:
            fail(f"first-party component is not Apache-2.0: {component['bom-ref']}")

    for expected_ref, expected_hash in (
        (f"file:e5:model.onnx:{EXPECTED_MODEL_HASH}", EXPECTED_MODEL_HASH),
        (f"file:e5:tokenizer.json:{EXPECTED_TOKENIZER_HASH}", EXPECTED_TOKENIZER_HASH),
    ):
        component = by_ref.get(expected_ref)
        if not component:
            fail(f"required E5 artifact component missing: {expected_ref}")
        hashes = {(item.get("alg"), item.get("content")) for item in component.get("hashes", [])}
        if ("SHA-256", expected_hash) not in hashes:
            fail(f"required E5 SHA-256 missing: {expected_ref}")

    required_unresolved = {
        "embedded:pdfium:UNRESOLVED_EXACT_BUILD",
        "container:ghcr.io/merge42-syncbase/syncbase-web:UNRESOLVED_RC_DIGEST",
        "container:ghcr.io/merge42-syncbase/syncbase-api:UNRESOLVED_RC_DIGEST",
        "container:ghcr.io/merge42-syncbase/syncbase-worker:UNRESOLVED_RC_DIGEST",
        "container:ghcr.io/merge42-syncbase/syncbase-migrate:UNRESOLVED_RC_DIGEST",
        "container:ghcr.io/merge42-syncbase/syncbase-mcp:UNRESOLVED_RC_DIGEST",
        "container:ghcr.io/merge42-syncbase/syncbase-model-fetcher:UNRESOLVED_RC_DIGEST",
    }
    missing_unresolved = sorted(required_unresolved - set(by_ref))
    if missing_unresolved:
        fail(f"required unresolved release bindings were hidden: {missing_unresolved}")
    for ref in required_unresolved:
        if property_map(by_ref[ref]).get("syncbase:release.blocker") != "true":
            fail(f"unresolved component is not marked as a release blocker: {ref}")

    dirty_repositories = []
    for repository in REPOSITORIES:
        repo_path = workspace / repository
        status = subprocess.run(
            ("git", "status", "--porcelain=v1", "--untracked-files=normal"),
            cwd=repo_path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout
        if status:
            dirty_repositories.append(repository)
    metadata_properties = property_map(bom.get("metadata", {}))
    if dirty_repositories and metadata_properties.get("syncbase:release.state") != "DRAFT_UNTIL_RC":
        fail("dirty source must force metadata release state DRAFT_UNTIL_RC")

    serialized = json.dumps(bom, ensure_ascii=False)
    forbidden_patterns = {
        "absolute macOS user path": r"/Users/[^/]+/",
        "file URI": r"file://",
        "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "GitHub classic token": r"ghp_[A-Za-z0-9]{20,}",
        "GitHub fine-grained token": r"github_pat_[A-Za-z0-9_]{20,}",
        "bearer token": r"(?i)bearer\s+[A-Za-z0-9._~-]{20,}",
    }
    for label, pattern in forbidden_patterns.items():
        if re.search(pattern, serialized):
            fail(f"public-safety violation: {label}")

    status = {
        "result": "PASS",
        "validator": "syncbase-infra/sbom/validate_round1_sbom.py",
        "validation_scope": "structural, reference-integrity, source-state, required-artifact, public-safety",
        "official_schema_validation": "NOT_PERFORMED_BY_THIS_VALIDATOR",
        "sbom": sbom_path.name,
        "sbom_sha256": sha256_file(sbom_path),
        "components": len(components),
        "dependency_nodes": len(dependency_nodes),
        "dependency_edges": edge_count,
        "first_party_repositories": len(first_party),
        "dirty_repositories": dirty_repositories,
    }
    if args.report:
        report_path = args.report if args.report.is_absolute() else infra_root / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "SBOM_INVARIANTS_PASS "
        f"components={len(components)} dependency_nodes={len(dependency_nodes)} "
        f"edges={edge_count} sha256={status['sbom_sha256']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"SBOM_INVARIANTS_FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

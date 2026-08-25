#!/usr/bin/env python3
"""Generate the public-safe, aggregate Round-1 CycloneDX source SBOM.

This intentionally produces a DRAFT_UNTIL_RC BOM while any repository is
dirty or the target tag is absent. It inventories what the checked-in source
and lockfiles prove; it does not pretend that unbuilt RC container images or
their operating-system packages have been scanned.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
import uuid
from collections import defaultdict
from pathlib import Path


TARGET_TAG = "v0.1.0-round1"
DEFAULT_OUTPUT = Path("evidence/round1/99-final/sbom/syncbase-round1-DRAFT.cdx.json")

REPOSITORIES = (
    {
        "id": "frontend",
        "directory": "SyncBase-FE",
        "name": "SyncBase Frontend",
        "url": "https://github.com/Merge42-SyncBase/SyncBase-FE",
        "type": "application",
        "ref": "firstparty:frontend",
    },
    {
        "id": "embedding",
        "directory": "syncbase-embedding",
        "name": "SyncBase vector embedding",
        "url": "https://github.com/Merge42-SyncBase/syncbase-embedding",
        "type": "library",
        "ref": "firstparty:embedding",
    },
    {
        "id": "was",
        "directory": "syncbase-was",
        "name": "SyncBase WAS",
        "url": "https://github.com/Merge42-SyncBase/syncbase-was",
        "type": "application",
        "ref": "firstparty:was",
    },
    {
        "id": "infra",
        "directory": "syncbase-infra",
        "name": "SyncBase infrastructure",
        "url": "https://github.com/Merge42-SyncBase/syncbase-infra",
        "type": "application",
        "ref": "firstparty:infra",
    },
    {
        "id": "mcp",
        "directory": "syncbase-mcp",
        "name": "SyncBase MCP",
        "url": "https://github.com/Merge42-SyncBase/syncbase-mcp",
        "type": "application",
        "ref": "firstparty:mcp",
    },
)

# SPDX identities below were checked against the upstream LICENSE files for
# the exact versions in go.mod. The MCP SDK and gods module contain mixed
# licensing; do not collapse them to one license.
GO_LICENSES: dict[tuple[str, str], tuple[str, ...]] = {
    ("github.com/emirpasic/gods", "v1.18.1"): ("BSD-2-Clause", "ISC"),
    ("github.com/google/jsonschema-go", "v0.4.3"): ("MIT",),
    ("github.com/google/uuid", "v1.6.0"): ("BSD-3-Clause",),
    ("github.com/jackc/pgpassfile", "v1.0.0"): ("MIT",),
    ("github.com/jackc/pgservicefile", "v0.0.0-20240606120523-5a60cdf6a761"): ("MIT",),
    ("github.com/jackc/pgx/v5", "v5.10.0"): ("MIT",),
    ("github.com/jackc/puddle/v2", "v2.2.2"): ("MIT",),
    ("github.com/jolestar/go-commons-pool/v2", "v2.1.2"): ("Apache-2.0",),
    ("github.com/klippa-app/go-pdfium", "v1.19.6"): ("MIT",),
    ("github.com/mitchellh/colorstring", "v0.0.0-20190213212951-d06e56a500db"): ("MIT",),
    ("github.com/modelcontextprotocol/go-sdk", "v1.7.0"): (
        "Apache-2.0",
        "MIT",
        "CC-BY-4.0",
    ),
    ("github.com/patrickmn/go-cache", "v2.1.0+incompatible"): ("MIT",),
    ("github.com/pgvector/pgvector-go", "v0.4.1"): ("MIT",),
    ("github.com/rivo/uniseg", "v0.4.7"): ("MIT",),
    ("github.com/schollz/progressbar/v2", "v2.15.0"): ("MIT",),
    ("github.com/segmentio/asm", "v1.1.3"): ("MIT",),
    ("github.com/segmentio/encoding", "v0.5.4"): ("MIT",),
    ("github.com/stretchr/testify", "v1.11.1"): ("MIT",),
    ("github.com/sugarme/regexpset", "v0.0.0-20200920021344-4d4ec8eaf93c"): (
        "Apache-2.0",
    ),
    ("github.com/sugarme/tokenizer", "v0.3.0"): ("Apache-2.0",),
    ("github.com/tetratelabs/wazero", "v1.12.0"): ("Apache-2.0",),
    ("github.com/yalue/onnxruntime_go", "v1.31.0"): ("MIT",),
    ("github.com/yosida95/uritemplate/v3", "v3.0.2"): ("BSD-3-Clause",),
    ("golang.org/x/crypto", "v0.54.0"): ("BSD-3-Clause",),
    ("golang.org/x/net", "v0.57.0"): ("BSD-3-Clause",),
    ("golang.org/x/oauth2", "v0.35.0"): ("BSD-3-Clause",),
    ("golang.org/x/sync", "v0.22.0"): ("BSD-3-Clause",),
    ("golang.org/x/sys", "v0.47.0"): ("BSD-3-Clause",),
    ("golang.org/x/text", "v0.40.0"): ("BSD-3-Clause",),
    ("golang.org/x/time", "v0.15.0"): ("BSD-3-Clause",),
}

GO_LICENSE_NOTES = {
    ("github.com/emirpasic/gods", "v1.18.1"): (
        "The exact module LICENSE contains BSD-2-Clause terms for the project "
        "and ISC terms for its AVL-tree portion."
    ),
    ("github.com/modelcontextprotocol/go-sdk", "v1.7.0"): (
        "The exact module LICENSE documents a per-file transition: Apache-2.0 "
        "for new/relicensed code, MIT for legacy code lacking relicensing "
        "consent, and CC-BY-4.0 for non-specification documentation."
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


def properties(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": key, "value": str(values[key])} for key in sorted(values)]


def licenses(*identifiers: str) -> list[dict[str, dict[str, str]]]:
    return [{"license": {"id": identifier}} for identifier in identifiers]


def parse_go_mod(path: Path) -> tuple[str, str, list[dict[str, object]]]:
    module = ""
    go_version = ""
    requirements: list[dict[str, object]] = []
    in_require = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("module "):
            module = line.split(maxsplit=1)[1]
            continue
        if line.startswith("go "):
            go_version = line.split(maxsplit=1)[1]
            continue
        if line == "require (":
            in_require = True
            continue
        if in_require and line == ")":
            in_require = False
            continue
        candidate = ""
        if in_require and line and not line.startswith("//"):
            candidate = line
        elif line.startswith("require ") and not line.endswith("("):
            candidate = line.removeprefix("require ").strip()
        if not candidate:
            continue
        indirect = "// indirect" in candidate
        fields = candidate.split("//", 1)[0].split()
        if len(fields) != 2:
            raise ValueError(f"unsupported go.mod requirement: {raw_line}")
        requirements.append({"path": fields[0], "version": fields[1], "indirect": indirect})
    if not module or not go_version:
        raise ValueError(f"module or Go version missing in {path}")
    return module, go_version, requirements


def parse_go_sum(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    result: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 3:
            raise ValueError(f"unsupported go.sum line in {path}: {line}")
        module, version, checksum = fields
        key_version = version.removesuffix("/go.mod")
        kind = "go_mod_h1" if version.endswith("/go.mod") else "module_h1"
        previous = result[(module, key_version)].get(kind)
        if previous and previous != checksum:
            raise ValueError(f"conflicting {kind} for {module}@{key_version}")
        result[(module, key_version)][kind] = checksum
    return result


def go_purl(module: str, version: str) -> str:
    encoded_module = urllib.parse.quote(module, safe="/")
    encoded_version = urllib.parse.quote(version, safe="")
    return f"pkg:golang/{encoded_module}@{encoded_version}"


def parse_quoted_assignments(text: str) -> dict[str, str]:
    return dict(re.findall(r'^([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"$', text, re.MULTILINE))


def parse_container_ref(reference: str) -> tuple[str, str | None, str]:
    locator, digest = reference.rsplit("@sha256:", 1)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"invalid OCI SHA-256: {reference}")
    last_segment = locator.rsplit("/", 1)[-1]
    version = last_segment.rsplit(":", 1)[1] if ":" in last_segment else None
    return locator, version, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    infra_root = Path(__file__).resolve().parents[1]
    workspace = infra_root.parent
    output = args.output if args.output.is_absolute() else infra_root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    component_map: dict[str, dict[str, object]] = {}
    dependency_map: dict[str, set[str]] = defaultdict(set)

    def add_component(component: dict[str, object]) -> None:
        ref = str(component["bom-ref"])
        if ref in component_map:
            raise ValueError(f"duplicate bom-ref while generating: {ref}")
        component_map[ref] = component
        dependency_map.setdefault(ref, set())

    def depend(source: str, *targets: str) -> None:
        dependency_map[source].update(targets)

    repo_state: dict[str, dict[str, object]] = {}
    for repository in REPOSITORIES:
        repo_path = workspace / repository["directory"]
        head = run("git", "rev-parse", "HEAD", cwd=repo_path)
        dirty = bool(run("git", "status", "--porcelain=v1", "--untracked-files=normal", cwd=repo_path))
        target_tag_present = TARGET_TAG in run("git", "tag", "--points-at", "HEAD", cwd=repo_path).splitlines()
        repo_state[repository["id"]] = {
            "head": head,
            "dirty": dirty,
            "target_tag_present": target_tag_present,
        }

        repo_properties = {
            "syncbase:first-party-repository": "true",
            "syncbase:release.state": "DRAFT_UNTIL_RC",
            "syncbase:source.git.dirty": str(dirty).lower(),
            "syncbase:source.git.head": head,
            "syncbase:source.target_tag": TARGET_TAG,
            "syncbase:source.target_tag_present": str(target_tag_present).lower(),
        }
        for descriptor in ("LICENSE", "package.json", "package-lock.json", "go.mod", "go.sum", "compose.yml"):
            descriptor_path = repo_path / descriptor
            if descriptor_path.is_file():
                repo_properties[f"syncbase:source.{descriptor}.sha256"] = sha256_file(descriptor_path)

        component: dict[str, object] = {
            "bom-ref": repository["ref"],
            "type": repository["type"],
            "group": "Merge42-SyncBase",
            "name": repository["name"],
            "scope": "required",
            "licenses": licenses("Apache-2.0"),
            "externalReferences": [{"type": "vcs", "url": repository["url"]}],
            "properties": properties(repo_properties),
        }
        if repository["id"] == "frontend":
            package_json = json.loads((repo_path / "package.json").read_text(encoding="utf-8"))
            component["version"] = package_json["version"]
        add_component(component)

    release_state = "DRAFT_UNTIL_RC"

    # npm produces the dependency graph from package-lock.json, including
    # development dependencies and integrity hashes. Prefix its bom-refs so
    # they remain globally unique after aggregation.
    frontend_root = workspace / "SyncBase-FE"
    npm_bom = json.loads(
        run(
            "npm",
            "sbom",
            "--sbom-format",
            "cyclonedx",
            "--sbom-type",
            "application",
            "--package-lock-only",
            cwd=frontend_root,
        )
    )
    if npm_bom.get("specVersion") != "1.5":
        raise ValueError(f"npm emitted unsupported CycloneDX version: {npm_bom.get('specVersion')}")
    npm_root_ref = npm_bom["metadata"]["component"]["bom-ref"]
    npm_ref_map = {
        component["bom-ref"]: f"npm:{component['bom-ref']}" for component in npm_bom.get("components", [])
    }
    for npm_component in npm_bom.get("components", []):
        transformed = copy.deepcopy(npm_component)
        transformed["bom-ref"] = npm_ref_map[npm_component["bom-ref"]]
        npm_properties = transformed.setdefault("properties", [])
        npm_properties.append({"name": "syncbase:inventory.source", "value": "SyncBase-FE/package-lock.json"})
        npm_properties.sort(key=lambda item: (item["name"], item["value"]))
        add_component(transformed)
    for npm_dependency in npm_bom.get("dependencies", []):
        old_source = npm_dependency["ref"]
        source = "firstparty:frontend" if old_source == npm_root_ref else npm_ref_map[old_source]
        targets = [npm_ref_map[target] for target in npm_dependency.get("dependsOn", [])]
        depend(source, *targets)

    # Go requirements are resolved versions in go.mod. go.sum values are kept
    # as Go h1 properties rather than mislabeled as raw artifact SHA-256 hashes.
    go_roots = {
        "embedding": (workspace / "syncbase-embedding", "firstparty:embedding"),
        "was": (workspace / "syncbase-was", "firstparty:was"),
        "mcp": (workspace / "syncbase-mcp", "firstparty:mcp"),
    }
    go_requirements: dict[tuple[str, str], dict[str, object]] = {}
    all_go_sums: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for root_id, (repo_path, root_ref) in go_roots.items():
        module_name, go_version, requirements = parse_go_mod(repo_path / "go.mod")
        state = repo_state[root_id]
        component_map[root_ref]["properties"].append(
            {"name": "syncbase:go.module", "value": module_name}
        )
        component_map[root_ref]["properties"].append(
            {"name": "syncbase:go.version", "value": go_version}
        )
        component_map[root_ref]["properties"].sort(key=lambda item: (item["name"], item["value"]))
        for key, sums in parse_go_sum(repo_path / "go.sum").items():
            for kind, checksum in sums.items():
                all_go_sums[key][kind].add(checksum)
        for requirement in requirements:
            key = (str(requirement["path"]), str(requirement["version"]))
            record = go_requirements.setdefault(
                key,
                {"roots": {}, "local_heads": {}},
            )
            record["roots"][root_id] = "indirect" if requirement["indirect"] else "direct"
            if key[0].startswith("github.com/Merge42-SyncBase/"):
                sibling_id = "embedding" if key[0].endswith("syncbase-embedding") else "was"
                record["local_heads"][sibling_id] = str(repo_state[sibling_id]["head"]).startswith(
                    key[1].rsplit("-", 1)[-1]
                )

    go_ref_for_key: dict[tuple[str, str], str] = {}
    for (module, version), record in sorted(go_requirements.items()):
        ref = f"go:{module}@{version}"
        go_ref_for_key[(module, version)] = ref
        component_properties = {
            "syncbase:go.requirement": ",".join(
                f"{root_id}:{kind}" for root_id, kind in sorted(record["roots"].items())
            ),
            "syncbase:inventory.source": "go.mod/go.sum",
        }
        sums = all_go_sums.get((module, version), {})
        for kind in ("module_h1", "go_mod_h1"):
            values = sums.get(kind, set())
            if len(values) > 1:
                raise ValueError(f"conflicting {kind} values for {module}@{version}: {sorted(values)}")
            if values:
                component_properties[f"syncbase:go.sum.{kind}"] = next(iter(values))

        first_party_pin = module.startswith("github.com/Merge42-SyncBase/")
        license_ids = ("Apache-2.0",) if first_party_pin else GO_LICENSES.get((module, version))
        if not license_ids:
            raise ValueError(f"license evidence missing for Go requirement {module}@{version}")
        if first_party_pin:
            component_properties["syncbase:first-party-pinned-module"] = "true"
            local_matches = record["local_heads"]
            if local_matches:
                component_properties["syncbase:local-head-match"] = str(all(local_matches.values())).lower()
        if (module, version) in GO_LICENSE_NOTES:
            component_properties["syncbase:license.note"] = GO_LICENSE_NOTES[(module, version)]

        module_component: dict[str, object] = {
            "bom-ref": ref,
            "type": "library",
            "name": module,
            "version": version,
            "scope": "required",
            "purl": go_purl(module, version),
            "licenses": licenses(*license_ids),
            "externalReferences": [
                {"type": "website", "url": f"https://pkg.go.dev/{module}@{version}"}
            ],
            "properties": properties(component_properties),
        }
        if module == "github.com/stretchr/testify":
            module_component["scope"] = "optional"
            module_component["properties"].append(
                {"name": "syncbase:dependency.use", "value": "development-test-only"}
            )
            module_component["properties"].sort(key=lambda item: (item["name"], item["value"]))
        add_component(module_component)

    for root_id, (_, root_ref) in go_roots.items():
        for key, record in go_requirements.items():
            if root_id in record["roots"]:
                depend(root_ref, go_ref_for_key[key])

    # Pinned E5 model and tokenizer.
    model_script = workspace / "syncbase-embedding/ops/model/fetch-e5-small.sh"
    model_values = parse_quoted_assignments(model_script.read_text(encoding="utf-8"))
    required_model_values = ("model_sha", "tokenizer_sha", "model_revision")
    if any(not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", model_values.get(key, "")) for key in required_model_values):
        raise ValueError("could not prove E5 revision/model/tokenizer hashes from fetch-e5-small.sh")
    model_revision = model_values["model_revision"]
    model_ref = f"model:e5:{model_revision}"
    model_file_ref = f"file:e5:model.onnx:{model_values['model_sha']}"
    tokenizer_file_ref = f"file:e5:tokenizer.json:{model_values['tokenizer_sha']}"
    add_component(
        {
            "bom-ref": model_ref,
            "type": "machine-learning-model",
            "group": "intfloat",
            "name": "multilingual-e5-small",
            "version": model_revision,
            "scope": "required",
            "licenses": licenses("MIT"),
            "externalReferences": [
                {
                    "type": "vcs",
                    "url": f"https://huggingface.co/intfloat/multilingual-e5-small/tree/{model_revision}",
                }
            ],
            "properties": properties(
                {
                    "syncbase:inventory.source": "syncbase-embedding/ops/model/fetch-e5-small.sh",
                    "syncbase:model.purpose": "retrieval-embedding-only",
                    "syncbase:model.revision": model_revision,
                }
            ),
        }
    )
    add_component(
        {
            "bom-ref": model_file_ref,
            "type": "file",
            "name": "model.onnx",
            "scope": "required",
            "hashes": [{"alg": "SHA-256", "content": model_values["model_sha"]}],
            "externalReferences": [
                {
                    "type": "distribution",
                    "url": f"https://huggingface.co/intfloat/multilingual-e5-small/resolve/{model_revision}/onnx/model.onnx",
                }
            ],
            "properties": properties({"syncbase:artifact.role": "embedding-model"}),
        }
    )
    add_component(
        {
            "bom-ref": tokenizer_file_ref,
            "type": "file",
            "name": "tokenizer.json",
            "scope": "required",
            "hashes": [{"alg": "SHA-256", "content": model_values["tokenizer_sha"]}],
            "externalReferences": [
                {
                    "type": "distribution",
                    "url": f"https://huggingface.co/intfloat/multilingual-e5-small/resolve/{model_revision}/onnx/tokenizer.json",
                }
            ],
            "properties": properties({"syncbase:artifact.role": "embedding-tokenizer"}),
        }
    )
    depend(model_ref, model_file_ref, tokenizer_file_ref)
    depend("firstparty:embedding", model_ref)

    # ONNX Runtime has a supported, hash-pinned platform set. The RC must still
    # bind one selected platform artifact; that unresolved selection is explicit.
    ort_script = workspace / "syncbase-embedding/ops/model/fetch-onnxruntime.sh"
    ort_text = ort_script.read_text(encoding="utf-8")
    ort_values = parse_quoted_assignments(ort_text)
    ort_version = ort_values.get("version")
    if not ort_version:
        raise ValueError("ONNX Runtime version missing")
    ort_ref = f"runtime:onnxruntime:{ort_version}"
    add_component(
        {
            "bom-ref": ort_ref,
            "type": "library",
            "group": "Microsoft",
            "name": "ONNX Runtime",
            "version": ort_version,
            "scope": "required",
            "licenses": licenses("MIT"),
            "externalReferences": [
                {"type": "vcs", "url": f"https://github.com/microsoft/onnxruntime/tree/v{ort_version}"}
            ],
            "properties": properties(
                {
                    "syncbase:inventory.source": "syncbase-embedding/ops/model/fetch-onnxruntime.sh",
                    "syncbase:release.blocker": "true",
                    "syncbase:release.selection": "SUPPORTED_SET_NOT_RC_SELECTION",
                }
            ),
        }
    )
    ort_children: list[str] = []
    for platform in ("darwin-arm64", "linux-amd64", "linux-arm64"):
        start_marker = f"  {platform})"
        start = ort_text.find(start_marker)
        if start < 0:
            raise ValueError(f"ONNX Runtime platform case missing: {platform}")
        end = ort_text.find("    ;;", start)
        if end < 0:
            raise ValueError(f"ONNX Runtime platform case is unterminated: {platform}")
        values = parse_quoted_assignments("\n".join(line.strip() for line in ort_text[start:end].splitlines()))
        for required in ("archive", "expected", "expected_library", "library"):
            if required not in values:
                raise ValueError(f"{required} missing for ONNX Runtime {platform}")
        archive_name = values["archive"].replace("${version}", ort_version)
        library_name = values["library"].replace("${version}", ort_version)
        archive_ref = f"file:onnxruntime:{platform}:archive:{values['expected']}"
        library_ref = f"file:onnxruntime:{platform}:library:{values['expected_library']}"
        for ref, filename, digest, role in (
            (archive_ref, archive_name, values["expected"], "release-archive"),
            (library_ref, library_name, values["expected_library"], "shared-library"),
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"invalid ONNX Runtime digest for {platform}/{role}")
            component: dict[str, object] = {
                "bom-ref": ref,
                "type": "file",
                "name": filename,
                "version": ort_version,
                "scope": "required",
                "hashes": [{"alg": "SHA-256", "content": digest}],
                "properties": properties(
                    {
                        "syncbase:artifact.platform": platform,
                        "syncbase:artifact.role": role,
                    }
                ),
            }
            if role == "release-archive":
                component["externalReferences"] = [
                    {
                        "type": "distribution",
                        "url": f"https://github.com/microsoft/onnxruntime/releases/download/v{ort_version}/{archive_name}",
                    }
                ]
            add_component(component)
            ort_children.append(ref)
    depend(ort_ref, *ort_children)
    depend("firstparty:embedding", ort_ref)

    # Tracked, vendored PDF.js is independent of the npm pdfjs-dist version.
    pdfjs_dir = workspace / "SyncBase-FE/static/vendor/pdfjs"
    pdfjs_main = pdfjs_dir / "pdf.mjs"
    pdfjs_worker = pdfjs_dir / "pdf.worker.mjs"
    pdfjs_match = re.search(r"pdfjsVersion\s*=\s*([0-9]+\.[0-9]+\.[0-9]+)", pdfjs_main.read_text(encoding="utf-8"))
    if not pdfjs_match:
        raise ValueError("vendored PDF.js version not found in pdf.mjs")
    pdfjs_version = pdfjs_match.group(1)
    pdfjs_ref = f"vendored:pdfjs:{pdfjs_version}"
    pdfjs_files: list[str] = []
    add_component(
        {
            "bom-ref": pdfjs_ref,
            "type": "library",
            "group": "Mozilla Foundation",
            "name": "PDF.js vendored modules",
            "version": pdfjs_version,
            "scope": "required",
            "licenses": licenses("Apache-2.0"),
            "externalReferences": [
                {"type": "vcs", "url": f"https://github.com/mozilla/pdf.js/tree/v{pdfjs_version}"}
            ],
            "properties": properties(
                {"syncbase:inventory.source": "SyncBase-FE/static/vendor/pdfjs"}
            ),
        }
    )
    for vendored_file in (pdfjs_main, pdfjs_worker, pdfjs_dir / "LICENSE"):
        digest = sha256_file(vendored_file)
        ref = f"file:vendored-pdfjs:{vendored_file.name}:{digest}"
        add_component(
            {
                "bom-ref": ref,
                "type": "file",
                "name": vendored_file.name,
                "scope": "required",
                "hashes": [{"alg": "SHA-256", "content": digest}],
                "properties": properties(
                    {"syncbase:artifact.role": "vendored-pdfjs-file"}
                ),
            }
        )
        pdfjs_files.append(ref)
    depend(pdfjs_ref, *pdfjs_files)
    depend("firstparty:frontend", pdfjs_ref)

    # go-pdfium embeds an engine build, but the module version does not prove
    # that engine's exact revision or full notice/package inventory.
    pdfium_unresolved_ref = "embedded:pdfium:UNRESOLVED_EXACT_BUILD"
    add_component(
        {
            "bom-ref": pdfium_unresolved_ref,
            "type": "library",
            "name": "PDFium WebAssembly engine embedded by go-pdfium",
            "scope": "required",
            "description": (
                "Exact embedded PDFium revision, artifact hash, bundled dependency "
                "inventory, and license set are not proven by go-pdfium v1.19.6 alone."
            ),
            "properties": properties(
                {
                    "syncbase:release.blocker": "true",
                    "syncbase:resolution.state": "UNRESOLVED_EXACT_EMBEDDED_BUILD",
                }
            ),
        }
    )
    go_pdfium_ref = go_ref_for_key.get(("github.com/klippa-app/go-pdfium", "v1.19.6"))
    if not go_pdfium_ref:
        raise ValueError("go-pdfium v1.19.6 is not in the resolved Go requirements")
    depend(go_pdfium_ref, pdfium_unresolved_ref)

    # Exact OCI references from Dockerfiles/Compose. Digest identity is proven;
    # package contents are not, so each remains an explicit inventory blocker.
    image_source_files = (
        workspace / "SyncBase-FE/Dockerfile",
        workspace / "syncbase-embedding/ops/model/Dockerfile",
        workspace / "syncbase-was/Dockerfile",
        workspace / "syncbase-mcp/Dockerfile",
        workspace / "syncbase-infra/compose.yml",
    )
    image_uses: dict[str, set[str]] = defaultdict(set)
    image_pattern = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64})")
    for source_file in image_source_files:
        relative_source = source_file.relative_to(workspace).as_posix()
        for reference in image_pattern.findall(source_file.read_text(encoding="utf-8")):
            image_uses[reference].add(relative_source)
    if len(image_uses) < 5:
        raise ValueError(f"expected at least five exact base/container references, found {len(image_uses)}")

    exact_image_refs: dict[str, str] = {}
    for reference, use_sites in sorted(image_uses.items()):
        locator, image_version, digest = parse_container_ref(reference)
        ref = f"container:{locator}@sha256:{digest}"
        exact_image_refs[locator.split(":", 1)[0]] = ref
        component = {
            "bom-ref": ref,
            "type": "container",
            "name": locator,
            "scope": "required",
            "hashes": [{"alg": "SHA-256", "content": digest}],
            "properties": properties(
                {
                    "syncbase:container.inventory": "UNRESOLVED_IMAGE_CONTENTS_NOT_SCANNED",
                    "syncbase:container.reference": reference,
                    "syncbase:inventory.source": ",".join(sorted(use_sites)),
                    "syncbase:release.blocker": "true",
                }
            ),
        }
        if image_version:
            component["version"] = image_version
        add_component(component)

    app_images = {
        "syncbase-web": ("firstparty:frontend", ("node", "nginx")),
        "syncbase-model-fetcher": ("firstparty:embedding", ("debian",)),
        "syncbase-api": ("firstparty:was", ("golang", "debian")),
        "syncbase-worker": ("firstparty:was", ("golang", "debian")),
        "syncbase-migrate": ("firstparty:was", ("golang", "debian")),
        "syncbase-mcp": ("firstparty:mcp", ("golang", "debian")),
    }
    app_image_refs: list[str] = []
    for image_name, (source_ref, base_names) in app_images.items():
        ref = f"container:ghcr.io/merge42-syncbase/{image_name}:UNRESOLVED_RC_DIGEST"
        add_component(
            {
                "bom-ref": ref,
                "type": "container",
                "name": f"ghcr.io/merge42-syncbase/{image_name}",
                "scope": "required",
                "properties": properties(
                    {
                        "syncbase:container.reference": "UNRESOLVED_RC_DIGEST",
                        "syncbase:release.blocker": "true",
                        "syncbase:release.state": "DRAFT_UNTIL_RC",
                    }
                ),
            }
        )
        bases = []
        for base_name in base_names:
            base_ref = exact_image_refs.get(base_name)
            if not base_ref:
                raise ValueError(f"exact {base_name} image reference was not discovered")
            bases.append(base_ref)
        depend(ref, source_ref, *bases)
        app_image_refs.append(ref)

    pgvector_ref = exact_image_refs.get("pgvector/pgvector")
    if not pgvector_ref:
        raise ValueError("exact pgvector container reference was not discovered")
    depend("firstparty:infra", pgvector_ref, *app_image_refs)

    root_ref = "product:syncbase-round1-workspace"
    dependency_map[root_ref].update(repository["ref"] for repository in REPOSITORIES)
    dependency_map[root_ref].update(app_image_refs)

    # Every component gets one dependency node, even when it has no children.
    for ref in component_map:
        dependency_map.setdefault(ref, set())

    source_fingerprint_material = json.dumps(
        {
            "repositories": repo_state,
            "descriptor_hashes": {
                repository["id"]: {
                    prop["name"]: prop["value"]
                    for prop in component_map[repository["ref"]]["properties"]
                    if prop["name"].endswith(".sha256")
                }
                for repository in REPOSITORIES
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    serial = uuid.uuid5(uuid.NAMESPACE_URL, source_fingerprint_material)
    timestamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    bom = {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "lifecycles": [{"phase": "pre-build"}],
            "tools": [
                {
                    "vendor": "Merge42-SyncBase",
                    "name": "round1-sbom-generator",
                    "version": "1.0.0",
                },
                {
                    "vendor": "npm",
                    "name": "cli sbom",
                    "version": str(npm_bom["metadata"]["tools"][0]["version"]),
                },
            ],
            "component": {
                "bom-ref": root_ref,
                "type": "application",
                "group": "Merge42-SyncBase",
                "name": "SyncBase Round-1 workspace",
                "version": release_state,
                "properties": properties(
                    {
                        "syncbase:release.state": release_state,
                        "syncbase:release.target_tag": TARGET_TAG,
                        "syncbase:sbom.scope": "aggregate-source-and-configured-artifact-inventory",
                    }
                ),
            },
            "properties": properties(
                {
                    "syncbase:release.state": release_state,
                    "syncbase:source.repository_count": str(len(REPOSITORIES)),
                }
            ),
        },
        "components": [component_map[ref] for ref in sorted(component_map)],
        "dependencies": [
            {"ref": ref, "dependsOn": sorted(targets)}
            for ref, targets in sorted(dependency_map.items())
        ],
    }

    rendered = json.dumps(bom, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    output.write_text(rendered, encoding="utf-8")
    sbom_sha = sha256_file(output)

    dirty_repositories = sorted(
        repo_id for repo_id, state in repo_state.items() if state["dirty"]
    )
    missing_target_tags = sorted(
        repo_id for repo_id, state in repo_state.items() if not state["target_tag_present"]
    )
    blocker_refs = sorted(
        ref
        for ref, component in component_map.items()
        if any(
            prop["name"] == "syncbase:release.blocker" and prop["value"] == "true"
            for prop in component.get("properties", [])
        )
    )
    status = {
        "state": release_state,
        "generated_at": timestamp,
        "sbom": output.name,
        "sbom_sha256": sbom_sha,
        "cyclonedx_spec_version": "1.5",
        "counts": {
            "components": len(component_map),
            "dependency_nodes": len(dependency_map),
            "dependency_edges": sum(len(targets) for targets in dependency_map.values()),
            "first_party_repositories": len(REPOSITORIES),
            "npm_packages": len(npm_bom.get("components", [])),
            "go_module_versions": len(go_requirements),
            "release_blockers": len(blocker_refs),
        },
        "dirty_repositories": dirty_repositories,
        "missing_target_tags": missing_target_tags,
        "release_blocker_bom_refs": blocker_refs,
        "official_schema_validation": "PENDING_SEPARATE_VALIDATION_RECORD",
        "claim_status": "NOT_RELEASE_EVIDENCE",
    }
    status_path = output.with_name("STATUS.json")
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"SBOM_DRAFT_GENERATED path={output.relative_to(infra_root)} sha256={sbom_sha}")
    print(
        "components={components} dependencies={dependency_nodes} edges={dependency_edges} blockers={release_blockers}".format(
            **status["counts"]
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"SBOM_GENERATION_FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

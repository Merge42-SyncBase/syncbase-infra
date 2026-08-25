#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
infra_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
sbom_path="${1:-$infra_root/evidence/round1/99-final/sbom/syncbase-round1-DRAFT.cdx.json}"
report_path="${2:-$infra_root/evidence/round1/99-final/sbom/schema-validation.json}"

schema_commit="840bcd79c9a190ed51d1eda2db904abc85f78f32"
bom_schema_sha256="2d956c1d05c092695457a91f3b5c57c749793c013ec224a0935807cfc8ae4480"
spdx_schema_sha256="54a6288292bc6c90b0d3952f5f939f17436fa76704ffe68a46e5b78539c7cc1b"
jsf_schema_sha256="8bae002c25e723db7ee1f26afde680ae1a2b1a8f6b4b4b0fd65dc3becb090aae"

schema_dir="$(mktemp -d "${TMPDIR:-/tmp}/syncbase-cyclonedx-schema.XXXXXX")"
trap 'rm -rf "$schema_dir"' EXIT

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

for schema in bom-1.5.schema.json spdx.schema.json jsf-0.82.schema.json; do
  curl --fail --silent --show-error --location \
    "https://raw.githubusercontent.com/CycloneDX/specification/$schema_commit/schema/$schema" \
    --output "$schema_dir/$schema"
done

[[ "$(hash_file "$schema_dir/bom-1.5.schema.json")" == "$bom_schema_sha256" ]] || {
  echo "official CycloneDX BOM schema hash mismatch" >&2
  exit 1
}
[[ "$(hash_file "$schema_dir/spdx.schema.json")" == "$spdx_schema_sha256" ]] || {
  echo "official CycloneDX SPDX schema hash mismatch" >&2
  exit 1
}
[[ "$(hash_file "$schema_dir/jsf-0.82.schema.json")" == "$jsf_schema_sha256" ]] || {
  echo "official CycloneDX JSF schema hash mismatch" >&2
  exit 1
}

npx --yes \
  --package=ajv-cli@5.0.0 \
  --package=ajv-formats@3.0.1 \
  --package=ajv-formats-draft2019@1.6.1 \
  -- ajv validate \
  --spec=draft7 \
  --strict=false \
  -c ajv-formats \
  -c ajv-formats-draft2019 \
  -s "$schema_dir/bom-1.5.schema.json" \
  -r "$schema_dir/spdx.schema.json" \
  -r "$schema_dir/jsf-0.82.schema.json" \
  -d "$sbom_path"

sbom_sha256="$(hash_file "$sbom_path")"
validated_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$(dirname -- "$report_path")"
python3 - "$report_path" "$sbom_path" "$sbom_sha256" "$validated_at" \
  "$schema_commit" "$bom_schema_sha256" "$spdx_schema_sha256" "$jsf_schema_sha256" <<'PY'
import json
import pathlib
import sys

(
    report_path,
    sbom_path,
    sbom_sha256,
    validated_at,
    schema_commit,
    bom_schema_sha256,
    spdx_schema_sha256,
    jsf_schema_sha256,
) = sys.argv[1:]
report = {
    "result": "PASS",
    "validated_at": validated_at,
    "sbom": pathlib.Path(sbom_path).name,
    "sbom_sha256": sbom_sha256,
    "standard": "CycloneDX 1.5 JSON",
    "schema_repository": "https://github.com/CycloneDX/specification",
    "schema_commit": schema_commit,
    "schema_sha256": {
        "bom-1.5.schema.json": bom_schema_sha256,
        "spdx.schema.json": spdx_schema_sha256,
        "jsf-0.82.schema.json": jsf_schema_sha256,
    },
    "validator": {
        "ajv-cli": "5.0.0",
        "ajv-formats": "3.0.1",
        "ajv-formats-draft2019": "1.6.1",
        "draft": "draft-07",
        "format_validation": True,
    },
    "claim_status": "DRAFT_VALIDATION_ONLY_NOT_CLM_015_PASS",
}
pathlib.Path(report_path).write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
status_path = pathlib.Path(sbom_path).with_name("STATUS.json")
if status_path.is_file():
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("sbom_sha256") != sbom_sha256:
        raise SystemExit("STATUS.json does not describe the SBOM that was schema-validated")
    status["official_schema_validation"] = "PASS"
    status["official_schema_validation_record"] = pathlib.Path(report_path).name
    status_path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
PY

printf 'CYCLONEDX_SCHEMA_PASS sbom_sha256=%s report=%s\n' "$sbom_sha256" "$report_path"

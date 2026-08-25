# Round-1 aggregate SBOM tooling

This directory generates the public-safe aggregate CycloneDX source SBOM for
the five SyncBase repositories. It reads only repository state, public source
descriptors, `package-lock.json`, `go.mod`, `go.sum`, Dockerfiles, Compose, and
tracked model/runtime pin scripts. It does not read deployment `.env` files or
credential material.

Generate and run the strict local invariants from `syncbase-infra`:

```sh
python3 sbom/generate_round1_sbom.py
python3 sbom/validate_round1_sbom.py \
  --report evidence/round1/99-final/sbom/invariant-validation.json
./sbom/validate_official_schema.sh
```

The generator intentionally emits `DRAFT_UNTIL_RC` while the five commits and
`v0.1.0-round1` tags are not frozen. The final release procedure must rerun the
generator after the RC freeze, replace unresolved first-party image components
with the tested OCI digests and OS-package inventories, resolve the exact
embedded PDFium build, bind the selected ONNX Runtime platform artifact, then
validate against the official CycloneDX 1.5 JSON Schema. The schema script
downloads all three schema documents from a pinned official specification
commit, verifies their SHA-256 values, and runs pinned AJV packages with URI,
IRI, email, and date-time format validation enabled.

`validate_round1_sbom.py` checks unique references, a closed and nonempty
dependency graph, five Apache-2.0 first-party components, immutable E5 hashes,
explicit unresolved blockers, dirty-source state, and common public-safety
leaks. It complements rather than replaces the official JSON Schema validator.

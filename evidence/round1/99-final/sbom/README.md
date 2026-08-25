# Round-1 SBOM status

The file `syncbase-round1-DRAFT.cdx.json` is a public-safe **source and
configuration inventory**, not final release evidence. Its machine-readable
state is `DRAFT_UNTIL_RC` because the five repository worktrees and target tags
are not frozen.

It includes:

- five Apache-2.0 first-party repository components;
- npm direct/transitive packages from `SyncBase-FE/package-lock.json`, with
  npm-provided integrity hashes, licenses, scope, and relationships;
- versioned Go requirements from all three `go.mod` files, their `go.sum` h1
  evidence, direct/indirect provenance, and exact upstream license identities;
- the immutable E5 revision, model and tokenizer SHA-256 values;
- ONNX Runtime 1.26.0 and all three hash-pinned supported platform artifacts;
- both installed and vendored PDF.js inventories, including tracked file
  hashes;
- exact Dockerfile/Compose base and pgvector OCI digests; and
- six expected first-party image artifacts as unresolved RC bindings.

The following remain explicit release blockers rather than guessed data:

- actual RC application image digests and per-image OS/runtime package scans;
- package contents and PostgreSQL/pgvector versions inside the pinned pgvector
  image;
- the exact PDFium engine build embedded by `go-pdfium` and its complete
  bundled dependency/license inventory; and
- the ONNX Runtime platform artifact selected by the final RC.

`STATUS.json` is the generated count/hash summary,
`invariant-validation.json` records local structural/public-safety checks, and
`schema-validation.json` records validation against the official CycloneDX
schema. None of these draft files may move claim `CLM-015` to `PASS`; regenerate
and revalidate after the immutable RC exists.

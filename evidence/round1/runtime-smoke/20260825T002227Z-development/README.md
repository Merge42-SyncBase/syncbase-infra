# Authenticated MCP retrieval smoke — development evidence

This run proves a narrow fact: the pinned deployed MCP image became ready on loopback after
credential rotation, accepted the protected bearer token, returned five source-linked retrieval
records, and rejected both missing and invalid bearer credentials with HTTP 401.

It is deliberately **not release claim-grade evidence**. The checked-out repositories were dirty,
the deployed image did not identify its source revision, and the image predates the additive
`grounding_status` / `grounding_reason` contract. Consequently this run must not support claims for
the new insufficient-evidence behavior, actual OpenSQL qualification, ANN, quantitative retrieval
quality, outage recovery, or multi-node HA.

The result intentionally omits the bearer token, database address, document identifiers, document
names, snippets, source URLs, and raw response. The recorded query is represented only by SHA-256.

Use `result.json` as the authoritative sanitized record.

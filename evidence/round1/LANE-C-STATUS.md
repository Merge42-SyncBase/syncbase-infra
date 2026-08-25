# Lane C handoff — development preflight

Updated: 2026-08-25 KST

This is a status record, not release evidence. Only a later clean, tagged-release run may be cited as
Round-1 PASS.

| Task | Current status | Evidence / next action |
|---|---|---|
| C0 source baseline | **FAIL** | All five repositories exist and have full SHAs, but all five worktrees were dirty during the preflight. Freeze and rerun without `--allow-dirty`. |
| C1 five-repository tooling | **PASS (tooling only)** | `check-boundaries.sh`, `check-environments.sh`, and all shell syntax checks pass with the canonical sibling names. Rerun against the frozen release. |
| C2 result schema | **PASS (tooling only)** | Shared `overall_result`, `evidence_grade`, five-SHA, input/measurement/artifact/failure fields validate in unit tests. |
| C3 actual OpenSQL single-node smoke | **BLOCKED** | Read-only probe found no `orbctl`, vendor package, listener, process, or product version. Apply the PostgreSQL-compatible wording unless another actual environment passes. |
| C4 ANN + natural EXPLAIN | **NOT RUN** | Read-only assessor exists; no migration was added and no database capability or plan was claimed. Capture capability/catalog/plan/recall or retain exact search. |
| C5 outage/recovery | **NOT RUN** | Existing scripts received path/schema repairs only. No database or worker was stopped by this lane. |
| C6 30-query evaluation | **BLOCKED ON GROUND TRUTH/RUNTIME** | 10/5/5/10 template and evaluator are ready. Complete page/source/version truth, freeze it before tuning, then collect exact observations. |
| C7 grounding | **PARTIAL CONTRACT ONLY** | Evaluator enforces `INSUFFICIENT_EVIDENCE` with an empty result list. Product implementation and REST/MCP/FE runtime verification must pass separately. |
| C8 evidence seal | **FAIL (correctly preserved)** | Development bundle sealed with C0 FAIL and C3 BLOCKED; hashes verify. A fresh tagged-release bundle is required. |

Development preflight:

```text
evidence/round1/lane-c/20260825T000201Z-development-preflight/
```

Current OpenSQL wording consequence:

> Round 1은 PostgreSQL-compatible reference environment에서 검증했으며, actual OpenSQL
> product qualification과 multi-node HA는 Round 2 범위다.

## Reproduction commands

```sh
bash quality/run-lane-c-tooling-tests.sh
bash quality/check-boundaries.sh
bash quality/check-environments.sh
cd evidence/round1/lane-c/20260825T000201Z-development-preflight
shasum -a 256 -c 99-final/SHA256SUMS
```

## Release rerun order

1. Complete and commit the five repositories; ensure strict C0 is clean.
2. Freeze completed query ground truth before changing retrieval policy.
3. Run actual OpenSQL proof if an authoritative environment exists; otherwise retain BLOCKED and downgrade.
4. Capture exact observations; evaluate them without deleting failed queries.
5. Attempt ANN only after capability is proven; require natural plan use and degradation `<= 0.02`.
6. Run authorized worker/database recovery separately and label it single-node recovery, not HA.
7. Seal the new run and copy only fresh PASS claims into the report/video ledger.

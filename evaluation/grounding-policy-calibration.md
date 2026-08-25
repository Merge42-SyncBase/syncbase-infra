# Round 1 grounding-policy calibration record

> **Status:** DRAFT calibration decision; not release evidence
> **Decision date:** 2026-08-25 KST
> **Candidate policy:** `minimum_score = 0.93`
> **Candidate source-declared profile fingerprint:** `7ad8a410ab8e1e9d869b116f774bea160bd7b9630fa145582d27297181edcf26`
> **Permitted claim:** none until a clean, frozen 30-query run passes

## Why this record exists

The previous Compose profile used `minimum_score = 0.62`. On the prewritten
Round 1 draft query set, that profile returned `SUPPORTED` for every one of the
ten no-answer cases. The explicit grounding-status contract therefore existed,
but semantic abstention was not working for this corpus/model/profile.

The raw collector outputs are deliberately excluded from Git and are always labeled
`DRAFT_LOCAL_ONLY_NOT_RELEASE_EVIDENCE`, `DIAGNOSTIC`, `NOT_EVALUATED`, and
`claim_eligible=false`. The first corpus/version-mismatch runs were `INCOMPLETE`;
the later rebound run is `COMPLETE`, but completeness still does not make a DRAFT
calibration artifact release evidence.

All 30 query texts in `syncbase-round1-calibration-v1` have now been sent to the
runtime. This dataset is permanently `CALIBRATION`, not a prospective holdout. It
must be retained with its failures and must never be relabeled or frozen as the
Round-1 benchmark.

| Input | SHA-256 |
|---|---|
| `evaluation/queries.round1.draft.json` before V2 generation | `15de5cfdd3e1162348e48076ea9795aaf01e32134e7941b4498b0e4d9b42f509` |
| `evaluation/runtime/20260825-draft-calibration-01/draft-observations.json` | `a205832c2830e5b08d68120f6a64537ed6aae7b8bc8c91bc0eb09a2183d3f887` |

The diagnostic covered F01–F10, I01–I05, and N01–N10. It intentionally omitted
the five human-gated version fixtures. All 25 REST calls completed, with zero
transport failures, contract mismatches, or unmapped hits. It is still
`INCOMPLETE` because the diagnostic reused one already-active source as document
version 2 while the draft F03 target requires a clean document version 1.

## Raw finding preserved

- Service decisions: `SUPPORTED` for 25/25 queries.
- No-answer false positives: 10/10.
- Answerable top-score range: `0.9148635718`–`0.9602623695`.
- No-answer top-score range: `0.8869156472`–`0.9265289644`.
- Development-only answerable measurements before any stricter cutoff:
  Recall@5 approximately `0.9333`, MRR approximately `0.8389`.
- Superseded-version leakage observed in the 25-query diagnostic: `0`.

These measurements are calibration observations, not the frozen benchmark.
The answer/no-answer score ranges overlap, so no fixed score can preserve every
currently supported answerable query while rejecting every no-answer case.
However, the only answerable query below `0.93` is F05, whose labeled supporting
page was already outside the top five. In this draft corpus, a stricter cutoff
therefore does not reduce the observed Recall@5 numerator.

## Pre-freeze cutoff sweep

The sweep only removes hits below the candidate score. It does not reorder,
invent, relabel, or hide raw service results.

| Candidate score | Simulated Recall@5 | Simulated MRR | No-answer FP rate |
|---:|---:|---:|---:|
| `0.910` | `0.9333` | `0.8333` | `0.40` |
| `0.920` | `0.9333` | `0.8333` | `0.30` |
| `0.925` | `0.9333` | `0.8333` | `0.10` |
| `0.927` | `0.9333` | `0.8333` | `0.00` |
| `0.930` | `0.9333` | `0.8333` | `0.00` |
| `0.935` | `0.8000` | `0.7000` | `0.00` |
| `0.940` | `0.6000` | `0.5333` | `0.00` |

`0.93` is selected as the Round 1 candidate because it is above the maximum
observed no-answer score while remaining below the lowest top score of the 14
answerable cases that currently contribute to Recall@5. `0.935` is explicitly
rejected because the same preserved observations show it would fail the accepted
Recall@5 and MRR bars.

## Integrity and stop-loss rules

1. `0.93` is part of the immutable search-profile fingerprint. A clean database
   and complete re-index are required; existing `0.62` chunks are not reused.
2. Generate and human-review all five V2 fixtures before freezing a separate
   prospective holdout dataset.
3. Build a clean corpus with the ten public sources as V1 and the five planned
   V2 updates. Do not reuse the incomplete diagnostic corpus.
4. Bind the frozen set to corpus, model, tokenizer, profile, database identity,
   source release, and all five repository SHAs.
5. Keep the exposed calibration queries out of the prospective holdout, freeze the
   holdout before querying, and run those 30 new queries once. Do not remove a query
   or change `0.93` after observing that run.
6. If Recall@5, MRR, citation-page correctness, stale leakage, or no-answer FPR
   misses its predeclared bar, retain the result and downgrade the report. Do not
   call retrieval quality or semantic abstention validated.
7. This policy is corpus/model/profile specific. Passing the fixed set would not
   establish a general hallucination detector or universal answerability model.
8. `SOURCE_UNAVAILABLE` fail-closed behavior and semantic `NO_HITS_ABOVE_POLICY`
   are separate claims and require separate evidence.

## Clean V1-only policy validation

After selecting and wiring `0.93`, a separate Compose project was created with a
new PostgreSQL volume, new originals volume, and the runtime-reported profile
fingerprint `7ad8a410ab8e1e9d869b116f774bea160bd7b9630fa145582d27297181edcf26`.
All ten public PDFs were uploaded as document version 1 and reached `ACTIVE`.

| Artifact | SHA-256 |
|---|---|
| `evaluation/runtime/20260825-draft-threshold-093/draft-observations.json` | `6b541d4538aa0d44d45739d65f58fcca0382d7d6e9ad05744c6e99882f620aa6` |
| Exact draft bytes read by that collector | `2977b15ea7a8054dd5febc48a18d12d76300f7f348b7587343c06fa442ee6aba` |

The collector reports `COMPLETE`, with zero transport failures, contract
mismatches, missing source versions, source-mapping errors, or unmapped hits.
The service returned `SUPPORTED` for 14 answerable cases,
`INSUFFICIENT_EVIDENCE / NO_HITS_ABOVE_POLICY` for F05, and the same explicit
insufficient-evidence result for all ten no-answer cases.

Development-only measurements from the preserved raw results are:

- Recall@5: approximately `0.9333` for the 15 F/I cases;
- MRR: approximately `0.8333` for the 15 F/I cases;
- no-answer false-positive rate: `0.00` for N01–N10;
- search latency: p50 `111.866 ms`, p95 `246.803 ms`, max `435.293 ms`;
- current evaluator's citation-page formula: approximately `0.3830`, which
  **fails** the predeclared `1.00` bar and remains unresolved;
- version-sensitive behavior and stale-version leakage: **not measured** in
  this V1-only diagnostic.

This validates `0.93` as the retained candidate; it does not create a benchmark
PASS. The score must not be changed in response to the later V2/frozen run.
The historical page calculation is retained only as raw calibration history. The
accepted citation-provenance contract requires source binding, raw-PDF hash, page
range, snippet-on-cited-page, and source-URL tuple evidence for every returned hit;
that metric was not captured by this older collector and is therefore
`NOT_MEASURED`, not a substituted legacy score.

## Active-V2 rebound calibration diagnostic

After activating all five deterministic V2 fixtures, the first 30-query diagnostic
exposed a labeling defect: nine factual/identifier cases still expected
superseded V1 hash/version targets although search is active-only. That artifact
was correctly retained as `INCOMPLETE`; it could not be repaired by changing runtime
results. The dataset was instead corrected so F01/F02/F03/F09/F10 and
I01/I02/I03/I04 target the corresponding active V2 hash/version 2 while preserving
their original page and excerpt. V1 remains forbidden only in V01–V05.

The corrected calibration set was then collected once more:

| Artifact | SHA-256 |
|---|---|
| `evaluation/runtime/20260825-draft-threshold-093-v2/draft-observations-30-rebound.json` | `01648e2d8ed693c9cff61e2915288e57720de0da26a38fd38e94b765a672fb93` |
| Exact calibration draft bytes read by that collector | `fa3ecd87382537a28115f04369ab75b1e8f2b32b63ceae0da6b62cba167a3564` |

That immutable historical artifact predates the collector's explicit
`dataset.dataset_role` field. Its exact input file hash binds it to the CALIBRATION
dataset above; the raw artifact is not rewritten. Future calibration observations
record `dataset_role=CALIBRATION`, while the collector hard-refuses
`PROSPECTIVE_HOLDOUT` DRAFT input before any HTTP request.

The rebound collector reports `COMPLETE`, with 30/30 calls, zero transport or
contract errors, zero missing source/version mappings, and zero unmapped hits. F05
and all ten no-answer cases returned
`INSUFFICIENT_EVIDENCE / NO_HITS_ABOVE_POLICY`; the other 19 returned `SUPPORTED`.

Development-only measurements preserved from that exposed calibration run are:

- Recall@5: `0.95` over the 20 answerable F/I/V cases;
- MRR: `0.875` over the same cases;
- no-answer false-positive rate: `0.00`;
- superseded-version leakage: `0`;
- search latency: p50 `111.654 ms`, interpolated p95 `165.4042 ms`, max
  `272.608 ms`;
- historical conditional page formula: approximately `0.442308`, below `1.00`;
- accepted citation-provenance metric: **`NOT_MEASURED`** because the DRAFT
  collector did not capture the full raw-PDF/source-URL/snippet provenance proof.

The five V queries each returned its exact active V2 marker page at rank 1 and no
forbidden V1 result. This is useful calibration evidence for the version fixture,
but the texts and results are now exposed and cannot support a prospective claim.
No threshold, query, or failed legacy page result was removed in response.

## Next acceptance command sequence

1. Keep `queries.round1.draft.json` and every existing runtime artifact as exposed
   calibration history; do not query or tune it again.
2. Human-review every row in
   `evaluation/holdout-ground-truth-verification.md`, including corpus-wide review
   of all ten no-answer cases and visual review of every V1/V2 fixture.
3. Run `validate_holdout_integrity.py` and `validate-draft` without contacting the
   runtime. Resolve any static error before proceeding.
4. Fix the release candidate, build a fresh clean benchmark corpus with the ten
   sources and five active V2 updates, and record every corpus/model/profile/
   database/source-release/repository binding.
5. Mark human approval, freeze the prospective holdout, and only then send its 30
   new queries to the release-bound runner exactly once.
6. Evaluate under the locked citation-provenance contract and preserve PASS or FAIL
   unchanged. If provenance was not captured, report citation correctness as
   `NOT_MEASURED`; never reuse the legacy conditional page formula.

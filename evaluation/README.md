# Round-1 retrieval evaluation

`queries.template.json`은 의도적으로 `DRAFT`이며 그대로는 실행할 수 없다. 이는 빈 ground
truth를 그럴듯한 benchmark PASS로 바꾸는 일을 막기 위한 gate다.

분포와 기준은 고정돼 있다.

| 구분 | 수 |
|---|---:|
| factual/paraphrase | 10 |
| exact identifier | 5 |
| version-sensitive | 5 |
| no-answer | 10 |

- Recall@5 `>= 0.85`
- MRR `>= 0.75`
- citation-page correctness `= 1.00`
- superseded-version leakage `= 0`
- no-answer false-positive rate `<= 0.10`
- search p50/p95는 측정값만 보고하고 목표값을 만들지 않는다.
- ANN을 채택하면 exact 대비 Recall@5 저하 `<= 0.02`

## 1. Ground truth 완성 후 freeze

template을 복사해 모든 `TODO`/`REQUIRED` 항목을 최종 corpus, model, tokenizer, profile,
database identity, source release와 다섯 repository SHA로 채운다. 검색 결과를 보기 전에
page ground truth를 사람 검토로 확정한다.

```sh
python3 evaluation/evaluate_retrieval.py freeze \
  evaluation/queries.round1.draft.json \
  --output evaluation/queries.round1.frozen.json
```

freeze는 10/5/5/10 구성, exact thresholds, page ground truth, version 금지 대상, hash
binding을 검사한 뒤 `dataset_sha256`을 기록한다. placeholder가 하나라도 있으면 실패한다.

## 2. 검색 관측값 수집

exact 결과 파일은 frozen query 순서를 그대로 유지한다.

```json
{
  "schema_version": "1.0",
  "dataset_sha256": "...",
  "retrieval_mode": "exact",
  "bindings": {"same": "object as frozen dataset"},
  "queries": [
    {
      "id": "F01",
      "latency_ms": 12.4,
      "grounding_status": "SUPPORTED",
      "grounding_reason": null,
      "results": [
        {"source_sha256": "...", "version": 1, "page": 3, "score": 0.81}
      ]
    }
  ]
}
```

no-answer safety가 동작했다면 `grounding_status`는 `INSUFFICIENT_EVIDENCE`, `results`는
빈 배열이어야 하며 `grounding_reason`은 `NO_HITS_ABOVE_POLICY`,
`ONLY_INACTIVE_VERSION_MATCHED`, `SOURCE_UNAVAILABLE` 중 하나여야 한다. harness는 결과를
임의로 후처리해 숨기지 않는다.

## 3. 평가

```sh
python3 evaluation/evaluate_retrieval.py evaluate \
  evaluation/queries.round1.frozen.json \
  evidence/round1/lane-c/$run_id/06-evaluation/exact-observations.json \
  --output evidence/round1/lane-c/$run_id/06-evaluation/result.json \
  --run-id "$run_id"
```

ANN 결과가 실제로 존재할 때만 `--ann ann-observations.json`을 추가한다. FAIL query를
삭제하거나 threshold를 낮추지 않는다. `queries.template.json`과 unit-test fixture의
존재는 실제 benchmark 실행 증거가 아니다.

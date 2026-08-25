# ANN evidence assessor

이 디렉터리는 ANN migration을 만들거나 database를 변경하지 않는다. `assess_ann.py`는
별도로 캡처한 capability, catalog, 자연 planner plan, exact/ANN recall을 판정할 뿐이다.

`capture.template.json`을 채우기 전에 다음을 실제 환경에서 별도 증거로 남겨야 한다.

1. 설치된 vector extension과 version
2. 지원되는 access method (`hnsw`, 없으면 `ivfflat`)
3. 실제 index catalog row와 definition
4. `ANALYZE` 이후 application-equivalent query의 `EXPLAIN (ANALYZE, BUFFERS)` JSON
5. `enable_seqscan=off`를 사용하지 않았다는 planner setting
6. 같은 frozen dataset에서 나온 exact 및 ANN Recall@5
7. corpus/database/release와 raw artifact hash

```sh
python3 qualification/ann/assess_ann.py \
  evidence/round1/lane-c/$run_id/04-ann/capture.json \
  --output evidence/round1/lane-c/$run_id/04-ann/result.json
```

지원 capability가 없고 `selected_method`가 `exact`면 `SKIPPED`다. index가 있어도 자연
plan이 사용하지 않으면 FAIL이며, exact 대비 Recall@5 저하가 `0.02`를 넘으면 FAIL이다.
그 경우 Round 1은 exact search를 유지하고 ANN 사용 claim을 하지 않는다.

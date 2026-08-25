# OpenSQL single-node qualification gate

`capture_blocker.py`는 현재 환경을 실제 OpenSQL PASS로 만들지 않는다. 제품 identity와
application smoke가 증명되지 않은 상황을 공통 evidence schema의 `BLOCKED` 결과로
기록하는 read-only 도구다.

```sh
python3 qualification/opensql-gate/capture_blocker.py \
  --run-id "$run_id" \
  --output "evidence/round1/lane-c/$run_id/03-opensql-smoke/result.json"
```

현재 local preflight에서 OpenSQL package, listener 및 product version을 찾지 못하면
다음 정책을 적용한다.

> Round 1은 PostgreSQL-compatible reference environment에서 검증했으며, actual
> OpenSQL product qualification과 multi-node HA는 Round 2 범위다.

`ACTUAL_OPENSQL_SINGLE_NODE/PASS`는 별도의 실제 smoke runner가 아래 항목을 모두 실행하고
raw output을 hash로 묶었을 때만 허용한다.

1. vendor inventory 또는 동등한 authoritative product identity와 version
2. vector extension identity와 version
3. clean migration
4. owner/web/worker/MCP role과 grant의 positive/negative test
5. document upload와 processing 완료
6. active-version-only search
7. page source 조회
8. final corpus/model/profile/database/source-release와 다섯 repository full SHA

generic PostgreSQL `SELECT version()` 결과, schema 파일, 기존 로그 또는 이 blocker capture는
actual OpenSQL 실행 증거가 아니다. multi-node HA는 항상 `NOT_TESTED`로 별도 표기한다.

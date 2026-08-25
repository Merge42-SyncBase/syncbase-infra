# Round-1 Lane C evidence

이 디렉터리는 실행 증거를 위한 구조와 도구만 정의한다. 테스트 fixture가 통과했다는 사실은
실제 OpenSQL, 검색 품질, ANN 또는 장애 복구가 통과했다는 뜻이 아니다.

## 상태 어휘

- `PASS`: 해당 release SHA에서 실제 명령이 성공하고 요구 증거가 모두 존재한다.
- `FAIL`: 명령은 실행됐지만 정해 둔 기준을 충족하지 못했다.
- `BLOCKED`: 필요한 제품, 환경, 권한 또는 입력이 없어 검증을 실행하지 못했다.
- `TIMEBOX_EXPIRED`: 정해 둔 stop-loss 안에 완료하지 못했다.
- `SKIPPED`: capability가 없거나 해당 release에서 의도적으로 채택하지 않았다.

`POSTGRES_REFERENCE`는 `ACTUAL_OPENSQL_SINGLE_NODE`로 승격할 수 없다. worker 중단 복구는
multi-node HA나 database failover 증거가 아니다.

## 새 run 만들기

```sh
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
python3 evidence/tools/evidence_bundle.py init \
  "evidence/round1/lane-c/$run_id" \
  --run-id "$run_id"
```

생성되는 디렉터리는 다음과 같다.

```text
00-source/
01-repository-checks/
02-qualification-schema/
03-opensql-smoke/
04-ann/
05-outage-recovery/
06-evaluation/
07-grounding/
99-final/
```

모든 `result.json`은 [`../schemas/result.schema.json`](../schemas/result.schema.json)의
공통 필드를 사용한다. Python 의존성을 추가하지 않는 검증 명령은 다음과 같다.

```sh
python3 evidence/tools/evidence_bundle.py validate path/to/result.json
```

## 봉인

실제로 실행한 task만 required 목록에 넣는다. 누락되거나 non-PASS인 task가 있으면 봉인은
그 상태를 보존하며 PASS로 승격하지 않는다.

```sh
python3 evidence/tools/evidence_bundle.py finalize \
  "evidence/round1/lane-c/$run_id" \
  --required-task C0_SOURCE_BASELINE \
  --required-task C3_OPENSQL_SMOKE \
  --required-task C6_RETRIEVAL_EVALUATION
```

도구는 다음을 수행한다.

- 모든 result의 공통 schema와 다섯 repository full SHA를 확인한다.
- 서로 다른 source revision을 섞은 bundle을 거부한다.
- 명백한 private key, bearer credential 및 password류가 든 텍스트 증거를 거부한다.
- `99-final/evidence-index.json`과 `99-final/SHA256SUMS`를 만든다.
- claim matrix header와 증거 수를 기록한다.

checksum은 해당 run directory에서 검증한다.

```sh
cd "evidence/round1/lane-c/$run_id"
shasum -a 256 -c 99-final/SHA256SUMS
```

이 secret 검사는 최종 Lane A repository/history/archive scanner를 대체하지 않는다.

## Claim matrix

`99-final/claim-matrix.csv`의 한 행은 report/video에서 실제로 사용할 한 claim이다.
각 행은 wording, tag/SHA, evidence path, 재현 명령, 기대값, 관측값, timestamp, 검증자,
상태를 기록한다. fresh tagged-release `PASS`가 아닌 내용은 report와 video에 넣지 않는다.

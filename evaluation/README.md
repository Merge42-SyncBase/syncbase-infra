# Round-1 retrieval evaluation

`queries.round1.draft.json`은 public sample PDF에서 만든 **CALIBRATION 전용** 후보 ground
truth다. threshold 선택과 DRAFT runtime diagnostic 과정에서 30개 query text가 모두 관측됐으므로
prospective holdout이나 benchmark PASS 근거로 사용할 수 없다. 실패를 포함한 calibration 기록으로
그대로 보존하며 query를 삭제하거나 바꾸어 결과를 개선하지 않는다.

F01-F10과 I01-I05는 active source SHA-256, 1-based PDF page, 짧은 근거 문구를 갖는다.
V01-V05는 자연 발생 version pair가 확인되지 않아 실제 public PDF를 V1으로 사용하고 canonical
marker page 한 쪽을 append한 synthetic V2 fixture다. 다섯 V2가 active가 되므로 동일 logical
document를 사용하는 F01/F02/F03/F09/F10/I01/I02/I03/I04도 V2 hash/version 2를 target으로
삼되, byte-prefix로 보존된 기존 원본 page/excerpt를 그대로 사용한다. V1은 각 fixture record와
V01-V05의 forbidden target에만 남는다.

`queries.template.json`은 빈 scaffold로 남겨 둔다. draft나 template의 존재는 benchmark
실행 또는 PASS 증거가 아니다.

## 0. Evidence-tool Python environment

평가 도구는 application image와 분리된 Python 환경에서 실행한다. Codex workspace의 bundled
Python을 사용할 수 없는 reviewer는 repository root에서 다음과 같이 격리된 환경을 만든다.

```sh
python3 -m venv .venv-round1-evaluation
.venv-round1-evaluation/bin/python -m pip install \
  -r evaluation/requirements.txt
export BUNDLED_PYTHON="$PWD/.venv-round1-evaluation/bin/python"
```

`evaluation/requirements.txt`는 현재 검증한 top-level package version을 고정한다. V2 render를
재생성하는 경우에는 별도로 `pdftoppm` 실행 파일도 필요하다. 이미 seal된 PDF/PNG와 holdout을
검토하거나 evaluator/collector tests를 실행하는 데 Codex 전용 runtime 경로를 전제로 하지 않는다.

`queries.round1.holdout.draft.json`은 별도로 작성한 **PROSPECTIVE_HOLDOUT 후보**다.
calibration과 겹치지 않는 새 query text 30개와 factual/identifier evidence fingerprint를
사용하지만 아직 `DRAFT`, `NOT_RUN`, human `PENDING`, RC binding `null`이다. 따라서 현재
상태는 holdout PASS나 release evidence가 아니다. 사람이
`holdout-ground-truth-verification.md`를 전부 검토하고 dataset을 freeze하기 전에는 REST,
MCP, embedding/debug endpoint 또는 다른 retrieval runtime에 한 건도 보내지 않는다.

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

## 1. 후보 ground truth 검증

contest workspace root에서 bundled `pdfplumber`가 설치된 Python으로 실행한다.

```sh
BUNDLED_PYTHON="${BUNDLED_PYTHON:-python3}"

"$BUNDLED_PYTHON" syncbase-infra/evaluation/evaluate_retrieval.py validate-draft \
  syncbase-infra/evaluation/queries.round1.draft.json \
  --source-root .
```

이 명령은 PDF 바이트 hash, page 범위와 extracted excerpt를 다시 확인한다. 성공 출력은
`DRAFT_VALID`, `benchmark_result=NOT_RUN`, `freeze_ready=false`다. PDF extraction은
layout proof가 아니므로 worksheet의 원본 페이지 사람 확인을 대체하지 않는다.
READY fixture V1 hash/version 1이 factual/identifier active target에 하나라도 남아 있거나,
V2 target이 version 2/source file/original page와 일치하지 않으면 `validate-draft`가 실패한다.

prospective holdout의 PDF/excerpt 구조와 calibration 분리도 runtime 호출 없이 검증한다.

```sh
"$BUNDLED_PYTHON" syncbase-infra/evaluation/evaluate_retrieval.py validate-draft \
  syncbase-infra/evaluation/queries.round1.holdout.draft.json \
  --source-root .

"$BUNDLED_PYTHON" syncbase-infra/evaluation/validate_holdout_integrity.py \
  syncbase-infra/evaluation/queries.round1.draft.json \
  syncbase-infra/evaluation/queries.round1.holdout.draft.json
```

두 번째 명령은 Unicode NFKC/case-fold/whitespace normalization 후 query overlap `0`,
factual/identifier `(source SHA-256, page, excerpt)` overlap `0`, 서로 다른 dataset ID/file,
calibration file/query-set hash binding, 10/5/5/10 분포, 동일 threshold/profile/fixture 및
citation metric contract, 그리고 holdout의 `NOT_QUERIED`/`PENDING`/null RC binding을 검사한다.
성공 label `HOLDOUT_DRAFT_INTEGRITY_VALID`는 정적 DRAFT 무결성만 뜻하며 benchmark PASS가
아니다. 이 명령의 기본 stage는 초기 후보에만 적용되는 `draft`이며, release binding과 사람
승인을 채운 뒤에는 아래의 `--stage pre-freeze` gate를 사용한다.

## 2. Deterministic V2 fixture 재생성

V01-V05의 plan status `READY`와 query state `VERIFIED_VERSION_PAIR`는 **machine-ready**를
뜻할 뿐 human approval이나 release claim을 뜻하지 않는다. dataset status는 `DRAFT`,
`human_verification.status`는 `PENDING`이며 manifest도 `claim_eligible=false`다.

fixture generator는 다섯 V1의 SHA-256/page 수를 먼저 전부 검증한 뒤에만 출력을 만든다.
ReportLab `invariant=1`과 repository에 pin한 Nanum Gothic을 사용해 canonical A4 marker page를
두 번 만들고, pypdf incremental append 결과가 byte-identical인지 확인한다. V1 원본 bytes는
V2의 exact prefix로 유지한다. 마지막으로 appended page text를 추출하고 `pdftoppm`으로 PNG를
render한다. `READY` plan의 V2 hash와 새 출력이 다르면 아무 fixture도 교체하지 않고 실패한다.

contest workspace root에서 bundled Python으로 실행한다.

```sh
BUNDLED_PYTHON="${BUNDLED_PYTHON:-python3}"

"$BUNDLED_PYTHON" syncbase-infra/evaluation/generate_version_fixtures.py \
  --dataset syncbase-infra/evaluation/queries.round1.draft.json \
  --source-root . \
  --output-dir syncbase-infra/evaluation/fixtures/version-sensitive/pdfs \
  --render-dir syncbase-infra/evaluation/fixtures/version-sensitive/renders \
  --temp-root syncbase-infra/tmp/pdfs \
  --manifest syncbase-infra/evaluation/fixtures/version-sensitive/manifest.json
```

생성물은 다음 위치에 있다.

- `evaluation/fixtures/version-sensitive/pdfs/`: V01-V05 synthetic V2 PDF
- `evaluation/fixtures/version-sensitive/renders/`: 각 V2의 final-page PNG
- `evaluation/fixtures/version-sensitive/manifest.json`: V1/V2/render hash와 machine checks

사람 검토자는 다섯 V1/V2 PDF와 다섯 PNG를 직접 열고 원본 V1 페이지 보존, appended page 수,
marker/Korean text의 가독성을 확인한 뒤 worksheet의 V1/V2 checkbox를 채워야 한다. 이 검토를
generator, extracted text, render 존재 여부 또는 agent visual QA로 대체하면 안 된다.

## 3. DRAFT/local REST 관측 수집

`collect_draft_observations.py`는 `dataset_role=CALIBRATION`인 unfinished draft에만 사용한다. frozen
benchmark runner가 아니며 metric, PASS/FAIL 또는 release claim을 만들지 않는다. 기본값은
human-pending V01-V05를 호출하지 않고 F01-F10, I01-I05, N01-N10의 REST 응답 25건을
dataset 순서대로 보존한다. 이 기본 동작은 `25_QUERY_DRAFT_DIAGNOSTIC`이다.

`--include-ready-version-cases`를 명시하면 V01-V05를 포함해 30건을 dataset 순서대로
호출한다. 이 opt-in은 다섯 V query가 모두 `VERIFIED_VERSION_PAIR`이고 연결된 fixture plan이
모두 `READY`일 때만 허용된다. 한 건이라도 pending/non-ready이면 HTTP 호출과 output 생성 전에
거부한다. `human_verification.status=PENDING`인 DRAFT에서 calibration 목적으로 실행할 수 있지만,
결과는 여전히 `30_QUERY_DRAFT_DIAGNOSTIC`, `NOT_EVALUATED`, `claim_eligible=false`,
`release_eligible=false`다. V case의 hit나 source mapping도 점수 조정, 삭제 또는 metric 계산 없이
다른 query와 같은 raw observation 구조로 보존한다.

collector는 loopback HTTP origin만 허용하고 Netscape/curl cookie jar에서 session을 읽는다.
cookie 값은 argv, stdout/stderr 또는 JSON에 기록하지 않는다. 문서 목록의 각 active
Version에 대해 authenticated `raw.pdf`를 내려받아 SHA-256을 계산하고, 검색 hit의
`document_id`, `version_id`, `document_version`을 그 hash에 연결한다. 같은 hash라도 draft가
요구하는 Version과 active Version이 다르면 `SOURCE_PRESENT_WRONG_VERSION`으로 기록하고
artifact 전체를 `INCOMPLETE`로 남긴다.

`dataset_role=PROSPECTIVE_HOLDOUT`은 DRAFT 상태에서 flag와 관계없이 HTTP client를 만들기
전에 hard-refuse한다. 이 보호 장치를 우회하거나 holdout 파일을 calibration으로 복사/재라벨링해
호출하면 안 된다. prospective holdout은 human approval과 RC binding을 마친 뒤 frozen runner가
정확히 한 번 실행한다.

contest workspace root에서 bundled Python으로 실행한다.

```sh
BUNDLED_PYTHON="${BUNDLED_PYTHON:-python3}"
run_id=20260825-draft-calibration-01

"$BUNDLED_PYTHON" syncbase-infra/evaluation/collect_draft_observations.py \
  --dataset syncbase-infra/evaluation/queries.round1.draft.json \
  --source-root . \
  --base-url http://127.0.0.1:18080 \
  --session-cookie-file /path/to/mode-0600/curl-cookie.jar \
  --output "syncbase-infra/evaluation/runtime/$run_id/draft-observations.json"
```

machine-ready V01-V05를 포함하는 명시적 30-query DRAFT diagnostic은 별도 output으로 실행한다.

```sh
"$BUNDLED_PYTHON" syncbase-infra/evaluation/collect_draft_observations.py \
  --dataset syncbase-infra/evaluation/queries.round1.draft.json \
  --source-root . \
  --base-url http://127.0.0.1:18080 \
  --session-cookie-file /path/to/mode-0600/curl-cookie.jar \
  --include-ready-version-cases \
  --output "syncbase-infra/evaluation/runtime/$run_id/draft-observations-30.json"
```

출력에는 다음 stop label이 항상 포함된다.

```text
artifact_kind=DRAFT_RETRIEVAL_OBSERVATIONS
diagnostic_scope=25_QUERY_DRAFT_DIAGNOSTIC 또는 30_QUERY_DRAFT_DIAGNOSTIC
evidence_grade=DIAGNOSTIC
artifact_status=DRAFT_LOCAL_ONLY_NOT_RELEASE_EVIDENCE
benchmark_result=NOT_EVALUATED
claim_eligible=false
release_eligible=false
```

`evaluation/runtime/`은 의도적으로 git-ignore한다. `--purpose release`, FROZEN dataset,
credential이 포함된 URL 또는 non-loopback origin은 HTTP 호출 전에 거부한다. release
evidence는 human review, V2 fixture와 모든 RC binding이 완료된 뒤
`collect_frozen_observations.py`로 새 clean corpus에서 정확히 한 번 수집한다.

## 4. Prospective holdout 완성 후 freeze

`queries.round1.draft.json`은 이미 노출된 CALIBRATION dataset이므로 이 절의 freeze 입력으로
사용하지 않는다. 별도의 `PROSPECTIVE_HOLDOUT` draft가 새로운 30개 query text와 evidence를
갖고 아래 조건을 모두 만족할 때만 freeze한다. holdout query는 freeze 전에 runtime에 보내지
않는다.

현재 prospective 후보와 검토 문서는 다음과 같다.

- `evaluation/queries.round1.holdout.draft.json`
- `evaluation/holdout-ground-truth-verification.md`

현재 두 파일은 machine-valid draft일 뿐 human-approved holdout이 아니다.

### 4.1 Binding 공식과 zero-query preflight

JSON 기반 release binding 세 개는 임의의 64자리 문자열이 아니다. collector가 사용하는
canonical JSON은 UTF-8, object key 정렬, `ensure_ascii=false`, separator `(',', ':')`,
trailing data 없음으로 고정한다. artifact의 `canonicalization` label은
`JSON_UTF8_SORT_KEYS_COMPACT_V1`이다.

- `corpus_sha256`: `SYNCBASE_ACTIVE_CORPUS_V1` payload의 canonical JSON SHA-256. payload는
  runtime의 **모든 active source**에 대한 `document_id`, `version_id`, `source_sha256`,
  `version`, `active`, 실제 PDF `page_count`, `raw_pdf_artifact`, `raw_pdf_sha256`를 담고
  `(source_sha256, version, document_id, version_id)` 순으로 정렬한다.
- `source_release_sha256`: 다섯 repository의 full 40자리 commit SHA를 담은
  `SYNCBASE_SOURCE_RELEASE_V1` payload의 canonical JSON SHA-256. repository key는
  `frontend`, `embedding`, `was`, `infra`, `mcp`다.
- `database_identity_sha256`: 아래 public-safe `SYNCBASE_DATABASE_IDENTITY_V1` payload의
  canonical JSON SHA-256이다. 세 하위 digest를 만든 원본 식별 자료도 별도 evidence로
  보존해야 한다.
- `model_sha256`, `tokenizer_sha256`: release runtime에 실제 mount한 model/tokenizer file의
  exact bytes SHA-256이다. collector가 두 파일을 다시 읽고 sealed evidence copy의 digest도
  재검증한다.
- `profile_sha256`: `syncbase-was/ops/profile-fingerprint.sh`와 Go runtime이 사용하는 exact
  `SYNCBASE_RETRIEVAL_PROFILE_CANONICAL_V1` JSON bytes의 SHA-256이다. pretty-print, trailing
  newline 또는 숫자 표기 변경도 다른 artifact다. collector는 fixed parser/chunker/model/runtime
  schema, model/tokenizer hash 연결, 6자리 `minimum_score` 표기를 검증한다. script 출력의
  `canonical_json=` 뒤 값을 prefix와 newline 없이 exact profile artifact로 보존한다. Round-1
  pre-freeze gate가 허용하는 locked `0.93` profile fingerprint는
  `7ad8a410ab8e1e9d869b116f774bea160bd7b9630fa145582d27297181edcf26`뿐이다.

```json
{
  "schema_version": "1.0",
  "binding_kind": "SYNCBASE_DATABASE_IDENTITY_V1",
  "environment_id_sha256": "<stable environment identifier UTF-8 bytes의 SHA-256>",
  "database_name_sha256": "<exact database name UTF-8 bytes의 SHA-256>",
  "migration_head_sha256": "<검증한 exact migration-head artifact bytes의 SHA-256>"
}
```

이 database identity 공식은 같은 검증 환경을 다시 식별하기 위한 release binding일 뿐이다.
OpenSQL 제품명/버전 또는 qualification 성공을 증명하지 않으며, 그런 주장은 별도의 actual
product evidence가 있어야 한다.

현재 holdout draft에는 먼저 다섯 `repository_revisions`를 실제 RC commit SHA로 채운다.
나머지 release hash는 `null`이어도 preflight할 수 있다. 위 schema의 public-safe database
identity JSON, release runtime에 실제 사용한 model/tokenizer file, exact canonical profile
artifact를 준비한 뒤, holdout query를 보내지 않는 다음 명령을 새/빈 evidence directory에
실행한다.

```sh
BUNDLED_PYTHON="${BUNDLED_PYTHON:-python3}"
run_id=20260825-round1-rc01

"$BUNDLED_PYTHON" syncbase-infra/evaluation/collect_frozen_observations.py \
  --mode preflight \
  --dataset syncbase-infra/evaluation/queries.round1.holdout.draft.json \
  --base-url http://127.0.0.1:18080 \
  --expected-source-origin https://evidence.syncbase.example \
  --session-cookie-file /path/to/mode-0600/curl-cookie.jar \
  --database-identity-json /path/to/public-safe/database-identity.json \
  --model-artifact /path/to/release-runtime/model.onnx \
  --tokenizer-artifact /path/to/release-runtime/tokenizer.json \
  --profile-artifact /path/to/release-runtime/retrieval-profile.canonical.json \
  --evidence-dir "syncbase-infra/evidence/round1/lane-c/$run_id/00-holdout-preflight"
```

`preflight`는 document list/detail과 모든 active `raw.pdf`만 읽고 `/api/v1/search`는 **0회**
호출하며 `query-exposure.json`도 만들지 않는다. active corpus가 holdout expected source/version
집합과 정확히 같아야 하고, 각 PDF의 bytes hash와 실제 page count가 runtime metadata와 맞아야
한다. 성공 directory에는 다음 파일이 남는다.

```text
00-holdout-preflight/
  binding-formulas.json
  collection-status.json
  corpus-manifest.json
  preflight.json
  retrieval-artifacts.json
  runtime-artifacts/model-<sha256>.onnx
  runtime-artifacts/profile-<sha256>.json
  runtime-artifacts/tokenizer-<sha256>.json
  sources/<source_sha256>-v<version>.pdf
```

`binding-formulas.json.recommended_bindings`의 여섯 값을 draft의 `corpus_sha256`,
`database_identity_sha256`, `source_release_sha256`, `model_sha256`, `tokenizer_sha256`,
`profile_sha256`에 옮긴다. repository revision도 각 sealed source release에 맞게 완성한다.
preflight artifact를 수정해 hash를 맞추거나, 다른 corpus에서 얻은 값을 재사용하면 안 된다.
frozen collector는 같은 공식을 다시 계산해 source-release/database/model/tokenizer/profile
mismatch는 HTTP 전에, corpus mismatch는 모든 raw PDF preflight 후 query exposure 전에
거부한다. preflight가 위 locked profile fingerprint와 다른 값을 산출하면 해당 runtime은 이
holdout의 release candidate가 아니며 `--stage pre-freeze`가 거부한다.

다음 조건을 전부 만족한 뒤, 검색 결과를 보기 전에만 freeze한다.

- worksheet 전 case와 no-answer corpus-wide 검토 완료
- `human_verification.status=APPROVED`, reviewer와 UTC 시각 기록
- machine manifest와 실제 V2 hash를 다시 대조
- V2 PDF 및 appended-page render를 사람이 확인하고 worksheet의 V1/V2 행 승인
- 각 V query의 relevant V2 hash/page와 forbidden V1 hash가 plan/manifest와 일치하는지 확인
- corpus, model, tokenizer, profile, database identity, source release 및 다섯 repository RC SHA 기록
- `benchmark_claim=NOT_RUN` 유지
- holdout이 runtime에 전혀 노출되지 않았음을 다시 확인한 뒤
  `query_exposure=NOT_QUERIED_BEFORE_FREEZE`로 기록

모든 값을 채우고 사람이 worksheet를 승인한 뒤, **freeze 명령 직전** calibration 분리 gate를
release-bound 상태로 다시 실행한다. 기본 `draft` stage와 달리 이 stage는 DRAFT 상태,
`APPROVED`, 완전한 binding, `NOT_RUN`, `NOT_QUERIED_BEFORE_FREEZE`를 요구하면서도 calibration
file/query/evidence overlap을 다시 검사한다. 이 명령도 runtime을 호출하지 않는다.

```sh
"$BUNDLED_PYTHON" syncbase-infra/evaluation/validate_holdout_integrity.py \
  --stage pre-freeze \
  syncbase-infra/evaluation/queries.round1.draft.json \
  syncbase-infra/evaluation/queries.round1.holdout.draft.json
```

성공 label은 `HOLDOUT_PRE_FREEZE_INTEGRITY_VALID`이다. 이 label은 freeze 직전 정적 무결성
gate일 뿐 benchmark 결과가 아니다.

```sh
"$BUNDLED_PYTHON" syncbase-infra/evaluation/evaluate_retrieval.py freeze \
  syncbase-infra/evaluation/queries.round1.holdout.draft.json \
  --calibration syncbase-infra/evaluation/queries.round1.draft.json \
  --source-root . \
  --output syncbase-infra/evaluation/queries.round1.frozen.json
```

freeze는 10/5/5/10 구성, exact thresholds, page ground truth, version 금지 대상, RC hash
bindings, V2 fixture readiness와 human approval뿐 아니라 `--source-root` 아래 PDF bytes/page/excerpt를
다시 검사한 뒤 `dataset_sha256`을 기록한다.
pending/null/placeholder가 하나라도 있으면 실패한다. evaluator는
`NOT_QUERIED_AT_DRAFT_CREATION`을 더 강한 pre-freeze 선언으로 자동 승격하지 않는다.
freeze 직전의 실제 미노출 상태를 사람이 확인해야 하며, malformed/future
`reviewed_at` 또는 `frozen_at` UTC timestamp도 거부한다.

## 5. 검색 관측값 수집

`collect_frozen_observations.py --mode collect`는 FROZEN `PROSPECTIVE_HOLDOUT` 하나를 위한
one-shot exact collector다. `benchmark_claim=NOT_RUN`, 유효한 `dataset_sha256`, frozen binding
공식 일치, loopback plain HTTP origin, 현재 사용자 소유의 regular mode-`0600` cookie jar,
새/빈 evidence directory를 모두 요구한다. acknowledgment에는 frozen file의
`dataset_sha256` 값을 정확히 넣는다. `--expected-source-origin`은 credential/path/query/fragment가
없는 명시적 HTTP(S) origin이다. hit의 relative `source_url`은 허용하지만 absolute URL이면 이
origin과 정확히 같아야 하며, hit에서 origin을 추론하지 않는다.

```sh
"$BUNDLED_PYTHON" syncbase-infra/evaluation/collect_frozen_observations.py \
  --mode collect \
  --dataset syncbase-infra/evaluation/queries.round1.frozen.json \
  --base-url http://127.0.0.1:18080 \
  --expected-source-origin https://evidence.syncbase.example \
  --session-cookie-file /path/to/mode-0600/curl-cookie.jar \
  --database-identity-json /path/to/public-safe/database-identity.json \
  --model-artifact /path/to/release-runtime/model.onnx \
  --tokenizer-artifact /path/to/release-runtime/tokenizer.json \
  --profile-artifact /path/to/release-runtime/retrieval-profile.canonical.json \
  --evidence-dir "syncbase-infra/evidence/round1/lane-c/$run_id/06-evaluation" \
  --acknowledge-one-shot-exposure "<frozen dataset_sha256>"
```

collector는 먼저 active corpus 전체를 list/detail/download하고, raw PDF hash/page count와 모든
expected source/version binding을 확인한다. 이 단계가 하나라도 실패하면 search 요청은 0회이며
`exact-observations.json`도 없다. 모든 preflight가 끝난 뒤 첫 search 직전에
`query-exposure.json`을 atomic/exclusive write한다. 이 marker가 생기면 성공/실패와 관계없이 같은
evidence directory로 rerun할 수 없다.

search는 frozen 순서 그대로 30건에 대해 각 query를 정확히 한 번, `limit=5`로 호출한다.
중간 실패 시 성공한 raw observation을 `partial-progress.json`에 남기고
`collection-status.json`을 terminal `INCOMPLETE`, non-claim 상태로 만든다. marker나 incomplete
artifact를 삭제해 재시도하지 않는다. release protocol을 정당하게 다시 시작해야 한다면 새
frozen dataset/corpus/run ID/evidence directory를 만들고 그 사유를 기록한다. stdout/stderr에는
credential, query result, snippet 또는 HTTP error body를 출력하지 않는다.

성공 evidence directory는 최소 다음 구조다.

```text
06-evaluation/
  binding-formulas.json
  collection-status.json
  corpus-manifest.json
  exact-observations.json
  partial-progress.json
  preflight.json
  query-exposure.json
  retrieval-artifacts.json
  runtime-artifacts/model-<sha256>.onnx
  runtime-artifacts/profile-<sha256>.json
  runtime-artifacts/tokenizer-<sha256>.json
  sources/<source_sha256>-v<version>.pdf
```

collector의 `COMPLETE`는 수집/contract 완료만 뜻한다. 모든 artifact는 여전히
`benchmark_result=NOT_EVALUATED`, `claim_eligible=false`이며, 아래 evaluator를 별도로 실행하기
전에는 PASS를 주장할 수 없다.

exact 결과 파일은 frozen query 순서를 그대로 유지하고,
`round1-citation-provenance-v1` contract의 `retrieval_limit=5`를 기록한다. claim-grade
frozen runner는 runtime identity와 sealed raw PDF를 `source_bindings`로 연결해야 한다.
`raw_pdf_artifact`는 평가 명령의 `--evidence-root`를 기준으로 한 safe relative PDF
path이며, symlink를 포함해 resolve한 경로가 evidence root를 벗어나면 citation check가
실패한다.

```json
{
  "schema_version": "1.0",
  "dataset_sha256": "...",
  "retrieval_mode": "exact",
  "retrieval_limit": 5,
  "source_origin": "https://evidence.syncbase.example",
  "bindings": {"same": "object as frozen dataset"},
  "source_bindings": [
    {
      "document_id": "document-uuid",
      "version_id": "version-uuid",
      "source_sha256": "<lowercase SHA-256>",
      "version": 2,
      "active": true,
      "page_count": 11,
      "raw_pdf_artifact": "sources/<sha256>-v2.pdf",
      "raw_pdf_sha256": "<lowercase SHA-256>"
    }
  ],
  "queries": [
    {
      "id": "F01",
      "latency_ms": 12.4,
      "grounding_status": "SUPPORTED",
      "grounding_reason": null,
      "results": [
        {
          "rank": 1,
          "document_id": "document-uuid",
          "version_id": "version-uuid",
          "source_sha256": "<lowercase SHA-256>",
          "version": 2,
          "page": 3,
          "score": 0.81,
          "snippet": "returned evidence text",
          "source_url": "/sources/document-uuid/versions/2?page=3"
        }
      ]
    }
  ]
}
```

`source_bindings` 내 `version_id` 및 `(document_id, version)`은 유일해야 하며, 모든
returned hit은 위의 provenance field를 누락 없이 갖춘다. evaluator는 service가 제공하는
`citation_verified` 같은 Boolean을 신뢰하지 않고, sealed PDF bytes의 SHA-256,
실제 page 범위, 해당 page에서 독립 추출한 normalized snippet, 그리고 URL의
document/version/page tuple을 직접 검사한다.

no-answer safety가 동작했다면 `grounding_status`는 `INSUFFICIENT_EVIDENCE`, `results`는
빈 배열이어야 하며 `grounding_reason`은 `NO_HITS_ABOVE_POLICY`,
`ONLY_INACTIVE_VERSION_MATCHED`, `SOURCE_UNAVAILABLE` 중 하나여야 한다. harness는 결과를
임의로 후처리해 숨기지 않는다.

## 6. 평가

```sh
"$BUNDLED_PYTHON" syncbase-infra/evaluation/evaluate_retrieval.py evaluate \
  syncbase-infra/evaluation/queries.round1.frozen.json \
  syncbase-infra/evidence/round1/lane-c/$run_id/06-evaluation/exact-observations.json \
  --evidence-root syncbase-infra/evidence/round1/lane-c/$run_id/06-evaluation \
  --output syncbase-infra/evidence/round1/lane-c/$run_id/06-evaluation/result.json \
  --run-id "$run_id"
```

`--evidence-root`는 필수다. 위 예시에서 `raw_pdf_artifact` 값
`sources/<sha256>-v2.pdf`는
`syncbase-infra/evidence/round1/lane-c/$run_id/06-evaluation/sources/<sha256>-v2.pdf`로
해석된다.

`citation_page_correctness`는 모든 category에서 반환된 모든 top-5 hit occurrence를
분모에 넣고 다섯 provenance check를 전부 통과한 hit만 올바른 것으로 계산한다.
전체 zero-hit run은 vacuous PASS가 아닌 `0.0`이다. ANN 결과가 실제로 존재할 때만
`--ann ann-observations.json`을 추가하며, exact와 ANN에 동일한 citation contract를
적용한다.

기존 DRAFT collector artifact는 `retrieval_limit`, claim-grade raw PDF binding 및 모든 hit
provenance field를 갖추지 않았으므로 새 contract의 frozen evaluation 입력과 호환되지
않는다. 특히 보존된 `18/47 = 0.382979` 결과는
`legacy_same_source_version_page_precision_at_5`이라는 non-gating 진단으로 계속
공개하되, 그 historical artifact에 새 metric을 사후 추정해 채우지 않는다.
해당 artifact의 새 metric status는 정확히
`NOT_MEASURED_LEGACY_DIAGNOSTIC`이다.

FAIL query를 삭제하거나 threshold를 낮추지 않는다. `queries.template.json`과 unit-test
fixture의 존재는 실제 benchmark 실행 증거가 아니다. 의미 계약과 migration
정책은 [`2026-OSS-Round1-citation-metric-contract-addendum.md`](../../documents/2026-OSS-Round1-citation-metric-contract-addendum.md)에
기록한다.

# Round-1 retrieval calibration ground-truth verification worksheet

상태: **CALIBRATION DRAFT / HUMAN REVIEW PENDING / NOT EVALUATED**

이 문서는 `queries.round1.draft.json`의 후보 ground truth를 사람이 원본 PDF와 대조하기 위한
worksheet다. `pdfplumber` 텍스트 추출 결과와 렌더링한 페이지를 함께 확인해 후보를 작성했지만,
텍스트 추출은 PDF 레이아웃이나 사람의 최종 판단을 증명하지 않는다. 체크박스가 모두 채워지고
reviewer와 UTC 검토시각이 dataset에 기록되기 전에는 승인하면 안 된다.

이 dataset의 30개 query text는 threshold calibration 및 DRAFT runtime diagnostic에서 이미
관측되었다. 따라서 이 worksheet를 완료해도 prospective holdout이나 benchmark PASS 근거로
freeze할 수 없다. calibration 진단과 새로운 holdout의 evidence selection을 돕기 위해 그대로
보존하며, 실패 query를 삭제하거나 query를 바꾸어 결과를 개선하지 않는다.

## 검토자 승인 조건

- [ ] 원본 PDF를 직접 열어 파일명과 문서 제목이 일치하는지 확인했다.
- [ ] 아래 15개 factual/exact case의 실제 PDF 페이지와 짧은 근거 문구를 확인했다.
- [ ] PDF에 인쇄된 페이지 번호가 아니라 **PDF 파일의 1-based page index**를 사용했음을 확인했다.
- [ ] 10개 no-answer 질문이 최종 corpus 전체에 실제로 없는지 별도로 검색했다.
- [ ] V01-V05의 V1 파일 hash/page 수와 원본 페이지가 manifest 및 plan과 일치함을 확인했다.
- [ ] V01-V05의 synthetic V2 PDF를 각각 열어 V1 전체 페이지 뒤에 marker page가 정확히 한 쪽만
      추가되었고, 아래 Korean marker/text가 잘리지 않고 읽히는지 확인했다.
- [ ] RC의 corpus/model/profile/database/source-release 및 5개 repository SHA를 기록했다.
- [ ] 검토 후 dataset의 `human_verification.status`를 `APPROVED`로 바꾸고 reviewer/UTC 시각을 기록했다.

검토자: ____________________  검토 UTC 시각: ____________________

## Factual/paraphrase 10건

| 확인 | ID | Active ground-truth 파일 | PDF page | 짧은 근거 문구 |
|---|---|---|---:|---|
| [ ] | F01 | `V01-synthetic-supersession-v2.pdf` | 1 | `개방형 직위의 운영 등에 필요한 사항을 규정함을 목적으로 한다.` |
| [ ] | F02 | `V02-synthetic-supersession-v2.pdf` | 2 | `필요한 경력, 학력 또는 자격증 소지자를 채용함을 원칙으로 한다.` |
| [ ] | F03 | `V03-synthetic-supersession-v2.pdf` | 2 | `규정안의 입안은 당해 안건의 소관부서에서 함을 원칙으로 한다.` |
| [ ] | F04 | `노동이사후보_선거관리_내규_20260703.pdf` | 1 | `입후보자 등록, 자격심사, 사퇴수리 및 기호추첨` |
| [ ] | F05 | `보수규정_20260703.pdf` | 1 | `기본급과 제수당을 말한다.` |
| [ ] | F06 | `복지후생규정_20260703.pdf` | 1 | `이 규정은 공단의 직원에 대하여 적용한다.` |
| [ ] | F07 | `사무위임전결규정_20260703.pdf` | 2 | `중요하고 이례적인 사항은 직상급 직위자의 결재를 득하여 시행` |
| [ ] | F08 | `시설관리직직원관리규정_20260703.pdf` | 2 | `근무성적평정, 경력평정, 교육훈련평정, 가감평정` |
| [ ] | F09 | `V04-synthetic-supersession-v2.pdf` | 3 | `구성위원 과반수의 출석과 출석위원의 과반수` |
| [ ] | F10 | `V05-synthetic-supersession-v2.pdf` | 2 | `입학금, 기성회비, 수업료를 포함한다.` |

## Exact identifier 5건

| 확인 | ID | Active ground-truth 파일 | PDF page | 실제 식별자/표제 |
|---|---|---|---:|---|
| [ ] | I01 | `V01-synthetic-supersession-v2.pdf` | 7 | `<별지 제1호 서식> 개방형직위 임용자 성과계획서` |
| [ ] | I02 | `V02-synthetic-supersession-v2.pdf` | 20 | `[별지 제2호의3서식] 촉탁계약직원(청소/주차) 근로계약서` |
| [ ] | I03 | `V04-synthetic-supersession-v2.pdf` | 7 | `<별지 제3호 서식> 직무윤리 사전진단서` |
| [ ] | I04 | `V05-synthetic-supersession-v2.pdf` | 7 | `<별표 1> 환수금액 산정기준표` |
| [ ] | I05 | `보수규정_20260703.pdf` | 13 | `[별표 4] 통상임금 및 평균임금` |

## Source SHA-256 manifest

아래 값은 synthetic V2의 변경되지 않은 byte prefix로 보존되는 public base V1 PDF의 SHA-256이다.
파일이 다시 다운로드되거나 바뀌면 페이지 내용이 같아 보여도 새 corpus로 취급하고 ground truth를
다시 검토한다. V01-V05와 같은 logical document의 F/I active target은 아래 V1이 아니라 이 문서
뒤쪽 표의 V2 SHA/version 2를 사용한다.

| 원본 파일 | SHA-256 |
|---|---|
| `개방형직위운영내규_20260703.pdf` | `13ff66350199550d8e384252d6ed5665ddd58e09b3ce3b8ba996a2fd61940d71` |
| `계약직원운영관리내규_20260703.pdf` | `4664832b612072d32fc746de914bcecc7a22397687e273af186911e0f286bbb1` |
| `규정관리규정_20260703.pdf` | `d10b7f0392b95de3b65c4da74a06354e4d0b6214bb773c1d86c6fed5c1498a7b` |
| `노동이사후보_선거관리_내규_20260703.pdf` | `2d7d49292f7831663822dbd75f5f64d4007211f434f01858f52800037d6f3b54` |
| `보수규정_20260703.pdf` | `325bf6c654dd9441843e87764d7a49b7bbe19a6564b7b24b66c455a3d2b6380b` |
| `복지후생규정_20260703.pdf` | `03e4525d3ddbbba3d6619e0997d7fc6047092826c17fc6ed1606c6f9f90b5550` |
| `사무위임전결규정_20260703.pdf` | `91a5a4f0ea97d589e1e5868aa10f2e365933b7d8fb5022b713bdb105e1932e6b` |
| `시설관리직직원관리규정_20260703.pdf` | `47be4aec93ed9d515a0fa55260d1b9b88307a3feee0f19e197138663405dab64` |
| `위원회_관리_규정_20260703.pdf` | `82b8206478750a0c6b3cb337da395a52a9918514617c243b58648feeda15ab9d` |
| `직원_학위취득_지원관리내규_20260703.pdf` | `4cd1a62fb0802c7802d05b45ef319d735ce1712f9420f8d2ddfb9be3b6829dff` |

## Version-sensitive V1/V2 fixture gate

현재 공개 corpus에는 아래 규정별 **자연 발생 V1/V2 파일 쌍이 증명되지 않았다**. 따라서 V01-V05를
실제 개정본이라고 부르지 않는다. 각 case는 투명하게 만든 synthetic supersession fixture다.
generator와 test가 V1 hash/page, V1 byte-prefix 보존, 두 번 생성한 V2의 byte 일치, 추가 페이지의
text extraction 및 render 생성을 확인했으므로 plan/query는 각각 `READY` /
`VERIFIED_VERSION_PAIR`다. 이 표시는 **machine-ready**만 뜻하며 사람의 worksheet 승인이나 frozen
benchmark eligibility를 뜻하지 않는다. dataset은 계속 `DRAFT`, `human_verification.status`는 계속
`PENDING`이다.

공통 생성 계약:

1. V1은 아래 public PDF 바이트를 그대로 사용한다. 재저장하거나 metadata를 바꾸지 않는다.
2. ReportLab의 invariant 출력으로 정확한 marker와 `v2_only_text`만 포함한 A4 1쪽을 생성한다.
3. 고정된 generator source SHA와 package versions를 기록하고 pypdf로 그 한 쪽을 V1 뒤에 append한다.
4. 동일 입력으로 두 번 생성하여 V2 SHA-256이 동일한지 확인한다. 다르면 deterministic fixture가 아니므로 중단한다.
5. 추가 페이지를 렌더링하고 V2 SHA를 dataset에 기록했다. 사람은 아래 PDF와 PNG를 직접 열어
   marker/text/page 및 원본 V1 페이지 보존을 별도로 승인한다.
6. 같은 logical document에 V1을 먼저 등록하고 V2를 나중에 활성화한다. V1은 forbidden target이다.
7. V2 hash/page와 human approval이 없으면 `freeze`가 계속 실패해야 한다.

| V1 확인 | V2 확인 | ID | V1 public 파일 / anchor page | V2 marker / append page | 현재 상태 |
|---|---|---|---|---|---|
| [ ] | [ ] | V01 | `개방형직위운영내규_20260703.pdf` / 1 | `SYNCBASE-R1-V01` / 11 | `READY / HUMAN REVIEW PENDING` |
| [ ] | [ ] | V02 | `계약직원운영관리내규_20260703.pdf` / 2 | `SYNCBASE-R1-V02` / 36 | `READY / HUMAN REVIEW PENDING` |
| [ ] | [ ] | V03 | `규정관리규정_20260703.pdf` / 2 | `SYNCBASE-R1-V03` / 17 | `READY / HUMAN REVIEW PENDING` |
| [ ] | [ ] | V04 | `위원회_관리_규정_20260703.pdf` / 3 | `SYNCBASE-R1-V04` / 9 | `READY / HUMAN REVIEW PENDING` |
| [ ] | [ ] | V05 | `직원_학위취득_지원관리내규_20260703.pdf` / 2 | `SYNCBASE-R1-V05` / 8 | `READY / HUMAN REVIEW PENDING` |

### 생성된 V2 fixture와 machine manifest

모든 경로는 contest workspace root 기준이다. `manifest.json`의 `claim_eligible=false`와
`status=MACHINE_READY_HUMAN_REVIEW_PENDING`은 사람 승인 전까지 유지한다.

| ID | Synthetic V2 PDF | Final-page render | V2 SHA-256 |
|---|---|---|---|
| V01 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V01-synthetic-supersession-v2.pdf` | `syncbase-infra/evaluation/fixtures/version-sensitive/renders/V01-final-page.png` | `5534d5914cb397c6ad9794c4dd24f73d366a899186917aaf5717cb53a03c4694` |
| V02 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V02-synthetic-supersession-v2.pdf` | `syncbase-infra/evaluation/fixtures/version-sensitive/renders/V02-final-page.png` | `66fe7b29b77d3bcf4e62ebb2d47ecf67386c7cf66783a082b9d6e23e42a3cafa` |
| V03 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V03-synthetic-supersession-v2.pdf` | `syncbase-infra/evaluation/fixtures/version-sensitive/renders/V03-final-page.png` | `3391d46ff5693606d98f5c3bd9648c03c842a2404613289b4464317a5bd2618f` |
| V04 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V04-synthetic-supersession-v2.pdf` | `syncbase-infra/evaluation/fixtures/version-sensitive/renders/V04-final-page.png` | `c37c73ea863d4ff2d3313f730a06376b674b9cbea678e59804474f4ad4109358` |
| V05 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V05-synthetic-supersession-v2.pdf` | `syncbase-infra/evaluation/fixtures/version-sensitive/renders/V05-final-page.png` | `99f44bef484474f6bae687e33acb6e65fc36ec2f5523e21bc2f594c6e9b7afd4` |

Machine manifest:
`syncbase-infra/evaluation/fixtures/version-sensitive/manifest.json`

사람 검토자는 다음을 **모두** 직접 확인해야 한다.

1. 다섯 V1 원본 PDF의 제목, hash, page count 및 anchor page가 표와 일치한다.
2. 다섯 V2 PDF의 처음 N쪽이 V1과 같은 문서 내용이고 마지막 N+1쪽만 synthetic marker page다.
3. 다섯 PNG에서 marker와 Korean sentence가 눈으로 읽히며 잘림, 대체문자, 빈 glyph가 없다.
4. V2 marker가 V1 원문에 없고, V1은 version-sensitive query의 forbidden target으로 유지된다.
5. 위 V1/V2 checkbox를 사람이 채우고 reviewer/UTC 시각을 기록한 뒤에만 global approval을 한다.

### 완료된 machine verification (사람 승인 아님)

- Generator source SHA-256: `49e8e3240af66680a9cf3f7ad58de11d3315d1303369327d499f4e1bce094fc1`
- ReportLab: `4.4.9`
- pypdf: `6.10.0`
- Embedded Nanum Gothic SHA-256: `76f45ef4a6bcff344c837c95a7dcc26e017e38b5846d5ae0cdcb5b86be2e2d31`
- V1 byte-prefix preserved, extracted marker/text present, final page rendered: `5/5`
- Two generation runs byte-identical: `5/5`

## Calibration worksheet 완료 전 마지막 기록

- 위 machine manifest와 실제 fixture hash 재검증: [ ]
- 다섯 V2 PDF와 final-page PNG 사람 검토: [ ]
- 모든 case worksheet 확인 완료: [ ]
- no-answer corpus-wide 검토 완료: [ ]
- RC bindings 기록 완료: [ ]
- 이 calibration dataset을 prospective holdout/PASS 근거로 freeze하지 않음: [ ]

# Prospective holdout ground-truth verification worksheet

> **Dataset:** `syncbase-round1-holdout-v1`
> **Role:** `PROSPECTIVE_HOLDOUT`
> **Status:** `DRAFT` / `NOT_RUN`
> **Runtime exposure:** `NOT_QUERIED`
> **Human verification:** `PENDING`
> **Release bindings:** all `null`

This worksheet is deliberately incomplete. Machine validation proves file hashes,
page ranges, extracted excerpts, category counts, and separation from the exposed
calibration set; it does not prove that the labels are semantically correct or that
no-answer cases are absent from the whole corpus. A named human reviewer must open
the original PDFs, complete every check below, sign the review, bind the release
candidate, and freeze the dataset **before any holdout query is sent to a runtime**.

## Integrity boundary

- Exposed calibration dataset: `syncbase-round1-calibration-v1`
- Calibration file SHA-256:
  `fa3ecd87382537a28115f04369ab75b1e8f2b32b63ceae0da6b62cba167a3564`
- Ordered normalized calibration query-text-set SHA-256:
  `e71231e20fbcfc954a514ee6d1400f8d512008965b7e9c09483123ac731bd1ac`
- Normalization: Unicode NFKC, case-fold, collapse whitespace
- Required normalized query-text overlap: `0`
- Required factual/identifier evidence fingerprint overlap: `0`
- Locked category counts: factual `10`, exact identifier `5`, version-sensitive
  `5`, no-answer `10`
- Locked thresholds, version-fixture protocol, and citation provenance contract:
  identical to the accepted Round-1 contract

Run the static integrity checker without calling the retrieval service:

```sh
python3 syncbase-infra/evaluation/validate_holdout_integrity.py \
  syncbase-infra/evaluation/queries.round1.draft.json \
  syncbase-infra/evaluation/queries.round1.holdout.draft.json
```

Expected DRAFT-only labels are `HOLDOUT_DRAFT_INTEGRITY_VALID`,
`runtime_exposure_status=NOT_QUERIED`, `benchmark_result=NOT_RUN`, and
`claim_eligible=false`. They are not a benchmark PASS.

## Factual and paraphrase cases

For each row, open the named active source PDF, verify the full SHA-256 against the
dataset, inspect the listed 1-based page, confirm the excerpt and the question's
single intended interpretation, and confirm that the expected target uses the
active document version. V2 files must retain the cited original V1 page unchanged.

| Done | ID | New query | Active source | Version/page | Supporting excerpt |
|---|---|---|---|---:|---|
| [ ] | HF01 | 개방형직위 선발시험은 응시자가 볼 수 있도록 최소 며칠 동안 공고해야 하는가? | `V01-synthetic-supersession-v2.pdf` | 2 / 2 | `10일 이상 공고하여야 한다.` |
| [ ] | HF02 | 전문·일반·촉탁 계약직원의 계약기간은 원칙적으로 얼마 이내인가? | `V02-synthetic-supersession-v2.pdf` | 2 / 3 | `전문계약직원, 일반계약직원 또는 촉탁계약직원의 계약기간은 1년 이내로 한다.` |
| [ ] | HF03 | 공개 대상 규정은 어느 온라인 장소에 게시하는가? | `V03-synthetic-supersession-v2.pdf` | 2 / 5 | `규정은 공단 홈페이지에 게시하여 공개한다.` |
| [ ] | HF04 | 노동이사후보 투표는 보통 몇 시에 시작해 몇 시에 마감하는가? | `노동이사후보_선거관리_내규_20260703.pdf` | 1 / 4 | `투표는 해당일 오전 8시부터 시작하여 오후 6시에 마감` |
| [ ] | HF05 | 출산·질병·재해 등 비상 사유로 지급일 전에 받을 수 있는 보수의 상한은 무엇인가? | `보수규정_20260703.pdf` | 1 / 3 | `월 보수액의 한도내에서 지급할 수 있다.` |
| [ ] | HF06 | 회원권을 비수기에 이용하려면 체크인일 기준 어느 기간 안에 신청해야 하는가? | `복지후생규정_20260703.pdf` | 1 / 2 | `비수기에는 체크인 일자 기준 최소 3일전~최대 60일 전 기간 내 신청해야 하고` |
| [ ] | HF07 | 사무를 위임받은 사람에게는 어떤 권한과 책임이 함께 생기는가? | `사무위임전결규정_20260703.pdf` | 1 / 1 | `사무위임을 받은 자는 그 위임받은 업무의 처리에 필요한 권한을 가지며 동시에 그에 대한 책임을 진다.` |
| [ ] | HF08 | 시설관리직 신규 채용 시 둘 수 있는 수습기간은 어느 정도인가? | `시설관리직직원관리규정_20260703.pdf` | 1 / 1 | `3개월 미만의 수습기간을 둘 수 있으며` |
| [ ] | HF09 | 위원회 구성에서 외부위원 비율은 어느 수준이 되도록 노력해야 하는가? | `V04-synthetic-supersession-v2.pdf` | 2 / 2 | `전체위원의 2분의 1이상 임명 또는 위촉되도록 노력하여야 한다.` |
| [ ] | HF10 | 직원 학위취득 학자금은 한 사람에게 한 학기당 최대 얼마까지 지원되는가? | `V05-synthetic-supersession-v2.pdf` | 2 / 1 | `학위취득 지원 학자금은 1인 1학기당 최대 100만원 한도 내` |

## Exact-identifier cases

For each row, verify the printed form title/identifier on the original page. Confirm
that whitespace normalization in the extracted excerpt does not change the visible
identifier and that no other page is a better ground-truth target.

| Done | ID | New query | Active source | Version/page | Supporting excerpt |
|---|---|---|---|---:|---|
| [ ] | HI01 | `<별지 제3호 서식> 근무성적평정 결과 이의신청서` | `V01-synthetic-supersession-v2.pdf` | 2 / 10 | `근무성적평정 결과 이의신청서` |
| [ ] | HI02 | `<별지 제3호 서식> 인사기록카드` | `V02-synthetic-supersession-v2.pdf` | 2 / 24 | `인사기록카드` |
| [ ] | HI03 | `<별지 제2호 서식> 규정관리대장` | `V03-synthetic-supersession-v2.pdf` | 2 / 16 | `규 정 관 리 대 장` |
| [ ] | HI04 | `【별지 제4호】 노동이사후보 선거결과 공고` | `노동이사후보_선거관리_내규_20260703.pdf` | 1 / 10 | `노동이사후보 선거결과 공고` |
| [ ] | HI05 | `<별지 제4호 서식> 위임장` | `V04-synthetic-supersession-v2.pdf` | 2 / 8 | `위 임 장` |

## Version-sensitive cases

For every row, inspect both PDFs and the rendered final page. Confirm that V2 starts
with byte-for-byte identical V1 bytes, has exactly one appended A4 page, visibly
contains the marker and Korean sentence, and is the active version. Confirm the V1
hash is forbidden and cannot appear in any search result. Machine `READY` and
`VERIFIED_VERSION_PAIR` do not substitute for these checks.

| Done | ID | New query | V2 page / SHA-256 | Forbidden V1 SHA-256 |
|---|---|---|---|---|
| [ ] | V01 | 문서 표식 `SYNCBASE-R1-V01`이 나타내는 현재 활성 상태를 확인해 줘. | 11 / `5534d5914cb397c6ad9794c4dd24f73d366a899186917aaf5717cb53a03c4694` | `13ff66350199550d8e384252d6ed5665ddd58e09b3ce3b8ba996a2fd61940d71` |
| [ ] | V02 | `SYNCBASE-R1-V02` 표식이 가리키는 최신 적용 상태를 근거 페이지에서 찾아줘. | 36 / `66fe7b29b77d3bcf4e62ebb2d47ecf67386c7cf66783a082b9d6e23e42a3cafa` | `4664832b612072d32fc746de914bcecc7a22397687e273af186911e0f286bbb1` |
| [ ] | V03 | 현재 적용 대상으로 확인해야 할 `SYNCBASE-R1-V03`의 상태는 무엇인가? | 17 / `3391d46ff5693606d98f5c3bd9648c03c842a2404613289b4464317a5bd2618f` | `d10b7f0392b95de3b65c4da74a06354e4d0b6214bb773c1d86c6fed5c1498a7b` |
| [ ] | V04 | `SYNCBASE-R1-V04` 문서가 활성 적용 상태인지 출처와 함께 확인해 줘. | 9 / `c37c73ea863d4ff2d3313f730a06376b674b9cbea678e59804474f4ad4109358` | `82b8206478750a0c6b3cb337da395a52a9918514617c243b58648feeda15ab9d` |
| [ ] | V05 | `SYNCBASE-R1-V05`에 기록된 V2 전용 적용 여부를 찾아 알려줘. | 8 / `99f44bef484474f6bae687e33acb6e65fc36ec2f5523e21bc2f594c6e9b7afd4` | `4cd1a62fb0802c7802d05b45ef319d735ce1712f9420f8d2ddfb9be3b6829dff` |

## No-answer cases

Each case requires a human corpus-wide review, including tables, appendices, OCR or
extracted text, and near-synonyms. Check `no answer` only when the current frozen
corpus contains no sufficient evidence. A merely low similarity score is not proof
of absence. Personal-data and live/external-information questions must also remain
unsupported by this fixed document corpus.

| No answer confirmed | ID | New query | Review note |
|---|---|---|---|
| [ ] | HN01 | 재택근무용 장비 구입비의 월별 지원 한도는 얼마인가? | Search remote-work, equipment, purchase, reimbursement synonyms across all sources. |
| [ ] | HN02 | 직원이 반려동물을 데리고 출근하려면 어떤 승인을 받아야 하는가? | Search pet/animal/access/approval terms across all sources. |
| [ ] | HN03 | 직원 전기차 충전비의 월 지원 상한을 알려 줘. | Search EV/charging/transport-benefit terms across all sources. |
| [ ] | HN04 | 사내 어린이집의 현재 대기 순번은 몇 번인가? | Confirm no live waiting-list data is present. |
| [ ] | HN05 | `<별지 제99호 서식> 생성형 AI 사용승인서`의 내용을 보여 줘. | Confirm the form number/title is absent, including appendices. |
| [ ] | HN06 | 양자암호 통신 교육의 연간 의무 이수시간은 얼마인가? | Search training/security/required-hours synonyms across all sources. |
| [ ] | HN07 | 이 규정들의 2027년 12월 31일 개정 예정 내용을 알려 줘. | Confirm the future revision date/content is absent. |
| [ ] | HN08 | 특정 직원의 개인 여권번호는 무엇인가? | Confirm no passport number is present; do not introduce personal data. |
| [ ] | HN09 | 현재 북서태평양 태풍의 실시간 위치를 알려 줘. | Confirm corpus has no real-time weather feed or current position. |
| [ ] | HN10 | 재고 코드 `QZ-8841` 품목의 최신 구매 단가는 얼마인가? | Search exact code and procurement/unit-price terms across all sources. |

## Final human and freeze gate

- [ ] All 15 factual/identifier pages were opened in the source PDF and approved.
- [ ] The wording of each expected answer is supported without unstated inference.
- [ ] All five V1/V2 pairs and final-page renders were visually inspected.
- [ ] Each V2 is active, each V1 is superseded, and all active source/version IDs
      were captured from the clean benchmark corpus.
- [ ] All ten no-answer cases received a corpus-wide review by a named person.
- [ ] Static integrity validation reports zero normalized query/evidence overlap.
- [ ] The reviewer confirms none of the 30 holdout queries was sent before freeze
      to the system-under-test REST, MCP, embedding/debug, or retrieval runtime,
      and none was used for threshold selection or tuning.
- [ ] The reviewer acknowledges that query drafting received LLM/agent assistance;
      a named human independently verified every answerable page, version pair,
      and corpus-wide no-answer judgment.
- [ ] Corpus, model, tokenizer, profile, database, source-release, and five repository
      RC hashes were recorded only after the release candidate was fixed.
- [ ] The dataset was frozen before the first runtime query.

Reviewer: `PENDING`
Reviewed at (UTC): `PENDING`
Frozen dataset SHA-256: `PENDING`
Frozen corpus identity: `PENDING`

If any checkbox remains incomplete, keep `human_verification.status=PENDING`, keep
all release bindings `null`, do not freeze, and do not query the holdout.

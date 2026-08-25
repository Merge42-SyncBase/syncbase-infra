# Round-1 holdout no-answer static corpus precheck

> **Status: `MACHINE_PRECHECK_ONLY_HUMAN_APPROVAL_PENDING`**
> This is supporting evidence for human review. It does not approve the holdout,
> prove runtime corpus completeness, or constitute a retrieval benchmark run.

## Result

The static review found **zero direct literal-support candidate pages** for
HN01-HN10 in the intended fixed 10-PDF corpus. Broader Korean/English keyword
and synonym searches produced 11 page-level review candidates. Each candidate
was inspected in the extracted page text and was unrelated to the requested
fact. This machine precheck therefore indicates **no current no-answer label
change**, but every label remains human-approval pending.

| Coverage item | Result |
|---|---:|
| Intended corpus PDFs | 10 |
| Pages extracted by each parser | 120 |
| Expected PDF hashes matched | 10/10 |
| Pages with empty `pypdf` extraction | 0 |
| Pages with empty `pdfplumber` extraction | 0 |
| `pypdf` extracted characters | 98,783 |
| `pdfplumber` extracted characters | 106,279 |
| Direct-support candidate pages | 0 |
| Broad review candidate pages | 11 |

Dataset audited:
`syncbase-infra/evaluation/queries.round1.holdout.draft.json`, SHA-256
`bd254c49226931626c0fe32cb49495ae4353b3377c84fd3a4213c06e325178bd`,
dataset ID `syncbase-round1-holdout-v1`, role `PROSPECTIVE_HOLDOUT`, status
`DRAFT`, human verification `PENDING`.

## Corpus boundary and fail-closed condition

The draft does not yet contain a separately frozen, runtime-attested corpus
manifest. For this precheck, `INTENDED_FIXED_CORPUS_FROM_DRAFT` means the union
of every `candidate_evidence.source_file` attached to the 20 supported holdout
queries, reconciled against all five `version_fixture_plans`. The five active V2
fixtures replace their corresponding base V1 PDFs; the other five sources are
unchanged V1 PDFs.

This is a statement about the **intended benchmark corpus**, not proof of what a
running database contains. Before freeze, the observation collector's corpus
manifest preflight must match the exact 10-file path/SHA-256 set below. Any
extra, missing, or substituted active source invalidates this precheck. Human
review must then expand to the changed corpus, and the freeze gate must remain
failed until that review is complete.

## PDF inventory

All paths are workspace-root relative. `V2` means the deterministic active
supersession fixture specified by the draft; `V1` means an unchanged source.

| Kind | File | Pages | SHA-256 | Holdout evidence IDs |
|---|---|---:|---|---|
| V1 | `documents/sample-pdfs/서울특별시-현행규정/노동이사후보_선거관리_내규_20260703.pdf` | 10 | `2d7d49292f7831663822dbd75f5f64d4007211f434f01858f52800037d6f3b54` | HF04, HI04 |
| V1 | `documents/sample-pdfs/서울특별시-현행규정/보수규정_20260703.pdf` | 13 | `325bf6c654dd9441843e87764d7a49b7bbe19a6564b7b24b66c455a3d2b6380b` | HF05 |
| V1 | `documents/sample-pdfs/서울특별시-현행규정/복지후생규정_20260703.pdf` | 6 | `03e4525d3ddbbba3d6619e0997d7fc6047092826c17fc6ed1606c6f9f90b5550` | HF06 |
| V1 | `documents/sample-pdfs/서울특별시-현행규정/사무위임전결규정_20260703.pdf` | 5 | `91a5a4f0ea97d589e1e5868aa10f2e365933b7d8fb5022b713bdb105e1932e6b` | HF07 |
| V1 | `documents/sample-pdfs/서울특별시-현행규정/시설관리직직원관리규정_20260703.pdf` | 5 | `47be4aec93ed9d515a0fa55260d1b9b88307a3feee0f19e197138663405dab64` | HF08 |
| V2 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V01-synthetic-supersession-v2.pdf` | 11 | `5534d5914cb397c6ad9794c4dd24f73d366a899186917aaf5717cb53a03c4694` | HF01, HI01, V01 |
| V2 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V02-synthetic-supersession-v2.pdf` | 36 | `66fe7b29b77d3bcf4e62ebb2d47ecf67386c7cf66783a082b9d6e23e42a3cafa` | HF02, HI02, V02 |
| V2 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V03-synthetic-supersession-v2.pdf` | 17 | `3391d46ff5693606d98f5c3bd9648c03c842a2404613289b4464317a5bd2618f` | HF03, HI03, V03 |
| V2 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V04-synthetic-supersession-v2.pdf` | 9 | `c37c73ea863d4ff2d3313f730a06376b674b9cbea678e59804474f4ad4109358` | HF09, HI05, V04 |
| V2 | `syncbase-infra/evaluation/fixtures/version-sensitive/pdfs/V05-synthetic-supersession-v2.pdf` | 8 | `99f44bef484474f6bae687e33acb6e65fc36ec2f5523e21bc2f594c6e9b7afd4` | HF10, V05 |

## Method

- Read and hash-verified every intended PDF byte-for-byte.
- Extracted every page independently with bundled `pypdf 6.10.0` and
  `pdfplumber 0.11.9`; the page counts agreed and neither extractor returned an
  empty page.
- Applied NFKC normalization, Unicode case-folding, and whitespace collapsing.
  A secondary whitespace-compacted literal match is allowed only for normalized
  terms of at least four characters to catch PDF glyph-spacing artifacts without
  turning short cross-word accidents into matches.
- Searched explicit direct phrases first, then broader Korean and English
  keywords/synonyms. A lexical hit is only a candidate for manual inspection; a
  miss is not treated as semantic proof.
- Did **not** call REST, MCP, the database, the embedding service, an LLM, a
  retrieval evaluator, or any network endpoint. No holdout query was exposed to
  the retrieval runtime.

The exact executable contract is
`evaluation/static_no_answer_precheck.py`. It fails closed if the HN query text,
the no-answer expected-hit contract, the V1/V2 bindings, a source hash, the
10-file derivation, page counts between parsers, or extractable-page coverage
changes.

## Search terms and candidate counts

The original Korean query text is also locked verbatim in the checker. Terms
below are the additional direct and review anchors. English terms are included
even though the regulations are primarily Korean.

| ID | Direct phrases (representative complete set) | Broad Korean/English review terms | Direct pages | Review pages |
|---|---|---|---:|---:|
| HN01 | `재택근무용 장비 구입비`, `재택근무 장비 구입비`, `원격근무 장비 구입비`, `월별 재택근무 장비 지원 한도`, `telework equipment purchase allowance`, `remote work equipment allowance`, `work from home equipment allowance` | `재택근무`, `원격근무`, `원격`, `장비`, `기기 구입`, `기기 구매`, `telework`, `remote work`, `work from home`, `equipment` | 0 | 1 |
| HN02 | `반려동물을 데리고 출근`, `반려동물 출근 승인`, `애완동물 출근 승인`, `pet-at-work approval`, `bring a pet to work`, `pets at work approval`, `companion animal workplace approval` | `반려동물`, `반려`, `애완동물`, `애완`, `동물`, `동반`, `pet`, `animal` | 0 | 2 |
| HN03 | `직원 전기차 충전비`, `전기차 충전비 월 지원 상한`, `전기자동차 충전비`, `electric vehicle charging allowance`, `EV charging monthly cap` | `전기차`, `전기자동차`, `충전비`, `충전 요금`, `충전`, `electric vehicle`, `EV charging`, `charging cost` | 0 | 0 |
| HN04 | `사내 어린이집 현재 대기 순번`, `직장 어린이집 대기 순번`, `daycare current waitlist position`, `childcare waitlist number` | `어린이집`, `보육시설`, `보육`, `대기 순번`, `대기번호`, `대기자`, `daycare`, `childcare`, `nursery`, `waitlist` | 0 | 0 |
| HN05 | `별지 제99호 서식 생성형 AI 사용승인서`, `제99호 서식 생성형 인공지능 사용승인서`, `generative AI use approval form no. 99` | `별지 제99호`, `제99호`, `99호 서식`, `생성형`, `인공지능`, `AI 사용`, `generative AI`, `artificial intelligence` | 0 | 0 |
| HN06 | `양자암호 통신 교육 연간 의무 이수시간`, `양자 암호 통신 교육 의무시간`, `annual mandatory quantum cryptography training hours` | `양자암호`, `양자 암호`, `양자통신`, `양자`, `암호`, `quantum`, `cryptography`, `encryption` | 0 | 0 |
| HN07 | `2027년 12월 31일 개정 예정`, `2027.12.31 개정 예정`, `2027-12-31 planned amendment`, `future amendment on 2027-12-31` | `2027`, `12월 31일`, `개정 예정`, `예정 개정`, `planned amendment`, `future amendment` | 0 | 4 |
| HN08 | `특정 직원의 개인 여권번호`, `직원 여권 번호`, `employee passport number` | `여권번호`, `여권 번호`, `여권`, `passport number`, `passport no.`, `passport` | 0 | 0 |
| HN09 | `현재 북서태평양 태풍의 실시간 위치`, `북서태평양 태풍 실시간 위치`, `real-time location of northwest Pacific typhoon` | `북서태평양`, `태풍`, `열대저기압`, `실시간`, `northwest pacific`, `typhoon`, `tropical cyclone`, `real-time` | 0 | 0 |
| HN10 | `재고 코드 QZ-8841 품목의 최신 구매 단가`, `QZ-8841 구매 단가`, `latest purchase unit price for inventory code QZ-8841` | `QZ-8841`, `QZ 8841`, `QZ8841`, `재고 코드`, `품목 코드`, `구매 단가`, `매입 단가`, `재고`, `단가`, `inventory code`, `purchase unit price` | 0 | 4 |

## Manual disposition of every broad candidate page

The page numbers below are PDF page indices, starting at one. Both parser
outputs were inspected for each candidate.

| Case | File/page and matched root | Disposition |
|---|---|---|
| HN01 | `V04-synthetic-supersession-v2.pdf`, p.3 — `원격` | Describes committee attendance by real-time audio/video remote communications. It contains no telework equipment purchase, monthly allowance, or support cap. **Unrelated.** |
| HN02 | `보수규정_20260703.pdf`, p.2 — `동반` | Describes unpaid leave when accompanying a spouse during overseas work, study, or training. It is not an animal-at-work rule or approval. **Unrelated.** |
| HN02 | `V02-synthetic-supersession-v2.pdf`, p.12 — `동물` | Lists animal-related expertise among possible specialist contract work domains. It says nothing about bringing a pet to work or approval. **Unrelated.** |
| HN07 | `보수규정_20260703.pdf`, p.6 — `12월 31일` | A transitional compensation provision ends on **1995-12-31**. No 2027 date or planned amendment appears. **Historical and unrelated.** |
| HN07 | `복지후생규정_20260703.pdf`, p.5 — `12월 31일` | Uses 2000-12-31 and an employee's age-year December 31 in a retirement-allowance transition. No 2027 date or planned amendment appears. **Historical/recurring cutoff and unrelated.** |
| HN07 | `시설관리직직원관리규정_20260703.pdf`, p.1 — `12월 31일` | Uses December 31 of the hiring year to calculate minimum hiring age. No 2027 date or planned amendment appears. **Recurring cutoff and unrelated.** |
| HN07 | `V02-synthetic-supersession-v2.pdf`, p.10 — `12월 31일` | States that an amendment applies from **2014-12-31**. No 2027 date or future amendment content appears. **Historical and unrelated.** |
| HN10 | `시설관리직직원관리규정_20260703.pdf`, p.2 — `재고` | The match is the prefix of `재고용` (re-employment), not inventory. No stock code, QZ-8841, item, or purchase price appears. **Lexical collision.** |
| HN10 | `V02-synthetic-supersession-v2.pdf`, p.1 — `재고` | `재고용` describes rehiring a contract worker until age 65. It is not inventory. **Lexical collision.** |
| HN10 | `V02-synthetic-supersession-v2.pdf`, p.3 — `재고` | `재고용·재계약` concerns a worker's re-employment/re-contracting. It is not inventory. **Lexical collision.** |
| HN10 | `V02-synthetic-supersession-v2.pdf`, p.6 — `단가` | `시중노임단가` means a prevailing labor/wage rate used to set contract-worker compensation. It is not a purchase unit price and has no QZ-8841 identifier. **Unrelated.** |

HN03, HN04, HN05, HN06, HN08, and HN09 produced no broad candidate page
under their listed Korean/English anchors. That absence is useful screening
evidence, not a substitute for human reading.

## Limitations and required human decision

1. Literal and synonym absence cannot prove semantic absence. A regulation can
   express a concept using an unlisted paraphrase.
2. Both extractors returned text for every page, but text extraction can still
   miss image-only regions, malformed glyph encodings, diagrams, or
   OCR-dependent content. The human reviewer must inspect the source PDFs, not
   only these extracted strings.
3. This precheck derives an intended corpus from the draft. It cannot prove that
   the release database has exactly that corpus; the exact manifest preflight is
   mandatory.
4. The checker deliberately did not estimate similarity or use a model. A low
   similarity score would not establish absence and is not part of this result.
5. HN08 and HN09 also request private/live external information that a fixed
   regulation corpus should not answer, but that design judgment does not replace
   corpus verification.

Human approval may be recorded only after a reviewer confirms all ten source
PDFs, the candidate dispositions above, and the exact frozen corpus manifest.
Until then the authoritative status remains:

`MACHINE_PRECHECK_ONLY_HUMAN_APPROVAL_PENDING`

## Reproduction

From `syncbase-infra/evaluation`, using the bundled workspace Python:

```bash
/Users/eddie/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  static_no_answer_precheck.py

/Users/eddie/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m unittest -v test_static_no_answer_precheck.py
```

The checker emits deterministic JSON to standard output and does not write an
evidence artifact or contact a runtime. The six tests cover normalization,
short cross-word false-positive rejection, corpus derivation, dual-parser PDF
coverage, zero direct-support hits, and preservation of all broad review
candidates.

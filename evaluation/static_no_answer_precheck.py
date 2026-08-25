#!/usr/bin/env python3
"""Static, non-runtime precheck for the Round-1 holdout no-answer cases.

This program deliberately does not call SyncBase, an embedding model, an LLM, or
any network endpoint.  It derives the *intended* fixed corpus from the holdout's
supported-query evidence and version-fixture plans, verifies the PDF hashes,
extracts every page with two independent bundled libraries, and performs only
literal Korean/English term searches.

The result is machine support for a human corpus review.  It is not proof that
the runtime contains exactly these sources and it never approves the holdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pdfplumber
import pypdf
from pypdf import PdfReader


STATUS = "MACHINE_PRECHECK_ONLY_HUMAN_APPROVAL_PENDING"
CORPUS_BASIS = "INTENDED_FIXED_CORPUS_FROM_DRAFT"


# These profiles are intentionally explicit.  A changed holdout question must
# fail closed until a human updates and re-reviews its lexical audit contract.
CASE_PROFILES: dict[str, dict[str, Any]] = {
    "HN01": {
        "query": "재택근무용 장비 구입비의 월별 지원 한도는 얼마인가?",
        "direct_terms": [
            "재택근무용 장비 구입비",
            "재택근무 장비 구입비",
            "원격근무 장비 구입비",
            "월별 재택근무 장비 지원 한도",
            "telework equipment purchase allowance",
            "remote work equipment allowance",
            "work from home equipment allowance",
        ],
        "review_terms": [
            "재택근무",
            "원격근무",
            "원격",
            "장비",
            "기기 구입",
            "기기 구매",
            "telework",
            "remote work",
            "work from home",
            "equipment",
        ],
    },
    "HN02": {
        "query": (
            "직원이 반려동물을 데리고 출근하려면 어떤 승인을 받아야 하는가?"
        ),
        "direct_terms": [
            "반려동물을 데리고 출근",
            "반려동물 출근 승인",
            "애완동물 출근 승인",
            "pet-at-work approval",
            "bring a pet to work",
            "pets at work approval",
            "companion animal workplace approval",
        ],
        "review_terms": [
            "반려동물",
            "반려",
            "애완동물",
            "애완",
            "동물",
            "동반",
            "pet",
            "animal",
        ],
    },
    "HN03": {
        "query": "직원 전기차 충전비의 월 지원 상한을 알려 줘.",
        "direct_terms": [
            "직원 전기차 충전비",
            "전기차 충전비 월 지원 상한",
            "전기자동차 충전비",
            "electric vehicle charging allowance",
            "EV charging monthly cap",
        ],
        "review_terms": [
            "전기차",
            "전기자동차",
            "충전비",
            "충전 요금",
            "충전",
            "electric vehicle",
            "EV charging",
            "charging cost",
        ],
    },
    "HN04": {
        "query": "사내 어린이집의 현재 대기 순번은 몇 번인가?",
        "direct_terms": [
            "사내 어린이집 현재 대기 순번",
            "직장 어린이집 대기 순번",
            "daycare current waitlist position",
            "childcare waitlist number",
        ],
        "review_terms": [
            "어린이집",
            "보육시설",
            "보육",
            "대기 순번",
            "대기번호",
            "대기자",
            "daycare",
            "childcare",
            "nursery",
            "waitlist",
        ],
    },
    "HN05": {
        "query": "<별지 제99호 서식> 생성형 AI 사용승인서의 내용을 보여 줘.",
        "direct_terms": [
            "별지 제99호 서식 생성형 AI 사용승인서",
            "제99호 서식 생성형 인공지능 사용승인서",
            "generative AI use approval form no. 99",
        ],
        "review_terms": [
            "별지 제99호",
            "제99호",
            "99호 서식",
            "생성형",
            "인공지능",
            "AI 사용",
            "generative AI",
            "artificial intelligence",
        ],
    },
    "HN06": {
        "query": "양자암호 통신 교육의 연간 의무 이수시간은 얼마인가?",
        "direct_terms": [
            "양자암호 통신 교육 연간 의무 이수시간",
            "양자 암호 통신 교육 의무시간",
            "annual mandatory quantum cryptography training hours",
        ],
        "review_terms": [
            "양자암호",
            "양자 암호",
            "양자통신",
            "양자",
            "암호",
            "quantum",
            "cryptography",
            "encryption",
        ],
    },
    "HN07": {
        "query": "이 규정들의 2027년 12월 31일 개정 예정 내용을 알려 줘.",
        "direct_terms": [
            "2027년 12월 31일 개정 예정",
            "2027.12.31 개정 예정",
            "2027-12-31 planned amendment",
            "future amendment on 2027-12-31",
        ],
        "review_terms": [
            "2027",
            "12월 31일",
            "개정 예정",
            "예정 개정",
            "planned amendment",
            "future amendment",
        ],
    },
    "HN08": {
        "query": "특정 직원의 개인 여권번호는 무엇인가?",
        "direct_terms": [
            "특정 직원의 개인 여권번호",
            "직원 여권 번호",
            "employee passport number",
        ],
        "review_terms": [
            "여권번호",
            "여권 번호",
            "여권",
            "passport number",
            "passport no.",
            "passport",
        ],
    },
    "HN09": {
        "query": "현재 북서태평양 태풍의 실시간 위치를 알려 줘.",
        "direct_terms": [
            "현재 북서태평양 태풍의 실시간 위치",
            "북서태평양 태풍 실시간 위치",
            "real-time location of northwest Pacific typhoon",
        ],
        "review_terms": [
            "북서태평양",
            "태풍",
            "열대저기압",
            "실시간",
            "northwest pacific",
            "typhoon",
            "tropical cyclone",
            "real-time",
        ],
    },
    "HN10": {
        "query": "재고 코드 QZ-8841 품목의 최신 구매 단가는 얼마인가?",
        "direct_terms": [
            "재고 코드 QZ-8841 품목의 최신 구매 단가",
            "QZ-8841 구매 단가",
            "latest purchase unit price for inventory code QZ-8841",
        ],
        "review_terms": [
            "QZ-8841",
            "QZ 8841",
            "QZ8841",
            "재고 코드",
            "품목 코드",
            "구매 단가",
            "매입 단가",
            "재고",
            "단가",
            "inventory code",
            "purchase unit price",
        ],
    },
}


class AuditError(RuntimeError):
    """Fail-closed static audit error."""


def normalize_text(value: str) -> str:
    """NFKC, case-fold, and collapse whitespace without semantic stemming."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def find_term(text: str, term: str) -> str | None:
    """Return the literal match mode, permitting conservative PDF-space repair.

    Compact matching is restricted to four or more normalized characters.  This
    avoids declaring a match for short cross-word accidents such as `직원 격려`
    -> `원격`.  All matches remain review candidates, never semantic proof.
    """

    normalized_text = normalize_text(text)
    normalized_term = normalize_text(term)
    if normalized_term in normalized_text:
        return "NFKC_CASEFOLD_SUBSTRING"

    compact_term = _compact(normalized_term)
    if len(compact_term) >= 4 and compact_term in _compact(normalized_text):
        return "NFKC_CASEFOLD_WHITESPACE_COMPACT_SUBSTRING"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_source(
    sources: dict[str, dict[str, Any]], path: str, sha256: str, evidence_id: str
) -> None:
    existing = sources.get(path)
    if existing and existing["expected_sha256"] != sha256:
        raise AuditError(f"conflicting SHA-256 bindings for {path}")
    if existing:
        existing["evidence_ids"].append(evidence_id)
        return
    sources[path] = {
        "path": path,
        "expected_sha256": sha256,
        "evidence_ids": [evidence_id],
        "source_kind": "UNCHANGED_V1",
    }


def derive_intended_corpus(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive the draft's intended active corpus without inspecting a runtime."""

    queries = dataset.get("queries")
    if not isinstance(queries, list):
        raise AuditError("dataset queries must be a list")

    sources: dict[str, dict[str, Any]] = {}
    no_answer_queries: dict[str, dict[str, Any]] = {}
    for query in queries:
        query_id = query.get("id")
        category = query.get("category")
        if category == "no_answer":
            no_answer_queries[str(query_id)] = query
            continue
        for evidence in query.get("candidate_evidence", []):
            _record_source(
                sources,
                evidence["source_file"],
                evidence["source_sha256"],
                str(query_id),
            )

    expected_ids = set(CASE_PROFILES)
    if set(no_answer_queries) != expected_ids:
        raise AuditError(
            "no-answer ID set changed; expected "
            f"{sorted(expected_ids)}, got {sorted(no_answer_queries)}"
        )
    for query_id, profile in CASE_PROFILES.items():
        query = no_answer_queries[query_id]
        if query.get("query") != profile["query"]:
            raise AuditError(f"{query_id} query text changed; human re-review required")
        expected = query.get("expected", {})
        if expected.get("no_answer") is not True:
            raise AuditError(f"{query_id} is not marked no_answer=true")
        if expected.get("relevant") or expected.get("forbidden"):
            raise AuditError(f"{query_id} no-answer expectations must have empty hit sets")

    plans = dataset.get("version_fixture_plans", [])
    if len(plans) != 5:
        raise AuditError(f"expected five version fixture plans, got {len(plans)}")
    for plan in plans:
        v2_path = plan["v2_source_file"]
        v2_sha = plan["v2_source_sha256"]
        source = sources.get(v2_path)
        if source is None or source["expected_sha256"] != v2_sha:
            raise AuditError(f"active V2 evidence is missing or mismatched for {plan['id']}")
        source["source_kind"] = "SYNTHETIC_ACTIVE_V2"
        source["fixture_plan_id"] = plan["id"]

        base_path = plan["base_source"]["source_file"]
        if base_path in sources:
            raise AuditError(f"superseded V1 unexpectedly remains in intended corpus: {base_path}")

        query_by_id = {str(q.get("id")): q for q in queries}
        version_query = query_by_id.get(str(plan["query_id"]), {})
        forbidden = version_query.get("expected", {}).get("forbidden", [])
        expected_v1 = plan["v1_source_sha256"]
        if not any(item.get("source_sha256") == expected_v1 for item in forbidden):
            raise AuditError(f"V1 forbidden binding is missing for {plan['query_id']}")

    if len(sources) != 10:
        raise AuditError(f"expected ten intended active PDFs, got {len(sources)}")
    if sum(x["source_kind"] == "SYNTHETIC_ACTIVE_V2" for x in sources.values()) != 5:
        raise AuditError("intended corpus must contain exactly five active V2 fixtures")

    for source in sources.values():
        source["evidence_ids"] = sorted(set(source["evidence_ids"]))
    return [sources[path] for path in sorted(sources)]


def _page_context(text: str, term: str, radius: int = 150) -> str | None:
    normalized_text = normalize_text(text)
    normalized_term = normalize_text(term)
    position = normalized_text.find(normalized_term)
    if position < 0:
        return None
    start = max(0, position - radius)
    end = min(len(normalized_text), position + len(normalized_term) + radius)
    return normalized_text[start:end]


def _search_pages(
    pages: list[dict[str, Any]], terms: list[str]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for page in pages:
        matches: list[dict[str, str]] = []
        for term in terms:
            for extractor, text in page["texts"].items():
                mode = find_term(text, term)
                if not mode:
                    continue
                match = {"term": term, "extractor": extractor, "mode": mode}
                context = _page_context(text, term)
                if context:
                    match["context"] = context
                matches.append(match)
        if matches:
            candidates.append(
                {
                    "path": page["path"],
                    "page": page["page"],
                    "matches": sorted(
                        matches,
                        key=lambda value: (
                            value["term"], value["extractor"], value["mode"]
                        ),
                    ),
                }
            )
    return candidates


def run_audit(dataset_path: Path, workspace_root: Path) -> dict[str, Any]:
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    corpus = derive_intended_corpus(dataset)

    inventory: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for source in corpus:
        path = workspace_root / source["path"]
        if not path.is_file():
            raise AuditError(f"source PDF is missing: {source['path']}")
        actual_sha = _sha256(path)
        if actual_sha != source["expected_sha256"]:
            raise AuditError(f"source PDF SHA-256 mismatch: {source['path']}")

        pypdf_reader = PdfReader(path)
        with pdfplumber.open(path) as pdfplumber_reader:
            if len(pypdf_reader.pages) != len(pdfplumber_reader.pages):
                raise AuditError(f"extractor page-count mismatch: {source['path']}")

            empty_pages: dict[str, list[int]] = {"pypdf": [], "pdfplumber": []}
            character_counts = {"pypdf": 0, "pdfplumber": 0}
            for page_number, (pypdf_page, pdfplumber_page) in enumerate(
                zip(pypdf_reader.pages, pdfplumber_reader.pages), start=1
            ):
                extracted = {
                    "pypdf": pypdf_page.extract_text() or "",
                    "pdfplumber": pdfplumber_page.extract_text() or "",
                }
                for extractor, text in extracted.items():
                    character_counts[extractor] += len(text)
                    if not text.strip():
                        empty_pages[extractor].append(page_number)
                pages.append(
                    {"path": source["path"], "page": page_number, "texts": extracted}
                )

        if any(empty_pages.values()):
            raise AuditError(
                f"one or more pages had no extractable text in {source['path']}: "
                f"{empty_pages}"
            )
        inventory.append(
            {
                **source,
                "actual_sha256": actual_sha,
                "sha256_matches": True,
                "pages": len(pypdf_reader.pages),
                "character_counts": character_counts,
                "empty_pages": empty_pages,
            }
        )

    cases: list[dict[str, Any]] = []
    for query_id, profile in CASE_PROFILES.items():
        direct_candidates = _search_pages(pages, profile["direct_terms"])
        review_candidates = _search_pages(pages, profile["review_terms"])
        cases.append(
            {
                "id": query_id,
                "query": profile["query"],
                "direct_terms": profile["direct_terms"],
                "review_terms": profile["review_terms"],
                "direct_candidates": direct_candidates,
                "review_candidates": review_candidates,
                "machine_disposition": (
                    "POSSIBLE_SUPPORT_REQUIRES_HUMAN_REVIEW"
                    if direct_candidates
                    else "NO_DIRECT_LITERAL_SUPPORT_FOUND_HUMAN_REVIEW_REQUIRED"
                ),
            }
        )

    return {
        "status": STATUS,
        "dataset": {
            "path": str(dataset_path.relative_to(workspace_root)),
            "sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "dataset_id": dataset.get("dataset_id"),
            "dataset_role": dataset.get("dataset_role"),
            "status": dataset.get("status"),
            "human_verification_status": dataset.get("human_verification", {}).get(
                "status"
            ),
        },
        "corpus_basis": CORPUS_BASIS,
        "runtime_corpus_completeness": "NOT_PROVEN",
        "runtime_manifest_freeze_precondition": (
            "The collector corpus-manifest preflight must match this exact ten-file "
            "path/SHA-256 set. Any extra, missing, or substituted active source "
            "invalidates this precheck and requires expanded human review before freeze."
        ),
        "tools": {
            "pypdf": pypdf.__version__,
            "pdfplumber": pdfplumber.__version__,
            "normalization": "NFKC_CASEFOLD_COLLAPSE_WHITESPACE_V1",
        },
        "coverage": {
            "files": len(inventory),
            "pages": len(pages),
            "all_expected_hashes_match": True,
            "all_pages_nonempty_in_both_extractors": True,
            "direct_candidate_pages": sum(
                len(case["direct_candidates"]) for case in cases
            ),
            "review_candidate_pages": sum(
                len(case["review_candidates"]) for case in cases
            ),
        },
        "inventory": inventory,
        "cases": cases,
        "limitations": [
            "Literal and synonym absence is not semantic proof that an answer is absent.",
            "Text extraction can miss image-only, malformed, visually encoded, "
            "or OCR-dependent content.",
            "This audit does not prove which sources are active in a runtime.",
            "No retrieval, embedding, LLM, REST, MCP, database, or network service was queried.",
            "A human must inspect the corpus and every review candidate before approval.",
        ],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=default_root)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_root
        / "syncbase-infra"
        / "evaluation"
        / "queries.round1.holdout.draft.json",
    )
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        result = run_audit(args.dataset.resolve(), args.workspace_root.resolve())
    except (AuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"static no-answer precheck failed: {exc}", file=sys.stderr)
        return 1
    json.dump(
        result,
        sys.stdout,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Tests for the static Round-1 holdout no-answer precheck."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

EVALUATION_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = EVALUATION_DIR.parents[1]
DATASET_PATH = EVALUATION_DIR / "queries.round1.holdout.draft.json"
MODULE_PATH = EVALUATION_DIR / "static_no_answer_precheck.py"


def load_module():
    spec = importlib.util.spec_from_file_location("static_no_answer_precheck", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRECHECK = load_module()


class StaticNoAnswerPrecheckUnitTests(unittest.TestCase):
    def test_normalized_literal_and_pdf_whitespace_matching(self) -> None:
        self.assertEqual(
            PRECHECK.find_term("원격 통신 수단", "원격"),
            "NFKC_CASEFOLD_SUBSTRING",
        )
        self.assertEqual(
            PRECHECK.find_term("재 택 근 무 장 비", "재택근무 장비"),
            "NFKC_CASEFOLD_WHITESPACE_COMPACT_SUBSTRING",
        )

    def test_short_compact_cross_word_accident_is_rejected(self) -> None:
        self.assertIsNone(PRECHECK.find_term("직원 격려", "원격"))

    def test_draft_derives_five_v2_and_five_unchanged_sources(self) -> None:
        dataset = json.loads(DATASET_PATH.read_text())
        corpus = PRECHECK.derive_intended_corpus(dataset)
        self.assertEqual(len(corpus), 10)
        self.assertEqual(
            sum(item["source_kind"] == "SYNTHETIC_ACTIVE_V2" for item in corpus), 5
        )
        self.assertEqual(
            sum(item["source_kind"] == "UNCHANGED_V1" for item in corpus), 5
        )
        self.assertEqual(
            set(PRECHECK.CASE_PROFILES), {f"HN{i:02d}" for i in range(1, 11)}
        )


class StaticNoAnswerPrecheckCorpusIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = PRECHECK.run_audit(DATASET_PATH, WORKSPACE_ROOT)

    def test_all_intended_pdf_bytes_and_pages_are_covered(self) -> None:
        self.assertEqual(self.result["status"], PRECHECK.STATUS)
        self.assertEqual(self.result["corpus_basis"], PRECHECK.CORPUS_BASIS)
        self.assertEqual(self.result["runtime_corpus_completeness"], "NOT_PROVEN")
        self.assertEqual(self.result["coverage"]["files"], 10)
        self.assertEqual(self.result["coverage"]["pages"], 120)
        self.assertTrue(self.result["coverage"]["all_expected_hashes_match"])
        self.assertTrue(
            self.result["coverage"]["all_pages_nonempty_in_both_extractors"]
        )

    def test_no_direct_literal_support_is_found(self) -> None:
        self.assertEqual(self.result["coverage"]["direct_candidate_pages"], 0)
        self.assertTrue(
            all(not case["direct_candidates"] for case in self.result["cases"])
        )

    def test_broad_review_candidate_counts_stay_visible(self) -> None:
        counts = {
            case["id"]: len(case["review_candidates"])
            for case in self.result["cases"]
        }
        self.assertEqual(
            counts,
            {
                "HN01": 1,
                "HN02": 2,
                "HN03": 0,
                "HN04": 0,
                "HN05": 0,
                "HN06": 0,
                "HN07": 4,
                "HN08": 0,
                "HN09": 0,
                "HN10": 4,
            },
        )


if __name__ == "__main__":
    unittest.main()

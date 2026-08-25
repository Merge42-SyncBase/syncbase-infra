from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pdfplumber
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4


MODULE_PATH = Path(__file__).with_name("generate_version_fixtures.py")
DRAFT_PATH = Path(__file__).with_name("queries.round1.draft.json")
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "generate_version_fixtures", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionFixtureGeneratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.dataset = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        self.renderer = shutil.which("pdftoppm")
        self.assertIsNotNone(self.renderer, "pdftoppm is required for fixture QA")

    def generate(self, root: Path, dataset: dict | None = None) -> dict:
        return self.module.generate_all(
            dataset=dataset or self.dataset,
            source_root=WORKSPACE_ROOT,
            output_dir=root / "fixtures",
            render_dir=root / "renders",
            temp_root=root / "tmp" / "pdfs",
            pdftoppm=Path(self.renderer),
        )

    def test_five_v2_fixtures_are_deterministic_incremental_and_rendered(self) -> None:
        original_hashes = {
            plan["id"]: hashlib.sha256(
                (WORKSPACE_ROOT / plan["base_source"]["source_file"]).read_bytes()
            ).hexdigest()
            for plan in self.dataset["version_fixture_plans"]
        }

        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            first = self.generate(first_root)
            second = self.generate(second_root)

            self.assertEqual(first["artifact_kind"], "DRAFT_VERSION_FIXTURE_MANIFEST")
            self.assertEqual(first["status"], "MACHINE_READY_HUMAN_REVIEW_PENDING")
            self.assertFalse(first["claim_eligible"])
            self.assertEqual(first["fixture_count"], 5)
            self.assertEqual(len(first["fixtures"]), 5)
            self.assertEqual(
                [item["v2_sha256"] for item in first["fixtures"]],
                [item["v2_sha256"] for item in second["fixtures"]],
            )

            plans = {plan["id"]: plan for plan in self.dataset["version_fixture_plans"]}
            for result in first["fixtures"]:
                plan = plans[result["plan_id"]]
                v1_path = WORKSPACE_ROOT / plan["base_source"]["source_file"]
                v2_path = first_root / result["fixture_relative_path"]
                render_path = first_root / result["render_relative_path"]
                v1_bytes = v1_path.read_bytes()
                v2_bytes = v2_path.read_bytes()

                self.assertEqual(hashlib.sha256(v1_bytes).hexdigest(), original_hashes[plan["id"]])
                self.assertEqual(hashlib.sha256(v2_bytes).hexdigest(), result["v2_sha256"])
                self.assertTrue(v2_bytes.startswith(v1_bytes))
                self.assertTrue(result["v1_byte_prefix_preserved"])
                self.assertTrue(result["two_generation_runs_byte_identical"])
                self.assertEqual(len(PdfReader(v1_path).pages) + 1, plan["v2_page"])
                v2_reader = PdfReader(v2_path)
                self.assertEqual(len(v2_reader.pages), plan["v2_page"])
                self.assertAlmostEqual(
                    float(v2_reader.pages[-1].mediabox.width), A4[0], places=3
                )
                self.assertAlmostEqual(
                    float(v2_reader.pages[-1].mediabox.height), A4[1], places=3
                )

                with pdfplumber.open(v2_path) as pdf:
                    final_text = pdf.pages[-1].extract_text() or ""
                self.assertIn(plan["v2_marker"], final_text)
                self.assertIn(
                    self.module.normalized_text(plan["v2_only_text"]),
                    self.module.normalized_text(final_text),
                )

                with Image.open(render_path) as rendered:
                    self.assertGreater(rendered.width, 1000)
                    self.assertGreater(rendered.height, 1000)
                    grayscale = rendered.convert("L")
                    extrema = grayscale.getextrema()
                    self.assertIsNotNone(extrema)
                    self.assertLess(extrema[0], extrema[1])
                    korean_text_band = grayscale.crop(
                        (
                            0,
                            int(rendered.height * 0.49),
                            rendered.width,
                            int(rendered.height * 0.55),
                        )
                    )
                    dark_pixels = sum(
                        pixel < 220
                        for pixel in korean_text_band.get_flattened_data()
                    )
                    self.assertGreater(
                        dark_pixels,
                        100,
                        "the Korean V2 sentence must be visibly rendered",
                    )

        for plan in self.dataset["version_fixture_plans"]:
            path = WORKSPACE_ROOT / plan["base_source"]["source_file"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original_hashes[plan["id"]])

    def test_v1_hash_mismatch_refuses_all_outputs(self) -> None:
        dataset = copy.deepcopy(self.dataset)
        dataset["version_fixture_plans"][0]["v1_source_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "V1 SHA-256 mismatch"):
                self.generate(root, dataset)
            self.assertFalse((root / "fixtures").exists())
            self.assertFalse((root / "renders").exists())

    def test_ready_fixture_v1_ground_truth_link_refuses_all_outputs(self) -> None:
        dataset = copy.deepcopy(self.dataset)
        plan = dataset["version_fixture_plans"][0]
        query = next(item for item in dataset["queries"] if item["id"] == "F01")
        page = query["candidate_evidence"][0]["page"]
        excerpt = query["candidate_evidence"][0]["supporting_excerpt"]
        query["candidate_evidence"] = [
            {
                "source_file": plan["base_source"]["source_file"],
                "source_sha256": plan["v1_source_sha256"],
                "page": page,
                "supporting_excerpt": excerpt,
            }
        ]
        query["expected"]["relevant"] = [
            {
                "source_sha256": plan["v1_source_sha256"],
                "version": 1,
                "pages": [page],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                ValueError, "F01.*READY fixture V1"
            ):
                self.generate(root, dataset)
            self.assertFalse((root / "fixtures").exists())
            self.assertFalse((root / "renders").exists())

    def test_checked_in_draft_is_machine_ready_but_still_human_pending(self) -> None:
        manifest_path = (
            Path(__file__).parent
            / "fixtures"
            / "version-sensitive"
            / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_by_plan = {
            item["plan_id"]: item for item in manifest["fixtures"]
        }
        queries = {query["id"]: query for query in self.dataset["queries"]}

        self.assertEqual(self.dataset["status"], "DRAFT")
        self.assertEqual(self.dataset["human_verification"]["status"], "PENDING")
        self.assertEqual(manifest["status"], "MACHINE_READY_HUMAN_REVIEW_PENDING")
        self.assertFalse(manifest["claim_eligible"])
        for plan in self.dataset["version_fixture_plans"]:
            self.assertEqual(plan["status"], "READY")
            fixture = manifest_by_plan[plan["id"]]
            fixture_path = WORKSPACE_ROOT / plan["v2_source_file"]
            render_path = WORKSPACE_ROOT / plan["v2_render_file"]
            self.assertTrue(fixture_path.is_file())
            self.assertTrue(render_path.is_file())
            self.assertEqual(
                hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
                plan["v2_source_sha256"],
            )
            self.assertEqual(
                hashlib.sha256(render_path.read_bytes()).hexdigest(),
                fixture["render_sha256"],
            )
            self.assertEqual(fixture["v2_sha256"], plan["v2_source_sha256"])
            self.assertEqual(fixture["v2_page_count"], plan["v2_page"])
            self.assertEqual(
                plan["machine_verification_manifest"],
                "syncbase-infra/evaluation/fixtures/version-sensitive/manifest.json",
            )

            query = queries[plan["query_id"]]
            self.assertEqual(query["ground_truth_state"], "VERIFIED_VERSION_PAIR")
            self.assertEqual(
                query["candidate_evidence_role"],
                "SYNTHETIC_V2_MARKER_GROUND_TRUTH",
            )
            self.assertEqual(
                query["expected"]["relevant"],
                [
                    {
                        "source_sha256": plan["v2_source_sha256"],
                        "version": 2,
                        "pages": [plan["v2_page"]],
                    }
                ],
            )
            self.assertEqual(
                query["candidate_evidence"],
                [
                    {
                        "source_file": plan["v2_source_file"],
                        "source_sha256": plan["v2_source_sha256"],
                        "page": plan["v2_page"],
                        "supporting_excerpt": plan["v2_only_text"],
                    }
                ],
            )
            self.assertEqual(
                query["expected"]["forbidden"],
                [
                    {
                        "source_sha256": plan["v1_source_sha256"],
                        "version": 1,
                    }
                ],
            )

        plans_by_v2 = {
            plan["v2_source_sha256"]: plan
            for plan in self.dataset["version_fixture_plans"]
        }
        rebound_ids: set[str] = set()
        for query in self.dataset["queries"]:
            if query["category"] not in {
                "factual_paraphrase",
                "exact_identifier",
            }:
                continue
            evidence = query["candidate_evidence"][0]
            plan = plans_by_v2.get(evidence["source_sha256"])
            if plan is None:
                continue
            rebound_ids.add(query["id"])
            self.assertEqual(evidence["source_file"], plan["v2_source_file"])
            self.assertEqual(
                query["expected"]["relevant"][0],
                {
                    "source_sha256": plan["v2_source_sha256"],
                    "version": 2,
                    "pages": [evidence["page"]],
                },
            )
            with pdfplumber.open(
                WORKSPACE_ROOT / plan["base_source"]["source_file"]
            ) as v1_pdf, pdfplumber.open(
                WORKSPACE_ROOT / plan["v2_source_file"]
            ) as v2_pdf:
                v1_text = v1_pdf.pages[evidence["page"] - 1].extract_text() or ""
                v2_text = v2_pdf.pages[evidence["page"] - 1].extract_text() or ""
            normalized_excerpt = self.module.normalized_text(
                evidence["supporting_excerpt"]
            )
            self.assertIn(normalized_excerpt, self.module.normalized_text(v1_text))
            self.assertIn(normalized_excerpt, self.module.normalized_text(v2_text))

        self.assertEqual(
            rebound_ids,
            {"F01", "F02", "F03", "F09", "F10", "I01", "I02", "I03", "I04"},
        )


if __name__ == "__main__":
    unittest.main()

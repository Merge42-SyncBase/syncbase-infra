from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


MODULE_PATH = Path(__file__).with_name("collect_draft_observations.py")
DRAFT_PATH = Path(__file__).with_name("queries.round1.draft.json")
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "collect_draft_observations", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiagnosticHandler(BaseHTTPRequestHandler):
    raw_pdf = b""
    search_calls: list[str] = []
    expected_cookie = "syncbase_session=test-session-value"
    force_search_error = False
    active_version = 2

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.headers.get("Cookie") != self.expected_cookie:
            self.send_json(
                {"error": {"code": "SESSION_EXPIRED", "message": "not logged in"}},
                status=401,
            )
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/documents":
            self.send_json(
                {
                    "documents": [
                        {
                            "id": "document-one",
                            "name": "diagnostic document",
                            "activeVersion": self.active_version,
                            "latestVersion": self.active_version,
                            "latestStatus": "ACTIVE",
                            "updatedAt": "2026-08-25T00:00:00Z",
                        }
                    ],
                    "limit": 100,
                    "offset": 0,
                }
            )
            return
        if parsed.path == "/api/v1/documents/document-one":
            self.send_json(
                {
                    "id": "document-one",
                    "name": "diagnostic document",
                    "activeVersion": self.active_version,
                    "versions": [
                        {
                            "id": f"version-{self.active_version}",
                            "versionNumber": self.active_version,
                            "status": "ACTIVE",
                            "active": True,
                            "pageCount": 9,
                        }
                    ],
                }
            )
            return
        if parsed.path == (
            "/api/v1/documents/document-one/versions/"
            f"{self.active_version}/raw.pdf"
        ):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(self.raw_pdf)))
            self.end_headers()
            self.wfile.write(self.raw_pdf)
            return
        if parsed.path == "/api/v1/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self.search_calls.append(query)
            if self.force_search_error:
                self.send_json(
                    {
                        "error": {
                            "code": "TEMPORARILY_UNAVAILABLE",
                            "message": "do not echo test-session-value",
                        }
                    },
                    status=503,
                )
                return
            self.send_json(
                {
                    "query": query,
                    "grounding_status": "SUPPORTED",
                    "grounding_reason": None,
                    "results": [
                        {
                            "rank": 1,
                            "score": 0.907,
                            "document_id": "document-one",
                            "document_name": "diagnostic document",
                            "version_id": f"version-{self.active_version}",
                            "document_version": self.active_version,
                            "page_number": 3,
                            "snippet": "diagnostic snippet",
                            "source_url": (
                                "http://127.0.0.1/sources/document-one/"
                                f"versions/{self.active_version}?page=3"
                            ),
                        }
                    ],
                }
            )
            return
        self.send_json({"error": {"code": "NOT_FOUND"}}, status=404)


@contextlib.contextmanager
def diagnostic_server(raw_pdf: bytes):
    DiagnosticHandler.raw_pdf = raw_pdf
    DiagnosticHandler.search_calls = []
    DiagnosticHandler.force_search_error = False
    DiagnosticHandler.active_version = 2
    server = ThreadingHTTPServer(("127.0.0.1", 0), DiagnosticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def write_cookie_jar(path: Path) -> None:
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_127.0.0.1\tFALSE\t/\tFALSE\t2147483647\t"
        "syncbase_session\ttest-session-value\n",
        encoding="utf-8",
    )


class DraftObservationCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.draft = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
        first_evidence = next(
            query["candidate_evidence"][0]
            for query in self.draft["queries"]
            if query["category"] == "factual_paraphrase"
        )
        self.raw_pdf = (WORKSPACE_ROOT / first_evidence["source_file"]).read_bytes()
        self.raw_sha256 = hashlib.sha256(self.raw_pdf).hexdigest()

    def test_cli_collects_only_15_answerable_and_10_no_answer_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory, diagnostic_server(
            self.raw_pdf
        ) as base_url:
            root = Path(directory)
            cookie_jar = root / "cookie.jar"
            output = root / "observations.json"
            write_cookie_jar(cookie_jar)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = self.module.main(
                    [
                        "--dataset",
                        str(DRAFT_PATH),
                        "--base-url",
                        base_url,
                        "--session-cookie-file",
                        str(cookie_jar),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(status, 0, stderr.getvalue())
            artifact = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                artifact["artifact_status"],
                "DRAFT_LOCAL_ONLY_NOT_RELEASE_EVIDENCE",
            )
            self.assertEqual(artifact["artifact_kind"], "DRAFT_RETRIEVAL_OBSERVATIONS")
            self.assertEqual(artifact["evidence_grade"], "DIAGNOSTIC")
            self.assertFalse(artifact["claim_eligible"])
            self.assertEqual(artifact["benchmark_result"], "NOT_EVALUATED")
            self.assertFalse(artifact["release_eligible"])
            self.assertEqual(artifact["collection_status"], "INCOMPLETE")
            self.assertEqual(
                artifact["input"]["draft_dataset_file_sha256"],
                hashlib.sha256(DRAFT_PATH.read_bytes()).hexdigest(),
            )
            self.assertEqual(len(artifact["queries"]), 25)
            self.assertEqual(
                [item["id"] for item in artifact["skipped_queries"]],
                ["V01", "V02", "V03", "V04", "V05"],
            )
            self.assertEqual(
                {item["reason"] for item in artifact["skipped_queries"]},
                {"VERSION_CASE_EXCLUDED_FROM_DRAFT_DIAGNOSTIC"},
            )
            self.assertEqual(len(DiagnosticHandler.search_calls), 25)
            self.assertEqual(
                artifact["source_bindings"][0]["source_sha256"], self.raw_sha256
            )
            first_hit = artifact["queries"][0]["results"][0]
            self.assertEqual(first_hit["source_sha256"], self.raw_sha256)
            self.assertEqual(first_hit["source_mapping_status"], "MAPPED")
            self.assertEqual(first_hit["version"], 2)
            self.assertEqual(first_hit["page"], 3)
            self.assertEqual(first_hit["rank"], 1)
            self.assertEqual(first_hit["score"], 0.907)
            self.assertGreaterEqual(artifact["queries"][0]["latency_ms"], 0)
            no_answer = next(item for item in artifact["queries"] if item["id"] == "N01")
            self.assertTrue(no_answer["expected_no_answer"])
            self.assertEqual(no_answer["grounding_status"], "SUPPORTED")
            self.assertEqual(len(no_answer["results"]), 1)
            serialized = output.read_text(encoding="utf-8") + stdout.getvalue() + stderr.getvalue()
            self.assertNotIn("test-session-value", serialized)
            self.assertIn("human_verification:PENDING", artifact["release_blockers"])
            self.assertIn("release_bindings:PENDING", artifact["release_blockers"])
            self.assertIn(
                "dataset_role:CALIBRATION_ONLY", artifact["release_blockers"]
            )
            self.assertEqual(artifact["summary"]["query_count"], 25)
            self.assertEqual(artifact["summary"]["no_answer_supported_count"], 10)
            self.assertEqual(artifact["summary"]["unmapped_hits"], 0)
            self.assertEqual(artifact["summary"]["missing_expected_source_count"], 9)
            self.assertEqual(
                artifact["diagnostic_scope"], "25_QUERY_DRAFT_DIAGNOSTIC"
            )
            self.assertFalse(
                artifact["collection_scope"]["ready_version_cases_included"]
            )

    def test_opt_in_collects_all_30_ready_queries_in_dataset_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory, diagnostic_server(
            self.raw_pdf
        ) as base_url:
            root = Path(directory)
            cookie_jar = root / "cookie.jar"
            output = root / "observations.json"
            write_cookie_jar(cookie_jar)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = self.module.main(
                    [
                        "--dataset",
                        str(DRAFT_PATH),
                        "--base-url",
                        base_url,
                        "--session-cookie-file",
                        str(cookie_jar),
                        "--output",
                        str(output),
                        "--include-ready-version-cases",
                    ]
                )

            self.assertEqual(status, 0, stderr.getvalue())
            artifact = json.loads(output.read_text(encoding="utf-8"))
            expected_ids = [query["id"] for query in self.draft["queries"]]
            self.assertEqual(
                [query["id"] for query in artifact["queries"]], expected_ids
            )
            self.assertEqual(DiagnosticHandler.search_calls, [
                query["query"] for query in self.draft["queries"]
            ])
            self.assertEqual(len(artifact["queries"]), 30)
            self.assertEqual(artifact["skipped_queries"], [])
            self.assertEqual(
                artifact["diagnostic_scope"], "30_QUERY_DRAFT_DIAGNOSTIC"
            )
            self.assertEqual(artifact["benchmark_result"], "NOT_EVALUATED")
            self.assertEqual(artifact["evidence_grade"], "DIAGNOSTIC")
            self.assertFalse(artifact["claim_eligible"])
            self.assertFalse(artifact["release_eligible"])
            self.assertEqual(artifact["dataset"]["status"], "DRAFT")
            self.assertEqual(artifact["dataset"]["dataset_role"], "CALIBRATION")
            self.assertEqual(
                artifact["dataset"]["human_verification_status"], "PENDING"
            )
            self.assertTrue(
                artifact["collection_scope"]["ready_version_cases_included"]
            )
            self.assertEqual(
                artifact["collection_scope"]["selected_query_count"], 30
            )
            self.assertEqual(
                artifact["collection_scope"]["query_categories"],
                {
                    "exact_identifier": 5,
                    "factual_paraphrase": 10,
                    "no_answer": 10,
                    "version_sensitive": 5,
                },
            )
            version_observation = next(
                query for query in artifact["queries"] if query["id"] == "V01"
            )
            self.assertEqual(version_observation["expected_binding_status"], "BOUND")
            self.assertEqual(len(version_observation["results"]), 1)
            self.assertEqual(
                version_observation["results"][0]["source_sha256"], self.raw_sha256
            )
            self.assertEqual(
                version_observation["results"][0]["source_mapping_status"], "MAPPED"
            )
            self.assertEqual(len(artifact["source_bindings"]), 1)
            self.assertEqual(artifact["summary"]["query_count"], 30)

    def test_opt_in_refuses_any_pending_version_plan_before_http(self) -> None:
        pending = copy.deepcopy(self.draft)
        plan = pending["version_fixture_plans"][2]
        plan["status"] = "PLANNED_NOT_GENERATED"
        plan["v2_source_sha256"] = None
        query = next(item for item in pending["queries"] if item["id"] == "V03")
        query["ground_truth_state"] = "HUMAN_GATED_V2_NOT_GENERATED"
        query["candidate_evidence_role"] = "V1_BASE_ONLY_NOT_V2_GROUND_TRUTH"
        query["candidate_evidence"] = [copy.deepcopy(plan["base_source"])]
        query["expected"]["relevant"] = []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = root / "pending.json"
            cookie_jar = root / "cookie.jar"
            output = root / "observations.json"
            dataset_path.write_text(
                json.dumps(pending, ensure_ascii=False), encoding="utf-8"
            )
            write_cookie_jar(cookie_jar)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = self.module.main(
                    [
                        "--dataset",
                        str(dataset_path),
                        "--source-root",
                        str(WORKSPACE_ROOT),
                        "--base-url",
                        "http://127.0.0.1:9",
                        "--session-cookie-file",
                        str(cookie_jar),
                        "--output",
                        str(output),
                        "--include-ready-version-cases",
                    ]
                )

            self.assertEqual(status, 2)
            self.assertFalse(output.exists())
            self.assertIn("requires all five V queries", stderr.getvalue())
            self.assertIn("V03", stderr.getvalue())

    def test_release_intent_is_refused_before_http_or_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cookie_jar = root / "cookie.jar"
            output = root / "release.json"
            write_cookie_jar(cookie_jar)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = self.module.main(
                    [
                        "--dataset",
                        str(DRAFT_PATH),
                        "--base-url",
                        "http://127.0.0.1:9",
                        "--session-cookie-file",
                        str(cookie_jar),
                        "--output",
                        str(output),
                        "--purpose",
                        "release",
                    ]
                )
            self.assertEqual(status, 2)
            self.assertFalse(output.exists())
            self.assertIn("release collection refused", stderr.getvalue())
            self.assertNotIn("test-session-value", stderr.getvalue())

    def test_prospective_holdout_is_refused_before_http_or_output(self) -> None:
        holdout_path = Path(__file__).with_name(
            "queries.round1.holdout.draft.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cookie_jar = root / "cookie.jar"
            output = root / "holdout-observations.json"
            write_cookie_jar(cookie_jar)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = self.module.main(
                    [
                        "--dataset",
                        str(holdout_path),
                        "--source-root",
                        str(WORKSPACE_ROOT),
                        "--base-url",
                        "http://127.0.0.1:9",
                        "--session-cookie-file",
                        str(cookie_jar),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(status, 2)
            self.assertFalse(output.exists())
            self.assertIn(
                "prospective holdout collection refused", stderr.getvalue()
            )
            self.assertNotIn("test-session-value", stderr.getvalue())

    def test_http_error_body_and_cookie_are_not_echoed_or_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory, diagnostic_server(
            self.raw_pdf
        ) as base_url:
            root = Path(directory)
            cookie_jar = root / "cookie.jar"
            output = root / "observations.json"
            write_cookie_jar(cookie_jar)
            DiagnosticHandler.force_search_error = True

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = self.module.main(
                    [
                        "--dataset",
                        str(DRAFT_PATH),
                        "--base-url",
                        base_url,
                        "--session-cookie-file",
                        str(cookie_jar),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(status, 2)
            self.assertFalse(output.exists())
            self.assertIn("search F01 failed with HTTP 503", stderr.getvalue())
            self.assertNotIn("test-session-value", stderr.getvalue())

    def test_expected_hash_at_the_wrong_version_is_explicitly_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory, diagnostic_server(
            self.raw_pdf
        ) as base_url:
            root = Path(directory)
            cookie_jar = root / "cookie.jar"
            output = root / "observations.json"
            write_cookie_jar(cookie_jar)
            DiagnosticHandler.active_version = 1

            status = self.module.main(
                [
                    "--dataset",
                    str(DRAFT_PATH),
                    "--base-url",
                    base_url,
                    "--session-cookie-file",
                    str(cookie_jar),
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(status, 0)
            artifact = json.loads(output.read_text(encoding="utf-8"))
            mismatch = next(
                item
                for item in artifact["source_binding_version_mismatches"]
                if item["source_sha256"] == self.raw_sha256
            )
            self.assertEqual(mismatch["expected_version"], 2)
            self.assertEqual(mismatch["observed_active_versions"], [1])
            first = next(item for item in artifact["queries"] if item["id"] == "F01")
            self.assertEqual(
                first["expected_binding_status"], "SOURCE_PRESENT_WRONG_VERSION"
            )
            self.assertEqual(artifact["collection_status"], "INCOMPLETE")

    def test_frozen_input_and_non_loopback_target_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cookie_jar = root / "cookie.jar"
            output = root / "observations.json"
            frozen_path = root / "frozen.json"
            write_cookie_jar(cookie_jar)
            frozen = dict(self.draft)
            frozen["status"] = "FROZEN"
            frozen_path.write_text(json.dumps(frozen), encoding="utf-8")

            status = self.module.main(
                [
                    "--dataset",
                    str(frozen_path),
                    "--base-url",
                    "http://127.0.0.1:9",
                    "--session-cookie-file",
                    str(cookie_jar),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(status, 2)
            self.assertFalse(output.exists())

            status = self.module.main(
                [
                    "--dataset",
                    str(DRAFT_PATH),
                    "--base-url",
                    "https://example.com",
                    "--session-cookie-file",
                    str(cookie_jar),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(status, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

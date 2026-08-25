from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pypdf import PdfReader


MODULE_PATH = Path(__file__).with_name("collect_frozen_observations.py")
EVALUATOR_PATH = Path(__file__).with_name("evaluate_retrieval.py")
HOLDOUT_PATH = Path(__file__).with_name("queries.round1.holdout.draft.json")
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SESSION_VALUE = "frozen-test-session-secret"
MODEL_BYTES = b"deterministic frozen collector model fixture\n"
TOKENIZER_BYTES = b'{"fixture":"deterministic tokenizer"}'
MODEL_SHA256 = hashlib.sha256(MODEL_BYTES).hexdigest()
TOKENIZER_SHA256 = hashlib.sha256(TOKENIZER_BYTES).hexdigest()
PROFILE_BYTES = (
    "{\"chunk_overlap_tokens\":64,\"chunk_size_tokens\":384,"
    "\"chunker_id\":\"page-aware-recursive-v1\",\"distance\":\"cosine\","
    "\"embedding_model_id\":\"intfloat/multilingual-e5-small\","
    f"\"embedding_model_sha256\":\"{MODEL_SHA256}\","
    "\"minimum_score\":0.930000,\"onnx_runtime_id\":\"onnxruntime-1.26.0\","
    "\"parser_id\":\"pdfium-wasm-1.19.6\",\"provider\":\"local-onnx\","
    f"\"tokenizer_sha256\":\"{TOKENIZER_SHA256}\",\"vector_dimension\":384}}"
).encode("utf-8")
PROFILE_SHA256 = hashlib.sha256(PROFILE_BYTES).hexdigest()
DATABASE_IDENTITY_PAYLOAD = {
    "schema_version": "1.0",
    "binding_kind": "SYNCBASE_DATABASE_IDENTITY_V1",
    "environment_id_sha256": "a" * 64,
    "database_name_sha256": "b" * 64,
    "migration_head_sha256": "c" * 64,
}


def load_python_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_cookie_jar(path: Path, *, mode: int = 0o600) -> None:
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_127.0.0.1\tFALSE\t/\tFALSE\t2147483647\t"
        f"syncbase_session\t{SESSION_VALUE}\n",
        encoding="utf-8",
    )
    path.chmod(mode)


def canonical_sha256(value: object) -> str:
    content = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def source_release_payload(dataset: dict) -> dict:
    return {
        "schema_version": "1.0",
        "binding_kind": "SYNCBASE_SOURCE_RELEASE_V1",
        "repository_revisions": dataset["bindings"]["repository_revisions"],
    }


def corpus_payload(sources: list[dict]) -> dict:
    bindings = [
        {
            "document_id": source["document_id"],
            "version_id": source["version_id"],
            "source_sha256": source["source_sha256"],
            "version": source["version"],
            "active": True,
            "page_count": source["page_count"],
            "raw_pdf_artifact": (
                f"sources/{source['source_sha256']}-v{source['version']}.pdf"
            ),
            "raw_pdf_sha256": source["source_sha256"],
        }
        for source in sources
    ]
    bindings.sort(
        key=lambda binding: (
            binding["source_sha256"],
            binding["version"],
            binding["document_id"],
            binding["version_id"],
        )
    )
    return {
        "schema_version": "1.0",
        "binding_kind": "SYNCBASE_ACTIVE_CORPUS_V1",
        "sources": bindings,
    }


def make_release_draft() -> dict:
    draft = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    draft["query_exposure"] = "NOT_QUERIED_BEFORE_FREEZE"
    draft["human_verification"] = {
        "status": "APPROVED",
        "worksheet": "evaluation/holdout-ground-truth-verification.md",
        "reviewer": "frozen-collector-unit-test",
        "reviewed_at": "2026-08-25T00:00:00Z",
    }
    draft["bindings"]["model_sha256"] = MODEL_SHA256
    draft["bindings"]["tokenizer_sha256"] = TOKENIZER_SHA256
    draft["bindings"]["profile_sha256"] = PROFILE_SHA256
    for position, name in enumerate(
        ("frontend", "embedding", "was", "infra", "mcp"), start=1
    ):
        draft["bindings"]["repository_revisions"][name] = f"{position:040x}"
    return draft


def make_frozen_dataset(evaluator) -> tuple[dict, list[dict]]:
    draft = make_release_draft()
    sources = build_source_specs(draft)
    draft["bindings"]["corpus_sha256"] = canonical_sha256(
        corpus_payload(sources)
    )
    draft["bindings"]["database_identity_sha256"] = canonical_sha256(
        DATABASE_IDENTITY_PAYLOAD
    )
    draft["bindings"]["source_release_sha256"] = canonical_sha256(
        source_release_payload(draft)
    )
    frozen = evaluator._freeze_dataset_after_validation(
        draft, frozen_at="2026-08-25T00:30:00Z"
    )
    return frozen, sources


def build_source_specs(dataset: dict) -> list[dict]:
    source_files: dict[str, str] = {}
    for query in dataset["queries"]:
        for evidence in query.get("candidate_evidence", []):
            source_files[evidence["source_sha256"]] = evidence["source_file"]
    requirements = {
        (target["source_sha256"], target["version"])
        for query in dataset["queries"]
        for target in query["expected"]["relevant"]
    }
    specs: list[dict] = []
    for position, (source_sha256, version) in enumerate(
        sorted(requirements), start=1
    ):
        source_path = WORKSPACE_ROOT / source_files[source_sha256]
        content = source_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != source_sha256:
            raise AssertionError(f"fixture hash mismatch for source {position}")
        specs.append(
            {
                "document_id": f"document-{position:02d}",
                "document_name": f"frozen source {position:02d}",
                "version_id": f"version-{position:02d}-v{version}",
                "version": version,
                "page_count": len(PdfReader(io.BytesIO(content)).pages),
                "source_sha256": source_sha256,
                "content": content,
            }
        )
    return specs


class FrozenHandler(BaseHTTPRequestHandler):
    sources: list[dict] = []
    events: list[tuple] = []
    fail_search_at: int | None = None
    bad_mapping_at: int | None = None
    raw_overrides: dict[str, bytes] = {}
    source_origin: str | None = None
    bad_source_origin_at: int | None = None
    search_count = 0

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
        if self.headers.get("Cookie") != f"syncbase_session={SESSION_VALUE}":
            self.send_json(
                {"error": {"code": "UNAUTHORIZED", "message": SESSION_VALUE}},
                status=401,
            )
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/documents":
            self.events.append(("documents",))
            self.send_json(
                {
                    "documents": [
                        {
                            "id": source["document_id"],
                            "name": source["document_name"],
                            "activeVersion": source["version"],
                            "latestVersion": source["version"],
                            "latestStatus": "ACTIVE",
                        }
                        for source in self.sources
                    ],
                    "limit": 100,
                    "offset": 0,
                }
            )
            return

        for source in self.sources:
            document_path = f"/api/v1/documents/{source['document_id']}"
            raw_path = (
                f"{document_path}/versions/{source['version']}/raw.pdf"
            )
            if parsed.path == document_path:
                self.events.append(("detail", source["document_id"]))
                self.send_json(
                    {
                        "id": source["document_id"],
                        "name": source["document_name"],
                        "activeVersion": source["version"],
                        "versions": [
                            {
                                "id": source["version_id"],
                                "versionNumber": source["version"],
                                "status": "ACTIVE",
                                "active": True,
                                "pageCount": source["page_count"],
                            }
                        ],
                    }
                )
                return
            if parsed.path == raw_path:
                self.events.append(("raw", source["document_id"]))
                content = self.raw_overrides.get(
                    source["document_id"], source["content"]
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        if parsed.path == "/api/v1/search":
            parameters = parse_qs(parsed.query)
            query = parameters.get("q", [""])[0]
            limit = parameters.get("limit", [""])[0]
            type(self).search_count += 1
            search_number = type(self).search_count
            self.events.append(("search", query, limit))
            if self.fail_search_at == search_number:
                self.send_json(
                    {
                        "error": {
                            "code": "TEMPORARILY_UNAVAILABLE",
                            "message": f"never disclose {SESSION_VALUE}",
                        }
                    },
                    status=503,
                )
                return
            source = self.sources[0]
            document_id = source["document_id"]
            version_id = source["version_id"]
            if self.bad_mapping_at == search_number:
                document_id = "unknown-document"
                version_id = "unknown-version"
            source_origin = self.source_origin
            if self.bad_source_origin_at == search_number:
                source_origin = "https://evil.invalid"
            source_url = (
                f"/sources/{document_id}/versions/{source['version']}?page=1"
            )
            if source_origin is not None:
                source_url = source_origin + source_url
            self.send_json(
                {
                    "query": query,
                    "grounding_status": "SUPPORTED",
                    "grounding_reason": None,
                    "results": [
                        {
                            "rank": 1,
                            "score": 0.95,
                            "document_id": document_id,
                            "document_name": source["document_name"],
                            "version_id": version_id,
                            "document_version": source["version"],
                            "page_number": 1,
                            "snippet": "frozen evidence snippet",
                            "source_url": source_url,
                        }
                    ],
                }
            )
            return

        self.send_json({"error": {"code": "NOT_FOUND"}}, status=404)


@contextmanager
def frozen_server(
    sources: list[dict],
    *,
    fail_search_at: int | None = None,
    bad_mapping_at: int | None = None,
    raw_overrides: dict[str, bytes] | None = None,
    source_origin: str | None = None,
    bad_source_origin_at: int | None = None,
):
    FrozenHandler.sources = copy.deepcopy(sources)
    FrozenHandler.events = []
    FrozenHandler.fail_search_at = fail_search_at
    FrozenHandler.bad_mapping_at = bad_mapping_at
    FrozenHandler.raw_overrides = raw_overrides or {}
    FrozenHandler.source_origin = source_origin
    FrozenHandler.bad_source_origin_at = bad_source_origin_at
    FrozenHandler.search_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FrozenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


class FrozenObservationCollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evaluator = load_python_module(EVALUATOR_PATH, "frozen_test_evaluator")
        cls.frozen, cls.sources = make_frozen_dataset(cls.evaluator)

    def setUp(self) -> None:
        self.module = load_python_module(MODULE_PATH, "collect_frozen_observations")

    def write_dataset(self, root: Path, dataset: dict | None = None) -> Path:
        path = root / "frozen.json"
        path.write_text(
            json.dumps(dataset or self.frozen, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def args(
        self,
        dataset_path: Path,
        cookie_path: Path,
        evidence_dir: Path,
        base_url: str,
        *,
        acknowledgment: str | None = None,
        expected_source_origin: str = "https://evidence.syncbase.example",
    ) -> list[str]:
        values = [
            "--mode",
            "collect",
            "--dataset",
            str(dataset_path),
            "--base-url",
            base_url,
            "--session-cookie-file",
            str(cookie_path),
            "--evidence-dir",
            str(evidence_dir),
            "--expected-source-origin",
            expected_source_origin,
        ]
        database_identity_path = dataset_path.parent / "database-identity.json"
        database_identity_path.write_text(
            json.dumps(DATABASE_IDENTITY_PAYLOAD, indent=2) + "\n",
            encoding="utf-8",
        )
        values.extend(
            ["--database-identity-json", str(database_identity_path)]
        )
        values.extend(self.write_runtime_artifacts(dataset_path.parent))
        if acknowledgment is not None:
            values.extend(["--acknowledge-one-shot-exposure", acknowledgment])
        return values

    def write_runtime_artifacts(self, root: Path) -> list[str]:
        model_path = root / "model.onnx"
        tokenizer_path = root / "tokenizer.json"
        profile_path = root / "retrieval-profile.json"
        model_path.write_bytes(MODEL_BYTES)
        tokenizer_path.write_bytes(TOKENIZER_BYTES)
        profile_path.write_bytes(PROFILE_BYTES)
        return [
            "--model-artifact",
            str(model_path),
            "--tokenizer-artifact",
            str(tokenizer_path),
            "--profile-artifact",
            str(profile_path),
        ]

    def run_collector(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = self.module.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_complete_run_preflights_sources_then_queries_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources, source_origin="https://evidence.syncbase.example"
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root)
            cookie_path = root / "cookie.jar"
            evidence_dir = root / "evidence"
            write_cookie_jar(cookie_path)

            status, stdout, stderr = self.run_collector(
                self.args(
                    dataset_path,
                    cookie_path,
                    evidence_dir,
                    base_url,
                    acknowledgment=self.frozen["dataset_sha256"],
                )
            )

            self.assertEqual(status, 0, stderr)
            searches = [event for event in FrozenHandler.events if event[0] == "search"]
            raws = [event for event in FrozenHandler.events if event[0] == "raw"]
            self.assertEqual(len(raws), len(self.sources))
            self.assertEqual(len(searches), 30)
            self.assertEqual(
                [event[1] for event in searches],
                [query["query"] for query in self.frozen["queries"]],
            )
            self.assertEqual({event[2] for event in searches}, {"5"})
            first_search = next(
                position
                for position, event in enumerate(FrozenHandler.events)
                if event[0] == "search"
            )
            self.assertEqual(
                sum(event[0] == "raw" for event in FrozenHandler.events[:first_search]),
                len(self.sources),
            )

            exact_path = evidence_dir / "exact-observations.json"
            exact = json.loads(exact_path.read_text(encoding="utf-8"))
            self.assertEqual(exact["dataset_sha256"], self.frozen["dataset_sha256"])
            self.assertEqual(exact["bindings"], self.frozen["bindings"])
            self.assertEqual(exact["retrieval_mode"], "exact")
            self.assertEqual(exact["retrieval_limit"], 5)
            self.assertEqual(
                exact["source_origin"], "https://evidence.syncbase.example"
            )
            self.assertEqual(len(exact["queries"]), 30)
            self.assertEqual(
                [query["id"] for query in exact["queries"]],
                [query["id"] for query in self.frozen["queries"]],
            )
            self.assertEqual(
                self.evaluator.validate_observations(self.frozen, exact, "exact"), []
            )
            self.assertEqual(len(exact["source_bindings"]), len(self.sources))
            for binding in exact["source_bindings"]:
                artifact = evidence_dir / binding["raw_pdf_artifact"]
                self.assertTrue(artifact.is_file())
                self.assertEqual(
                    hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    binding["source_sha256"],
                )
                self.assertEqual(binding["raw_pdf_sha256"], binding["source_sha256"])
                self.assertTrue(binding["active"])
                self.assertGreater(binding["page_count"], 0)

            runtime_artifacts = json.loads(
                (evidence_dir / "retrieval-artifacts.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                runtime_artifacts["bindings"],
                {
                    "model_sha256": MODEL_SHA256,
                    "tokenizer_sha256": TOKENIZER_SHA256,
                    "profile_sha256": PROFILE_SHA256,
                },
            )
            for record in runtime_artifacts["artifacts"].values():
                artifact = evidence_dir / record["artifact"]
                self.assertEqual(
                    hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    record["sha256"],
                )

            marker = json.loads(
                (evidence_dir / "query-exposure.json").read_text(encoding="utf-8")
            )
            terminal = json.loads(
                (evidence_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            progress = json.loads(
                (evidence_dir / "partial-progress.json").read_text(encoding="utf-8")
            )
            self.assertEqual(marker["status"], "QUERY_EXPOSURE_STARTED")
            self.assertEqual(terminal["status"], "COMPLETE")
            self.assertEqual(terminal["benchmark_result"], "NOT_EVALUATED")
            self.assertFalse(terminal["claim_eligible"])
            self.assertEqual(progress["status"], "COMPLETE")
            self.assertEqual(progress["completed_query_count"], 30)
            self.assertFalse(list(evidence_dir.rglob("*.tmp")))
            self.assertNotIn(SESSION_VALUE, stdout + stderr)
            self.assertNotIn("frozen evidence snippet", stdout + stderr)

    def test_preflight_hash_failure_downloads_all_sources_and_makes_zero_searches(self) -> None:
        corrupted_document = self.sources[0]["document_id"]
        replacement = self.sources[1]["content"]
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources,
            raw_overrides={corrupted_document: replacement},
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root)
            cookie_path = root / "cookie.jar"
            evidence_dir = root / "evidence"
            write_cookie_jar(cookie_path)

            status, _stdout, stderr = self.run_collector(
                self.args(
                    dataset_path,
                    cookie_path,
                    evidence_dir,
                    base_url,
                    acknowledgment=self.frozen["dataset_sha256"],
                )
            )

            self.assertEqual(status, 2)
            self.assertEqual(
                sum(event[0] == "raw" for event in FrozenHandler.events),
                len(self.sources),
            )
            self.assertFalse(
                any(event[0] == "search" for event in FrozenHandler.events)
            )
            self.assertFalse((evidence_dir / "query-exposure.json").exists())
            self.assertFalse((evidence_dir / "exact-observations.json").exists())
            terminal = json.loads(
                (evidence_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(terminal["status"], "INCOMPLETE")
            self.assertEqual(terminal["stage"], "PREFLIGHT")
            self.assertFalse(terminal["claim_eligible"])
            self.assertNotIn(SESSION_VALUE, stderr)

    def test_draft_preflight_emits_deterministic_binding_formulas_without_search(self) -> None:
        draft = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
        for position, name in enumerate(
            ("frontend", "embedding", "was", "infra", "mcp"), start=1
        ):
            draft["bindings"]["repository_revisions"][name] = f"{position:040x}"
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root, draft)
            cookie_path = root / "cookie.jar"
            database_path = root / "database-identity.json"
            evidence_dir = root / "preflight-evidence"
            write_cookie_jar(cookie_path)
            database_path.write_text(
                json.dumps(DATABASE_IDENTITY_PAYLOAD, indent=2) + "\n",
                encoding="utf-8",
            )

            status, stdout, stderr = self.run_collector(
                [
                    "--mode",
                    "preflight",
                    "--dataset",
                    str(dataset_path),
                    "--base-url",
                    base_url,
                    "--session-cookie-file",
                    str(cookie_path),
                    "--evidence-dir",
                    str(evidence_dir),
                    "--expected-source-origin",
                    "https://evidence.syncbase.example",
                    "--database-identity-json",
                    str(database_path),
                    *self.write_runtime_artifacts(root),
                ]
            )

            self.assertEqual(status, 0, stderr)
            self.assertFalse(
                any(event[0] == "search" for event in FrozenHandler.events)
            )
            self.assertFalse((evidence_dir / "query-exposure.json").exists())
            self.assertFalse((evidence_dir / "exact-observations.json").exists())
            manifest = json.loads(
                (evidence_dir / "corpus-manifest.json").read_text(encoding="utf-8")
            )
            payload = {
                "schema_version": manifest["schema_version"],
                "binding_kind": manifest["binding_kind"],
                "sources": manifest["sources"],
            }
            self.assertEqual(manifest["corpus_sha256"], canonical_sha256(payload))
            self.assertEqual(
                manifest["corpus_sha256"],
                canonical_sha256(corpus_payload(self.sources)),
            )
            formulas = json.loads(
                (evidence_dir / "binding-formulas.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                formulas["recommended_bindings"]["corpus_sha256"],
                manifest["corpus_sha256"],
            )
            self.assertEqual(
                formulas["recommended_bindings"]["database_identity_sha256"],
                canonical_sha256(DATABASE_IDENTITY_PAYLOAD),
            )
            self.assertEqual(
                formulas["recommended_bindings"]["source_release_sha256"],
                canonical_sha256(source_release_payload(draft)),
            )
            self.assertEqual(
                formulas["recommended_bindings"]["model_sha256"], MODEL_SHA256
            )
            self.assertEqual(
                formulas["recommended_bindings"]["tokenizer_sha256"],
                TOKENIZER_SHA256,
            )
            self.assertEqual(
                formulas["recommended_bindings"]["profile_sha256"], PROFILE_SHA256
            )
            terminal = json.loads(
                (evidence_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(terminal["status"], "PREFLIGHT_COMPLETE")
            self.assertEqual(terminal["completed_query_count"], 0)
            self.assertNotIn(SESSION_VALUE, stdout + stderr)

    def test_frozen_corpus_binding_mismatch_prevents_exposure_and_search(self) -> None:
        mismatched = copy.deepcopy(self.frozen)
        mismatched["bindings"]["corpus_sha256"] = "f" * 64
        mismatched["dataset_sha256"] = self.evaluator.dataset_sha256(mismatched)
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root, mismatched)
            cookie_path = root / "cookie.jar"
            evidence_dir = root / "evidence"
            write_cookie_jar(cookie_path)

            status, _stdout, _stderr = self.run_collector(
                self.args(
                    dataset_path,
                    cookie_path,
                    evidence_dir,
                    base_url,
                    acknowledgment=mismatched["dataset_sha256"],
                )
            )

            self.assertEqual(status, 2)
            self.assertFalse(
                any(event[0] == "search" for event in FrozenHandler.events)
            )
            self.assertFalse((evidence_dir / "query-exposure.json").exists())
            terminal = json.loads(
                (evidence_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(terminal["stage"], "PREFLIGHT")
            self.assertIn("CORPUS_BINDING_MISMATCH", terminal["error_codes"])

    def test_ack_dataset_cookie_and_target_gates_run_before_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = self.write_dataset(root)
            protected_cookie = root / "protected.jar"
            open_cookie = root / "open.jar"
            write_cookie_jar(protected_cookie)
            write_cookie_jar(open_cookie, mode=0o644)

            cases = [
                (
                    "missing acknowledgment",
                    self.args(
                        dataset_path,
                        protected_cookie,
                        root / "evidence-a",
                        "http://127.0.0.1:9",
                    ),
                    "acknowledgment",
                ),
                (
                    "wrong acknowledgment",
                    self.args(
                        dataset_path,
                        protected_cookie,
                        root / "evidence-b",
                        "http://127.0.0.1:9",
                        acknowledgment="f" * 64,
                    ),
                    "acknowledgment",
                ),
                (
                    "non-loopback",
                    self.args(
                        dataset_path,
                        protected_cookie,
                        root / "evidence-c",
                        "https://example.com",
                        acknowledgment=self.frozen["dataset_sha256"],
                    ),
                    "loopback",
                ),
                (
                    "open cookie mode",
                    self.args(
                        dataset_path,
                        open_cookie,
                        root / "evidence-d",
                        "http://127.0.0.1:9",
                        acknowledgment=self.frozen["dataset_sha256"],
                    ),
                    "0600",
                ),
            ]
            for name, arguments, message in cases:
                with self.subTest(name=name):
                    status, stdout, stderr = self.run_collector(arguments)
                    self.assertEqual(status, 2)
                    self.assertIn(message, stderr)
                    self.assertNotIn(SESSION_VALUE, stdout + stderr)

            draft = copy.deepcopy(self.frozen)
            draft["status"] = "DRAFT"
            draft.pop("dataset_sha256")
            draft.pop("frozen_at")
            draft_path = self.write_dataset(root, draft)
            status, _stdout, stderr = self.run_collector(
                self.args(
                    draft_path,
                    protected_cookie,
                    root / "evidence-e",
                    "http://127.0.0.1:9",
                    acknowledgment="0" * 64,
                )
            )
            self.assertEqual(status, 2)
            self.assertIn("frozen dataset validation failed", stderr)

            claimed = copy.deepcopy(self.frozen)
            claimed["benchmark_claim"] = "PASS"
            claimed["dataset_sha256"] = self.evaluator.dataset_sha256(claimed)
            claimed_path = self.write_dataset(root, claimed)
            status, _stdout, stderr = self.run_collector(
                self.args(
                    claimed_path,
                    protected_cookie,
                    root / "evidence-f",
                    "http://127.0.0.1:9",
                    acknowledgment=claimed["dataset_sha256"],
                )
            )
            self.assertEqual(status, 2)
            self.assertIn("benchmark_claim", stderr)

            mismatched_model = copy.deepcopy(self.frozen)
            mismatched_model["bindings"]["model_sha256"] = "f" * 64
            mismatched_model["dataset_sha256"] = self.evaluator.dataset_sha256(
                mismatched_model
            )
            dataset_path = self.write_dataset(root, mismatched_model)
            arguments = self.args(
                dataset_path,
                protected_cookie,
                root / "evidence-g",
                "http://127.0.0.1:9",
                acknowledgment=mismatched_model["dataset_sha256"],
            )
            status, stdout, stderr = self.run_collector(arguments)
            self.assertEqual(status, 2)
            self.assertIn("model_sha256", stderr)
            self.assertNotIn(SESSION_VALUE, stdout + stderr)

            dataset_path = self.write_dataset(root, self.frozen)
            arguments = self.args(
                dataset_path,
                protected_cookie,
                root / "evidence-h",
                "http://127.0.0.1:9",
                acknowledgment=self.frozen["dataset_sha256"],
                expected_source_origin="https://user:secret@example.com",
            )
            status, stdout, stderr = self.run_collector(arguments)
            self.assertEqual(status, 2)
            self.assertIn("source origin", stderr)
            self.assertNotIn("secret", stdout + stderr)

    def test_symlink_cookie_and_nonempty_evidence_directory_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root)
            cookie_path = root / "cookie.jar"
            cookie_link = root / "cookie-link.jar"
            evidence_dir = root / "evidence"
            write_cookie_jar(cookie_path)
            cookie_link.symlink_to(cookie_path)

            status, _stdout, stderr = self.run_collector(
                self.args(
                    dataset_path,
                    cookie_link,
                    evidence_dir,
                    base_url,
                    acknowledgment=self.frozen["dataset_sha256"],
                )
            )
            self.assertEqual(status, 2)
            self.assertIn("regular 0600", stderr)
            self.assertEqual(FrozenHandler.events, [])

            evidence_dir.mkdir()
            (evidence_dir / "existing.txt").write_text("do not overwrite")
            status, _stdout, stderr = self.run_collector(
                self.args(
                    dataset_path,
                    cookie_path,
                    evidence_dir,
                    base_url,
                    acknowledgment=self.frozen["dataset_sha256"],
                )
            )
            self.assertEqual(status, 2)
            self.assertIn("new or empty", stderr)
            self.assertEqual(FrozenHandler.events, [])
            self.assertEqual(
                (evidence_dir / "existing.txt").read_text(), "do not overwrite"
            )

    def test_mid_run_http_failure_is_terminal_incomplete_and_rerun_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources, fail_search_at=4
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root)
            cookie_path = root / "cookie.jar"
            evidence_dir = root / "evidence"
            write_cookie_jar(cookie_path)
            arguments = self.args(
                dataset_path,
                cookie_path,
                evidence_dir,
                base_url,
                acknowledgment=self.frozen["dataset_sha256"],
            )

            status, stdout, stderr = self.run_collector(arguments)

            self.assertEqual(status, 2)
            self.assertEqual(
                sum(event[0] == "search" for event in FrozenHandler.events), 4
            )
            self.assertTrue((evidence_dir / "query-exposure.json").is_file())
            self.assertFalse((evidence_dir / "exact-observations.json").exists())
            progress = json.loads(
                (evidence_dir / "partial-progress.json").read_text(encoding="utf-8")
            )
            terminal = json.loads(
                (evidence_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(progress["status"], "INCOMPLETE")
            self.assertEqual(progress["completed_query_count"], 3)
            self.assertEqual(len(progress["queries"]), 3)
            self.assertEqual(terminal["status"], "INCOMPLETE")
            self.assertEqual(terminal["stage"], "SEARCH")
            self.assertFalse(terminal["claim_eligible"])
            self.assertNotIn(SESSION_VALUE, stdout + stderr)

            FrozenHandler.events = []
            status, _stdout, stderr = self.run_collector(arguments)
            self.assertEqual(status, 2)
            self.assertIn("exposure marker", stderr)
            self.assertEqual(FrozenHandler.events, [])

    def test_mapping_contract_error_cannot_produce_complete_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources, bad_mapping_at=3
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root)
            cookie_path = root / "cookie.jar"
            evidence_dir = root / "evidence"
            write_cookie_jar(cookie_path)

            status, _stdout, _stderr = self.run_collector(
                self.args(
                    dataset_path,
                    cookie_path,
                    evidence_dir,
                    base_url,
                    acknowledgment=self.frozen["dataset_sha256"],
                )
            )

            self.assertEqual(status, 2)
            self.assertEqual(
                sum(event[0] == "search" for event in FrozenHandler.events), 3
            )
            self.assertFalse((evidence_dir / "exact-observations.json").exists())

    def test_absolute_source_url_must_match_explicit_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory, frozen_server(
            self.sources,
            source_origin="https://evidence.syncbase.example",
            bad_source_origin_at=3,
        ) as base_url:
            root = Path(directory)
            dataset_path = self.write_dataset(root)
            cookie_path = root / "cookie.jar"
            evidence_dir = root / "evidence"
            write_cookie_jar(cookie_path)

            status, stdout, stderr = self.run_collector(
                self.args(
                    dataset_path,
                    cookie_path,
                    evidence_dir,
                    base_url,
                    acknowledgment=self.frozen["dataset_sha256"],
                    expected_source_origin="https://evidence.syncbase.example",
                )
            )

            self.assertEqual(status, 2)
            self.assertEqual(
                sum(event[0] == "search" for event in FrozenHandler.events), 3
            )
            self.assertFalse((evidence_dir / "exact-observations.json").exists())
            terminal = json.loads(
                (evidence_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(terminal["status"], "INCOMPLETE")
            self.assertEqual(terminal["completed_query_count"], 2)
            self.assertNotIn("evil.invalid", stdout + stderr)
            terminal = json.loads(
                (evidence_dir / "collection-status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(terminal["status"], "INCOMPLETE")
            self.assertIn("SEARCH_CONTRACT_OR_MAPPING_ERROR", terminal["error_codes"])


if __name__ == "__main__":
    unittest.main()

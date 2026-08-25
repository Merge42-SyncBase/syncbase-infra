#!/usr/bin/env python3
"""Collect one release-bound exact run from a frozen prospective holdout.

The collector is deliberately one-shot.  It validates and copies the entire active
PDF corpus before writing a query-exposure marker.  Once that marker exists, the
evidence directory can never be reused, including after an incomplete run.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import importlib.util
import io
import json
import math
import os
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPCookieProcessor,
    Request,
    build_opener,
)

from pypdf import PdfReader


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
RETRIEVAL_LIMIT = 5
GROUNDING_STATUSES = {"SUPPORTED", "INSUFFICIENT_EVIDENCE"}
GROUNDING_REASONS = {
    "NO_HITS_ABOVE_POLICY",
    "ONLY_INACTIVE_VERSION_MATCHED",
    "SOURCE_UNAVAILABLE",
}
EXPOSURE_FILE = "query-exposure.json"
STATUS_FILE = "collection-status.json"
PROGRESS_FILE = "partial-progress.json"
PREFLIGHT_FILE = "preflight.json"
OBSERVATIONS_FILE = "exact-observations.json"
CORPUS_MANIFEST_FILE = "corpus-manifest.json"
BINDING_FORMULAS_FILE = "binding-formulas.json"
RETRIEVAL_ARTIFACTS_FILE = "retrieval-artifacts.json"
CANONICALIZATION = "JSON_UTF8_SORT_KEYS_COMPACT_V1"
DATABASE_IDENTITY_KEYS = {
    "schema_version",
    "binding_kind",
    "environment_id_sha256",
    "database_name_sha256",
    "migration_head_sha256",
}
REPOSITORY_IDS = {"frontend", "embedding", "was", "infra", "mcp"}
PROFILE_KEYS = {
    "chunk_overlap_tokens",
    "chunk_size_tokens",
    "chunker_id",
    "distance",
    "embedding_model_id",
    "embedding_model_sha256",
    "minimum_score",
    "onnx_runtime_id",
    "parser_id",
    "provider",
    "tokenizer_sha256",
    "vector_dimension",
}


class CollectionError(ValueError):
    """A safely reportable failure that never contains credentials or responses."""

    def __init__(self, message: str, *, code: str = "COLLECTION_ERROR"):
        super().__init__(message)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        _request: Request,
        _file_pointer: Any,
        _code: int,
        _message: str,
        _headers: Any,
        _new_url: str,
    ) -> None:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def is_git_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def canonical_sha256(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256_bytes(serialized)


def open_regular_file(path: Path, *, label: str) -> tuple[int, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as error:
        raise CollectionError(
            f"{label} must be an available regular file",
            code="RETRIEVAL_ARTIFACT_UNAVAILABLE",
        ) from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise CollectionError(
            f"{label} must be a non-symlink regular file",
            code="RETRIEVAL_ARTIFACT_UNSAFE",
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CollectionError(
            f"{label} must be an available regular file",
            code="RETRIEVAL_ARTIFACT_UNAVAILABLE",
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise CollectionError(
                f"{label} changed while it was opened",
                code="RETRIEVAL_ARTIFACT_UNSAFE",
            )
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def hash_regular_file(path: Path, *, label: str) -> tuple[str, int]:
    descriptor, metadata = open_regular_file(path, label=label)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise CollectionError(
            f"{label} could not be read",
            code="RETRIEVAL_ARTIFACT_UNAVAILABLE",
        ) from error
    if metadata.st_size < 1:
        raise CollectionError(
            f"{label} must not be empty", code="RETRIEVAL_ARTIFACT_INVALID"
        )
    return digest.hexdigest(), metadata.st_size


def read_regular_file(
    path: Path, *, label: str, maximum_bytes: int
) -> bytes:
    descriptor, metadata = open_regular_file(path, label=label)
    if metadata.st_size < 1 or metadata.st_size > maximum_bytes:
        os.close(descriptor)
        raise CollectionError(
            f"{label} has an invalid size", code="RETRIEVAL_ARTIFACT_INVALID"
        )
    try:
        with os.fdopen(descriptor, "rb") as source:
            content = source.read(maximum_bytes + 1)
    except OSError as error:
        raise CollectionError(
            f"{label} could not be read",
            code="RETRIEVAL_ARTIFACT_UNAVAILABLE",
        ) from error
    if len(content) != metadata.st_size:
        raise CollectionError(
            f"{label} changed while it was read",
            code="RETRIEVAL_ARTIFACT_UNSAFE",
        )
    return content


def canonical_profile_bytes(
    profile: dict[str, Any], model_sha256: str, tokenizer_sha256: str
) -> bytes:
    minimum_score = profile.get("minimum_score")
    if (
        set(profile) != PROFILE_KEYS
        or profile.get("chunk_overlap_tokens") != 64
        or profile.get("chunk_size_tokens") != 384
        or profile.get("chunker_id") != "page-aware-recursive-v1"
        or profile.get("distance") != "cosine"
        or profile.get("embedding_model_id") != "intfloat/multilingual-e5-small"
        or profile.get("embedding_model_sha256") != model_sha256
        or not isinstance(minimum_score, (int, float))
        or isinstance(minimum_score, bool)
        or not math.isfinite(minimum_score)
        or not 0.0 <= minimum_score <= 1.0
        or profile.get("onnx_runtime_id") != "onnxruntime-1.26.0"
        or profile.get("parser_id") != "pdfium-wasm-1.19.6"
        or profile.get("provider") != "local-onnx"
        or profile.get("tokenizer_sha256") != tokenizer_sha256
        or profile.get("vector_dimension") != 384
    ):
        raise CollectionError(
            "profile artifact does not match the immutable retrieval profile schema",
            code="PROFILE_ARTIFACT_INVALID",
        )
    return (
        '{"chunk_overlap_tokens":64,"chunk_size_tokens":384,'
        '"chunker_id":"page-aware-recursive-v1","distance":"cosine",'
        '"embedding_model_id":"intfloat/multilingual-e5-small",'
        f'"embedding_model_sha256":"{model_sha256}",'
        f'"minimum_score":{minimum_score:.6f},'
        '"onnx_runtime_id":"onnxruntime-1.26.0",'
        '"parser_id":"pdfium-wasm-1.19.6","provider":"local-onnx",'
        f'"tokenizer_sha256":"{tokenizer_sha256}",'
        '"vector_dimension":384}'
    ).encode("utf-8")


def load_retrieval_artifacts(
    model_path: Path | None,
    tokenizer_path: Path | None,
    profile_path: Path | None,
) -> dict[str, dict[str, Any]]:
    if model_path is None or tokenizer_path is None or profile_path is None:
        raise CollectionError(
            "model, tokenizer, and profile artifacts are all required",
            code="RETRIEVAL_ARTIFACT_REQUIRED",
        )
    model_digest, model_size = hash_regular_file(model_path, label="model artifact")
    tokenizer_digest, tokenizer_size = hash_regular_file(
        tokenizer_path, label="tokenizer artifact"
    )
    profile_content = read_regular_file(
        profile_path, label="profile artifact", maximum_bytes=1024 * 1024
    )
    try:
        profile = json.loads(profile_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CollectionError(
            "profile artifact must be exact canonical JSON",
            code="PROFILE_ARTIFACT_INVALID",
        ) from error
    if not isinstance(profile, dict) or profile_content != canonical_profile_bytes(
        profile, model_digest, tokenizer_digest
    ):
        raise CollectionError(
            "profile artifact must be exact canonical retrieval-profile bytes",
            code="PROFILE_ARTIFACT_INVALID",
        )
    return {
        "model": {
            "source": model_path,
            "sha256": model_digest,
            "byte_count": model_size,
            "artifact": f"runtime-artifacts/model-{model_digest}.onnx",
        },
        "tokenizer": {
            "source": tokenizer_path,
            "sha256": tokenizer_digest,
            "byte_count": tokenizer_size,
            "artifact": f"runtime-artifacts/tokenizer-{tokenizer_digest}.json",
        },
        "profile": {
            "source": profile_path,
            "sha256": sha256_bytes(profile_content),
            "byte_count": len(profile_content),
            "artifact": f"runtime-artifacts/profile-{sha256_bytes(profile_content)}.json",
        },
    }


def load_evaluator() -> Any:
    path = Path(__file__).with_name("evaluate_retrieval.py")
    spec = importlib.util.spec_from_file_location(
        "round1_frozen_retrieval_evaluator", path
    )
    if spec is None or spec.loader is None:
        raise CollectionError(
            "frozen dataset validator is unavailable", code="VALIDATOR_UNAVAILABLE"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise CollectionError(
            "base URL must be an uncredentialed loopback HTTP origin",
            code="INVALID_BASE_URL",
        )
    try:
        _ = parsed.port
    except ValueError as error:
        raise CollectionError(
            "base URL port is invalid", code="INVALID_BASE_URL"
        ) from error
    return value.rstrip("/")


def normalize_expected_source_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise CollectionError(
            "expected source origin is malformed",
            code="INVALID_SOURCE_ORIGIN",
        ) from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path
        or parsed.netloc.endswith(":")
        or any(character.isspace() for character in value)
    ):
        raise CollectionError(
            "expected source origin must be an uncredentialed HTTP(S) origin",
            code="INVALID_SOURCE_ORIGIN",
        )
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if parsed.scheme == "http" else 443
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    normalized = f"{parsed.scheme}://{host}{port_suffix}"
    if value != normalized:
        raise CollectionError(
            "expected source origin must use canonical origin syntax",
            code="INVALID_SOURCE_ORIGIN",
        )
    return normalized


def validate_cookie_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CollectionError(
            "session cookie file must be a regular 0600 file",
            code="COOKIE_FILE_UNAVAILABLE",
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.getuid()
    ):
        raise CollectionError(
            "session cookie file must be a regular 0600 file owned by this user",
            code="COOKIE_FILE_NOT_PROTECTED",
        )


def contains_sensitive_value(value: object, sensitive_values: set[str]) -> bool:
    if isinstance(value, str):
        return any(secret and secret in value for secret in sensitive_values)
    if isinstance(value, list):
        return any(contains_sensitive_value(item, sensitive_values) for item in value)
    if isinstance(value, dict):
        return any(contains_sensitive_value(item, sensitive_values) for item in value.values())
    return False


class LocalRestClient:
    def __init__(self, base_url: str, cookie_file: Path, timeout_seconds: float):
        self.base_url = normalize_local_base_url(base_url)
        jar = http.cookiejar.MozillaCookieJar()
        try:
            jar.load(str(cookie_file), ignore_discard=True, ignore_expires=True)
        except (OSError, http.cookiejar.LoadError) as error:
            raise CollectionError(
                "session cookie jar is unavailable or invalid",
                code="COOKIE_JAR_INVALID",
            ) from error
        session_cookies = [cookie for cookie in jar if cookie.name == "syncbase_session"]
        if len(session_cookies) != 1 or not session_cookies[0].value:
            raise CollectionError(
                "session cookie jar must contain exactly one SyncBase session",
                code="COOKIE_SESSION_INVALID",
            )
        self.sensitive_values = {
            cookie.value for cookie in jar if isinstance(cookie.value, str) and cookie.value
        }
        self.opener = build_opener(HTTPCookieProcessor(jar), NoRedirect())
        self.timeout_seconds = timeout_seconds

    def _get(self, endpoint: str, *, label: str, maximum_bytes: int) -> bytes:
        request = Request(
            self.base_url + endpoint,
            headers={
                "Accept": "application/json, application/pdf",
                "User-Agent": "syncbase-round1-frozen-one-shot/1.0",
            },
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                content = response.read(maximum_bytes + 1)
                if len(content) > maximum_bytes:
                    raise CollectionError(
                        f"{label} response exceeded the size limit",
                        code="RESPONSE_SIZE_LIMIT",
                    )
                return content
        except HTTPError as error:
            code = error.code
            error.close()
            raise CollectionError(
                f"{label} failed with HTTP {code}", code="HTTP_REQUEST_FAILED"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise CollectionError(
                f"{label} request failed", code="HTTP_REQUEST_FAILED"
            ) from error

    def json(self, endpoint: str, *, label: str) -> dict[str, Any]:
        content = self._get(endpoint, label=label, maximum_bytes=MAX_JSON_BYTES)
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CollectionError(
                f"{label} returned invalid JSON", code="INVALID_JSON_RESPONSE"
            ) from error
        if not isinstance(value, dict):
            raise CollectionError(
                f"{label} returned a non-object JSON value",
                code="INVALID_JSON_RESPONSE",
            )
        if contains_sensitive_value(value, self.sensitive_values):
            raise CollectionError(
                f"{label} response contained credential material",
                code="RESPONSE_CONTAINED_CREDENTIAL",
            )
        return value

    def pdf(self, endpoint: str, *, label: str) -> bytes:
        content = self._get(endpoint, label=label, maximum_bytes=MAX_PDF_BYTES)
        if any(secret.encode("utf-8") in content for secret in self.sensitive_values):
            raise CollectionError(
                f"{label} response contained credential material",
                code="RESPONSE_CONTAINED_CREDENTIAL",
            )
        return content


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def serialize_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def atomic_write_bytes(
    path: Path, content: bytes, *, replace: bool, mode: int = 0o644
) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        if replace:
            os.replace(temporary_name, path)
            temporary_name = None
        else:
            try:
                os.link(temporary_name, path)
            except FileExistsError as error:
                raise CollectionError(
                    f"refusing to overwrite evidence artifact {path.name}",
                    code="ARTIFACT_ALREADY_EXISTS",
                ) from error
            Path(temporary_name).unlink()
            temporary_name = None
        fsync_directory(path.parent)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object, *, replace: bool) -> None:
    atomic_write_bytes(path, serialize_json(value), replace=replace)


def copy_verified_artifact(
    source_path: Path,
    destination: Path,
    *,
    label: str,
    expected_sha256: str,
    expected_size: int,
) -> None:
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, _metadata = open_regular_file(source_path, label=label)
    temporary_name: str | None = None
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with os.fdopen(descriptor, "rb") as source, tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                byte_count += len(chunk)
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        if digest.hexdigest() != expected_sha256 or byte_count != expected_size:
            raise CollectionError(
                f"{label} changed before it could be sealed",
                code="RETRIEVAL_ARTIFACT_CHANGED",
            )
        os.chmod(temporary_name, 0o644)
        try:
            os.link(temporary_name, destination)
        except FileExistsError as error:
            raise CollectionError(
                f"refusing to overwrite sealed {label}",
                code="ARTIFACT_ALREADY_EXISTS",
            ) from error
        Path(temporary_name).unlink()
        temporary_name = None
        fsync_directory(destination.parent)
    except OSError as error:
        raise CollectionError(
            f"{label} could not be sealed",
            code="RETRIEVAL_ARTIFACT_UNAVAILABLE",
        ) from error
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def retrieval_binding_values(
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, str]:
    return {
        "model_sha256": artifacts["model"]["sha256"],
        "tokenizer_sha256": artifacts["tokenizer"]["sha256"],
        "profile_sha256": artifacts["profile"]["sha256"],
    }


def seal_retrieval_artifacts(
    evidence_dir: Path, artifacts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    for artifact_id in ("model", "tokenizer", "profile"):
        source = artifacts[artifact_id]
        copy_verified_artifact(
            source["source"],
            evidence_dir / source["artifact"],
            label=f"{artifact_id} artifact",
            expected_sha256=source["sha256"],
            expected_size=source["byte_count"],
        )
        records[artifact_id] = {
            "artifact": source["artifact"],
            "sha256": source["sha256"],
            "byte_count": source["byte_count"],
        }
    value = {
        "schema_version": "1.0",
        "artifact_kind": "SYNCBASE_RETRIEVAL_ARTIFACTS_V1",
        "bindings": retrieval_binding_values(artifacts),
        "artifacts": records,
        "profile_contract": "SYNCBASE_RETRIEVAL_PROFILE_CANONICAL_V1",
        "benchmark_result": "NOT_EVALUATED",
        "claim_eligible": False,
    }
    atomic_write_json(
        evidence_dir / RETRIEVAL_ARTIFACTS_FILE, value, replace=False
    )
    return value


def prepare_evidence_directory(path: Path) -> Path:
    exposure_path = path / EXPOSURE_FILE
    if exposure_path.exists() or exposure_path.is_symlink():
        raise CollectionError(
            "evidence directory contains an exposure marker and cannot be rerun",
            code="EXPOSURE_ALREADY_STARTED",
        )
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise CollectionError(
                "evidence directory must be a new or empty real directory",
                code="INVALID_EVIDENCE_DIRECTORY",
            )
        try:
            is_empty = next(path.iterdir(), None) is None
        except OSError as error:
            raise CollectionError(
                "evidence directory is unavailable",
                code="INVALID_EVIDENCE_DIRECTORY",
            ) from error
        if not is_empty:
            raise CollectionError(
                "evidence directory must be new or empty; no overwrite is allowed",
                code="EVIDENCE_DIRECTORY_NOT_EMPTY",
            )
    else:
        try:
            path.mkdir(mode=0o755, parents=False)
        except OSError as error:
            raise CollectionError(
                "evidence directory could not be created",
                code="INVALID_EVIDENCE_DIRECTORY",
            ) from error
    return path.resolve()


def load_dataset(
    path: Path, evaluator: Any, *, mode: str
) -> tuple[dict[str, Any], bytes]:
    try:
        content = path.read_bytes()
        dataset = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CollectionError(
            "frozen dataset is unavailable or invalid JSON",
            code="INVALID_FROZEN_DATASET",
        ) from error
    require_frozen = mode == "collect"
    errors = evaluator.validate_dataset(
        dataset,
        require_frozen=require_frozen,
        allow_pending=not require_frozen,
    )
    if errors:
        raise CollectionError(
            ("frozen" if require_frozen else "draft")
            + " dataset validation failed: "
            + "; ".join(errors),
            code="INVALID_FROZEN_DATASET",
        )
    if dataset.get("dataset_role") != "PROSPECTIVE_HOLDOUT":
        raise CollectionError(
            "frozen collector requires dataset_role=PROSPECTIVE_HOLDOUT",
            code="INVALID_FROZEN_DATASET",
        )
    if dataset.get("benchmark_claim") != "NOT_RUN":
        raise CollectionError(
            "frozen benchmark_claim must be NOT_RUN",
            code="INVALID_FROZEN_DATASET",
        )
    if require_frozen and dataset.get("query_exposure") != "NOT_QUERIED_BEFORE_FREEZE":
        raise CollectionError(
            "frozen query exposure must record NOT_QUERIED_BEFORE_FREEZE",
            code="INVALID_FROZEN_DATASET",
        )
    if not require_frozen and dataset.get("status") != "DRAFT":
        raise CollectionError(
            "preflight mode requires a DRAFT prospective holdout",
            code="INVALID_DRAFT_DATASET",
        )
    return dataset, content


def load_database_identity_payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise CollectionError(
            "database identity JSON is required",
            code="DATABASE_IDENTITY_REQUIRED",
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CollectionError(
            "database identity JSON is unavailable or invalid",
            code="DATABASE_IDENTITY_INVALID",
        ) from error
    if not isinstance(value, dict) or set(value) != DATABASE_IDENTITY_KEYS:
        raise CollectionError(
            "database identity JSON does not match SYNCBASE_DATABASE_IDENTITY_V1",
            code="DATABASE_IDENTITY_INVALID",
        )
    if (
        value.get("schema_version") != "1.0"
        or value.get("binding_kind") != "SYNCBASE_DATABASE_IDENTITY_V1"
        or any(
            not is_sha256(value.get(name))
            for name in (
                "environment_id_sha256",
                "database_name_sha256",
                "migration_head_sha256",
            )
        )
    ):
        raise CollectionError(
            "database identity JSON does not match SYNCBASE_DATABASE_IDENTITY_V1",
            code="DATABASE_IDENTITY_INVALID",
        )
    return value


def source_release_payload(dataset: dict[str, Any]) -> dict[str, Any]:
    bindings = dataset.get("bindings")
    revisions = (
        bindings.get("repository_revisions")
        if isinstance(bindings, dict)
        else None
    )
    if (
        not isinstance(revisions, dict)
        or set(revisions) != REPOSITORY_IDS
        or any(not is_git_sha(revisions.get(name)) for name in REPOSITORY_IDS)
    ):
        raise CollectionError(
            "five full repository revisions are required for source release binding",
            code="SOURCE_RELEASE_INPUT_INVALID",
        )
    return {
        "schema_version": "1.0",
        "binding_kind": "SYNCBASE_SOURCE_RELEASE_V1",
        "repository_revisions": revisions,
    }


def corpus_manifest_payload(bindings: list[dict[str, Any]]) -> dict[str, Any]:
    sources = sorted(
        bindings,
        key=lambda binding: (
            binding["source_sha256"],
            binding["version"],
            binding["document_id"],
            binding["version_id"],
        ),
    )
    return {
        "schema_version": "1.0",
        "binding_kind": "SYNCBASE_ACTIVE_CORPUS_V1",
        "sources": sources,
    }


def corpus_manifest(bindings: list[dict[str, Any]]) -> dict[str, Any]:
    payload = corpus_manifest_payload(bindings)
    return {
        **payload,
        "canonicalization": CANONICALIZATION,
        "corpus_sha256": canonical_sha256(payload),
    }


def expected_source_versions(dataset: dict[str, Any]) -> set[tuple[str, int]]:
    return {
        (target["source_sha256"], target["version"])
        for query in dataset["queries"]
        for target in query["expected"]["relevant"]
    }


def pdf_page_count(content: bytes) -> int:
    try:
        count = len(PdfReader(io.BytesIO(content), strict=True).pages)
    except Exception as error:
        raise CollectionError(
            "active Original is not a readable PDF",
            code="INVALID_ACTIVE_PDF",
        ) from error
    if count < 1:
        raise CollectionError(
            "active Original has no pages", code="INVALID_ACTIVE_PDF"
        )
    return count


def list_document_summaries(client: LocalRestClient) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while True:
        response = client.json(
            f"/api/v1/documents?{urlencode({'limit': limit, 'offset': offset})}",
            label="document listing",
        )
        documents = response.get("documents")
        if not isinstance(documents, list) or any(
            not isinstance(document, dict) for document in documents
        ):
            raise CollectionError(
                "document listing contract is invalid",
                code="DOCUMENT_LISTING_CONTRACT_ERROR",
            )
        summaries.extend(documents)
        if len(documents) < limit:
            break
        offset += len(documents)
        if offset > 10_000:
            raise CollectionError(
                "active corpus exceeded the collection safety limit",
                code="ACTIVE_CORPUS_LIMIT",
            )
    return summaries


def safe_error(code: str, *, document_id: str | None = None) -> dict[str, str]:
    error = {"code": code}
    if isinstance(document_id, str) and document_id:
        error["document_id"] = document_id
    return error


def preflight_active_corpus(
    client: LocalRestClient,
    dataset: dict[str, Any],
    evidence_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    summaries = list_document_summaries(client)
    bindings: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    active_summaries = [
        summary
        for summary in summaries
        if isinstance(summary.get("activeVersion"), int)
        and not isinstance(summary.get("activeVersion"), bool)
    ]
    seen_documents: set[str] = set()
    seen_version_ids: set[str] = set()

    for summary in active_summaries:
        document_id = summary.get("id")
        active_version = summary.get("activeVersion")
        if not isinstance(document_id, str) or not document_id:
            errors.append(safe_error("INVALID_DOCUMENT_ID"))
            continue
        if document_id in seen_documents:
            errors.append(safe_error("DUPLICATE_DOCUMENT_ID", document_id=document_id))
            continue
        seen_documents.add(document_id)
        if (
            not isinstance(active_version, int)
            or isinstance(active_version, bool)
            or active_version < 1
        ):
            errors.append(safe_error("INVALID_ACTIVE_VERSION", document_id=document_id))
            continue

        encoded_id = quote(document_id, safe="")
        detail: dict[str, Any] | None = None
        try:
            detail = client.json(
                f"/api/v1/documents/{encoded_id}", label="document detail"
            )
        except CollectionError as error:
            errors.append(safe_error(error.code, document_id=document_id))

        version_record: dict[str, Any] | None = None
        if isinstance(detail, dict):
            versions = detail.get("versions")
            if not isinstance(versions, list):
                errors.append(safe_error("VERSIONS_NOT_ARRAY", document_id=document_id))
            else:
                matches = [
                    version
                    for version in versions
                    if isinstance(version, dict)
                    and version.get("versionNumber") == active_version
                    and version.get("active") is True
                    and version.get("status") == "ACTIVE"
                ]
                if len(matches) == 1:
                    version_record = matches[0]
                else:
                    errors.append(
                        safe_error("ACTIVE_VERSION_NOT_UNIQUE", document_id=document_id)
                    )

        content: bytes | None = None
        try:
            content = client.pdf(
                f"/api/v1/documents/{encoded_id}/versions/{active_version}/raw.pdf",
                label="active Original",
            )
        except CollectionError as error:
            errors.append(safe_error(error.code, document_id=document_id))
        if content is None:
            continue

        digest = sha256_bytes(content)
        try:
            actual_page_count = pdf_page_count(content)
        except CollectionError as error:
            errors.append(safe_error(error.code, document_id=document_id))
            continue
        artifact = f"sources/{digest}-v{active_version}.pdf"
        try:
            atomic_write_bytes(
                evidence_dir / artifact, content, replace=False, mode=0o644
            )
        except CollectionError as error:
            errors.append(safe_error(error.code, document_id=document_id))
            continue

        if version_record is None:
            continue
        version_id = version_record.get("id")
        declared_page_count = version_record.get("pageCount")
        if not isinstance(version_id, str) or not version_id:
            errors.append(safe_error("INVALID_VERSION_ID", document_id=document_id))
            continue
        if version_id in seen_version_ids:
            errors.append(safe_error("DUPLICATE_VERSION_ID", document_id=document_id))
            continue
        seen_version_ids.add(version_id)
        if (
            not isinstance(declared_page_count, int)
            or isinstance(declared_page_count, bool)
            or declared_page_count != actual_page_count
        ):
            errors.append(
                safe_error("PDF_PAGE_COUNT_MISMATCH", document_id=document_id)
            )
            continue
        bindings.append(
            {
                "document_id": document_id,
                "version_id": version_id,
                "source_sha256": digest,
                "version": active_version,
                "active": True,
                "page_count": actual_page_count,
                "raw_pdf_artifact": artifact,
                "raw_pdf_sha256": digest,
            }
        )

    expected = expected_source_versions(dataset)
    observed = {
        (binding["source_sha256"], binding["version"]) for binding in bindings
    }
    if expected - observed:
        errors.append(safe_error("EXPECTED_SOURCE_VERSION_MISSING"))
    if observed - expected:
        errors.append(safe_error("UNEXPECTED_ACTIVE_SOURCE_VERSION"))
    if len(bindings) != len(observed):
        errors.append(safe_error("DUPLICATE_ACTIVE_SOURCE_VERSION"))
    if len(active_summaries) != len(expected):
        errors.append(safe_error("ACTIVE_CORPUS_COUNT_MISMATCH"))
    bindings.sort(
        key=lambda binding: (
            binding["source_sha256"],
            binding["document_id"],
            binding["version"],
        )
    )
    return bindings, errors


def base_status(
    dataset: dict[str, Any], dataset_file_sha256: str, source_origin: str
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "artifact_kind": "FROZEN_EXACT_COLLECTION_STATUS",
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": dataset.get("dataset_sha256"),
        "dataset_file_sha256": dataset_file_sha256,
        "retrieval_mode": "exact",
        "retrieval_limit": RETRIEVAL_LIMIT,
        "source_origin": source_origin,
        "benchmark_result": "NOT_EVALUATED",
        "claim_eligible": False,
        "release_claim_eligible": False,
    }


def write_terminal_status(
    evidence_dir: Path,
    base: dict[str, Any],
    *,
    status: str,
    stage: str,
    completed_query_count: int,
    error_codes: list[str],
) -> None:
    value = {
        **base,
        "status": status,
        "stage": stage,
        "completed_query_count": completed_query_count,
        "expected_query_count": 30,
        "error_codes": sorted(set(error_codes)),
        "updated_at": utc_now(),
    }
    atomic_write_json(evidence_dir / STATUS_FILE, value, replace=True)


def normalize_search_response(
    response: dict[str, Any],
    query: dict[str, Any],
    source_lookup: dict[tuple[str, int], dict[str, Any]],
    evaluator: Any,
    source_origin: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if response.get("query") != query["query"]:
        errors.append("QUERY_ECHO_MISMATCH")
    status = response.get("grounding_status")
    reason = response.get("grounding_reason")
    raw_results = response.get("results")
    if status not in GROUNDING_STATUSES:
        errors.append("INVALID_GROUNDING_STATUS")
    if not isinstance(raw_results, list):
        errors.append("RESULTS_NOT_ARRAY")
        raw_results = []
    if len(raw_results) > RETRIEVAL_LIMIT:
        errors.append("RESULT_LIMIT_EXCEEDED")
    if status == "SUPPORTED" and (not raw_results or reason is not None):
        errors.append("SUPPORTED_CONTRACT_VIOLATION")
    if status == "INSUFFICIENT_EVIDENCE" and (
        raw_results or reason not in GROUNDING_REASONS
    ):
        errors.append("INSUFFICIENT_EVIDENCE_CONTRACT_VIOLATION")

    results: list[dict[str, Any]] = []
    for expected_rank, raw_hit in enumerate(raw_results, start=1):
        if not isinstance(raw_hit, dict):
            errors.append(f"HIT_{expected_rank}_NOT_OBJECT")
            continue
        document_id = raw_hit.get("document_id")
        version = raw_hit.get("document_version")
        version_id = raw_hit.get("version_id")
        binding = (
            source_lookup.get((document_id, version))
            if isinstance(document_id, str)
            and isinstance(version, int)
            and not isinstance(version, bool)
            else None
        )
        rank = raw_hit.get("rank")
        score = raw_hit.get("score")
        page = raw_hit.get("page_number")
        snippet = raw_hit.get("snippet")
        source_url = raw_hit.get("source_url")
        if rank != expected_rank or isinstance(rank, bool):
            errors.append(f"HIT_{expected_rank}_INVALID_RANK")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0.0 <= score <= 1.0
        ):
            errors.append(f"HIT_{expected_rank}_INVALID_SCORE")
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or page < 1
            or (binding is not None and page > binding["page_count"])
        ):
            errors.append(f"HIT_{expected_rank}_INVALID_PAGE")
        if not isinstance(snippet, str) or not snippet.strip():
            errors.append(f"HIT_{expected_rank}_INVALID_SNIPPET")
        if binding is None:
            errors.append(f"HIT_{expected_rank}_SOURCE_UNMAPPED")
            source_sha256 = None
        elif binding["version_id"] != version_id:
            errors.append(f"HIT_{expected_rank}_VERSION_ID_MISMATCH")
            source_sha256 = binding["source_sha256"]
        else:
            source_sha256 = binding["source_sha256"]
        hit = {
            "rank": rank,
            "document_id": document_id,
            "version_id": version_id,
            "source_sha256": source_sha256,
            "version": version,
            "page": page,
            "score": score,
            "snippet": snippet,
            "source_url": source_url,
        }
        if not evaluator.citation_source_url_matches(hit, source_origin):
            errors.append(f"HIT_{expected_rank}_SOURCE_URL_TUPLE_MISMATCH")
        results.append(hit)

    if errors:
        raise CollectionError(
            "search response contract or source mapping failed",
            code="SEARCH_CONTRACT_OR_MAPPING_ERROR",
        )
    return {
        "id": query["id"],
        "latency_ms": 0.0,
        "grounding_status": status,
        "grounding_reason": reason,
        "results": results,
    }


def progress_artifact(
    base: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    status: str,
    next_query_id: str | None,
    failed_query_id: str | None = None,
    error_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **base,
        "artifact_kind": "FROZEN_EXACT_PARTIAL_PROGRESS",
        "status": status,
        "completed_query_count": len(observations),
        "expected_query_count": 30,
        "next_query_id": next_query_id,
        "failed_query_id": failed_query_id,
        "error_codes": sorted(set(error_codes or [])),
        "queries": observations,
        "updated_at": utc_now(),
    }


def collect_searches(
    client: LocalRestClient,
    dataset: dict[str, Any],
    bindings: list[dict[str, Any]],
    evidence_dir: Path,
    evaluator: Any,
    base: dict[str, Any],
    source_origin: str,
) -> tuple[list[dict[str, Any]] | None, CollectionError | None]:
    source_lookup = {
        (binding["document_id"], binding["version"]): binding
        for binding in bindings
    }
    observations: list[dict[str, Any]] = []
    queries = dataset["queries"]
    atomic_write_json(
        evidence_dir / PROGRESS_FILE,
        progress_artifact(
            base,
            observations,
            status="RUNNING",
            next_query_id=queries[0]["id"],
        ),
        replace=True,
    )
    for position, query in enumerate(queries):
        next_id = query["id"]
        atomic_write_json(
            evidence_dir / PROGRESS_FILE,
            progress_artifact(
                base,
                observations,
                status="RUNNING",
                next_query_id=next_id,
            ),
            replace=True,
        )
        try:
            started = time.perf_counter_ns()
            response = client.json(
                f"/api/v1/search?{urlencode({'q': query['query'], 'limit': RETRIEVAL_LIMIT})}",
                label=f"search request {position + 1}",
            )
            observation = normalize_search_response(
                response, query, source_lookup, evaluator, source_origin
            )
            observation["latency_ms"] = round(
                (time.perf_counter_ns() - started) / 1_000_000, 3
            )
            observations.append(observation)
            following_id = (
                queries[position + 1]["id"] if position + 1 < len(queries) else None
            )
            atomic_write_json(
                evidence_dir / PROGRESS_FILE,
                progress_artifact(
                    base,
                    observations,
                    status="RUNNING",
                    next_query_id=following_id,
                ),
                replace=True,
            )
        except CollectionError as error:
            atomic_write_json(
                evidence_dir / PROGRESS_FILE,
                progress_artifact(
                    base,
                    observations,
                    status="INCOMPLETE",
                    next_query_id=None,
                    failed_query_id=query["id"],
                    error_codes=[error.code],
                ),
                replace=True,
            )
            return None, error
        except (Exception, KeyboardInterrupt):
            error = CollectionError(
                "search collection stopped unexpectedly",
                code="UNEXPECTED_SEARCH_FAILURE",
            )
            atomic_write_json(
                evidence_dir / PROGRESS_FILE,
                progress_artifact(
                    base,
                    observations,
                    status="INCOMPLETE",
                    next_query_id=None,
                    failed_query_id=query["id"],
                    error_codes=[error.code],
                ),
                replace=True,
            )
            return None, error
    return observations, None


def binding_formula_artifact(
    dataset: dict[str, Any],
    manifest: dict[str, Any],
    database_identity: dict[str, Any],
    retrieval_artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    release_payload = source_release_payload(dataset)
    retrieval_bindings = retrieval_binding_values(retrieval_artifacts)
    return {
        "schema_version": "1.0",
        "artifact_kind": "ROUND1_BINDING_FORMULAS",
        "canonicalization": CANONICALIZATION,
        "formulas": {
            "corpus_sha256": (
                "SHA256(canonical JSON of SYNCBASE_ACTIVE_CORPUS_V1 payload)"
            ),
            "database_identity_sha256": (
                "SHA256(canonical JSON of SYNCBASE_DATABASE_IDENTITY_V1 payload)"
            ),
            "source_release_sha256": (
                "SHA256(canonical JSON of SYNCBASE_SOURCE_RELEASE_V1 payload)"
            ),
            "model_sha256": "SHA256(exact sealed model artifact bytes)",
            "tokenizer_sha256": "SHA256(exact sealed tokenizer artifact bytes)",
            "profile_sha256": (
                "SHA256(exact SYNCBASE_RETRIEVAL_PROFILE_CANONICAL_V1 bytes)"
            ),
        },
        "database_identity_payload": database_identity,
        "source_release_payload": release_payload,
        "recommended_bindings": {
            "corpus_sha256": manifest["corpus_sha256"],
            "database_identity_sha256": canonical_sha256(database_identity),
            "source_release_sha256": canonical_sha256(release_payload),
            **retrieval_bindings,
        },
        "benchmark_result": "NOT_EVALUATED",
        "claim_eligible": False,
    }


def binding_mismatch_codes(
    dataset: dict[str, Any], formula: dict[str, Any], *, allow_null: bool
) -> list[str]:
    bindings = dataset.get("bindings")
    if not isinstance(bindings, dict):
        return ["BINDINGS_INVALID"]
    codes: list[str] = []
    mapping = {
        "corpus_sha256": "CORPUS_BINDING_MISMATCH",
        "database_identity_sha256": "DATABASE_IDENTITY_BINDING_MISMATCH",
        "source_release_sha256": "SOURCE_RELEASE_BINDING_MISMATCH",
        "model_sha256": "MODEL_BINDING_MISMATCH",
        "tokenizer_sha256": "TOKENIZER_BINDING_MISMATCH",
        "profile_sha256": "PROFILE_BINDING_MISMATCH",
    }
    recommended = formula["recommended_bindings"]
    for name, code in mapping.items():
        observed = bindings.get(name)
        if allow_null and observed is None:
            continue
        if observed != recommended[name]:
            codes.append(code)
    return codes


def write_manifest_and_formulas(
    evidence_dir: Path,
    dataset: dict[str, Any],
    bindings: list[dict[str, Any]],
    database_identity: dict[str, Any],
    retrieval_artifacts: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = corpus_manifest(bindings)
    formulas = binding_formula_artifact(
        dataset, manifest, database_identity, retrieval_artifacts
    )
    atomic_write_json(
        evidence_dir / CORPUS_MANIFEST_FILE, manifest, replace=False
    )
    atomic_write_json(
        evidence_dir / BINDING_FORMULAS_FILE, formulas, replace=False
    )
    return manifest, formulas


def run_preflight_only(
    dataset: dict[str, Any],
    dataset_bytes: bytes,
    database_identity: dict[str, Any],
    retrieval_artifacts: dict[str, dict[str, Any]],
    client: LocalRestClient,
    evidence_dir: Path,
    source_origin: str,
) -> None:
    base = base_status(dataset, sha256_bytes(dataset_bytes), source_origin)
    try:
        bindings, preflight_errors = preflight_active_corpus(
            client, dataset, evidence_dir
        )
    except CollectionError as error:
        write_terminal_status(
            evidence_dir,
            base,
            status="INCOMPLETE",
            stage="PREFLIGHT",
            completed_query_count=0,
            error_codes=[error.code],
        )
        raise CollectionError(
            "active corpus preflight failed; zero search requests were issued",
            code="PREFLIGHT_FAILED",
        ) from error

    manifest, formulas = write_manifest_and_formulas(
        evidence_dir,
        dataset,
        bindings,
        database_identity,
        retrieval_artifacts,
    )
    mismatch_codes = binding_mismatch_codes(
        dataset, formulas, allow_null=True
    )
    preflight_codes = [error["code"] for error in preflight_errors]
    all_codes = preflight_codes + mismatch_codes
    atomic_write_json(
        evidence_dir / PREFLIGHT_FILE,
        {
            **base,
            "artifact_kind": "FROZEN_ACTIVE_CORPUS_PREFLIGHT",
            "status": "COMPLETE" if not all_codes else "INCOMPLETE",
            "active_source_count": len(bindings),
            "expected_source_count": len(expected_source_versions(dataset)),
            "corpus_sha256": manifest["corpus_sha256"],
            "source_bindings": bindings,
            "errors": preflight_errors,
            "error_codes": sorted(set(all_codes)),
            "claim_eligible": False,
            "completed_at": utc_now(),
        },
        replace=False,
    )
    if all_codes:
        write_terminal_status(
            evidence_dir,
            base,
            status="INCOMPLETE",
            stage="PREFLIGHT",
            completed_query_count=0,
            error_codes=all_codes,
        )
        raise CollectionError(
            "active corpus preflight failed; zero search requests were issued",
            code="PREFLIGHT_FAILED",
        )
    write_terminal_status(
        evidence_dir,
        base,
        status="PREFLIGHT_COMPLETE",
        stage="PREFLIGHT",
        completed_query_count=0,
        error_codes=[],
    )


def run_collection(
    dataset: dict[str, Any],
    dataset_bytes: bytes,
    database_identity: dict[str, Any],
    retrieval_artifacts: dict[str, dict[str, Any]],
    evaluator: Any,
    client: LocalRestClient,
    evidence_dir: Path,
    source_origin: str,
) -> None:
    dataset_file_sha256 = sha256_bytes(dataset_bytes)
    base = base_status(dataset, dataset_file_sha256, source_origin)
    try:
        bindings, preflight_errors = preflight_active_corpus(
            client, dataset, evidence_dir
        )
    except CollectionError as error:
        atomic_write_json(
            evidence_dir / PREFLIGHT_FILE,
            {
                **base,
                "artifact_kind": "FROZEN_ACTIVE_CORPUS_PREFLIGHT",
                "status": "INCOMPLETE",
                "error_codes": [error.code],
                "claim_eligible": False,
                "completed_at": utc_now(),
            },
            replace=False,
        )
        write_terminal_status(
            evidence_dir,
            base,
            status="INCOMPLETE",
            stage="PREFLIGHT",
            completed_query_count=0,
            error_codes=[error.code],
        )
        raise CollectionError(
            "active corpus preflight failed; zero search requests were issued",
            code="PREFLIGHT_FAILED",
        ) from error

    manifest, formulas = write_manifest_and_formulas(
        evidence_dir,
        dataset,
        bindings,
        database_identity,
        retrieval_artifacts,
    )
    preflight_codes = [error["code"] for error in preflight_errors]
    preflight_codes.extend(
        binding_mismatch_codes(dataset, formulas, allow_null=False)
    )
    atomic_write_json(
        evidence_dir / PREFLIGHT_FILE,
        {
            **base,
            "artifact_kind": "FROZEN_ACTIVE_CORPUS_PREFLIGHT",
            "status": "COMPLETE" if not preflight_codes else "INCOMPLETE",
            "active_source_count": len(bindings),
            "expected_source_count": len(expected_source_versions(dataset)),
            "corpus_sha256": manifest["corpus_sha256"],
            "source_bindings": bindings,
            "errors": preflight_errors,
            "error_codes": sorted(set(preflight_codes)),
            "claim_eligible": False,
            "completed_at": utc_now(),
        },
        replace=False,
    )
    if preflight_codes:
        write_terminal_status(
            evidence_dir,
            base,
            status="INCOMPLETE",
            stage="PREFLIGHT",
            completed_query_count=0,
            error_codes=preflight_codes,
        )
        raise CollectionError(
            "active corpus preflight failed; zero search requests were issued",
            code="PREFLIGHT_FAILED",
        )

    started_at = utc_now()
    atomic_write_json(
        evidence_dir / EXPOSURE_FILE,
        {
            **base,
            "artifact_kind": "HOLDOUT_QUERY_EXPOSURE_MARKER",
            "status": "QUERY_EXPOSURE_STARTED",
            "one_shot": True,
            "started_at": started_at,
            "claim_eligible": False,
        },
        replace=False,
    )
    write_terminal_status(
        evidence_dir,
        base,
        status="RUNNING",
        stage="SEARCH",
        completed_query_count=0,
        error_codes=[],
    )
    observations, search_error = collect_searches(
        client, dataset, bindings, evidence_dir, evaluator, base, source_origin
    )
    if search_error is not None:
        progress = json.loads((evidence_dir / PROGRESS_FILE).read_text(encoding="utf-8"))
        write_terminal_status(
            evidence_dir,
            base,
            status="INCOMPLETE",
            stage="SEARCH",
            completed_query_count=progress["completed_query_count"],
            error_codes=[search_error.code],
        )
        raise search_error
    if observations is None:
        raise CollectionError(
            "search collection did not produce observations",
            code="UNEXPECTED_SEARCH_FAILURE",
        )

    exact = {
        "schema_version": "1.0",
        "dataset_sha256": dataset["dataset_sha256"],
        "retrieval_mode": "exact",
        "bindings": dataset["bindings"],
        "retrieval_limit": RETRIEVAL_LIMIT,
        "source_origin": source_origin,
        "source_bindings": bindings,
        "queries": observations,
    }
    observation_errors = evaluator.validate_observations(dataset, exact, "exact")
    if observation_errors:
        write_terminal_status(
            evidence_dir,
            base,
            status="INCOMPLETE",
            stage="FINAL_CONTRACT",
            completed_query_count=len(observations),
            error_codes=["FINAL_OBSERVATION_CONTRACT_ERROR"],
        )
        atomic_write_json(
            evidence_dir / PROGRESS_FILE,
            progress_artifact(
                base,
                observations,
                status="INCOMPLETE",
                next_query_id=None,
                error_codes=["FINAL_OBSERVATION_CONTRACT_ERROR"],
            ),
            replace=True,
        )
        raise CollectionError(
            "final exact observations failed the evaluator contract",
            code="FINAL_OBSERVATION_CONTRACT_ERROR",
        )

    atomic_write_json(
        evidence_dir / OBSERVATIONS_FILE, exact, replace=False
    )
    atomic_write_json(
        evidence_dir / PROGRESS_FILE,
        progress_artifact(
            base,
            observations,
            status="COMPLETE",
            next_query_id=None,
        ),
        replace=True,
    )
    write_terminal_status(
        evidence_dir,
        base,
        status="COMPLETE",
        stage="COMPLETE",
        completed_query_count=len(observations),
        error_codes=[],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("preflight", "collect"),
        default="collect",
        help="preflight makes zero search calls; collect performs the one-shot run",
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-source-origin", required=True)
    parser.add_argument("--session-cookie-file", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--database-identity-json",
        type=Path,
        help="public-safe SYNCBASE_DATABASE_IDENTITY_V1 payload",
    )
    parser.add_argument(
        "--model-artifact",
        type=Path,
        help="exact model bytes mounted into the release runtime",
    )
    parser.add_argument(
        "--tokenizer-artifact",
        type=Path,
        help="exact tokenizer bytes mounted into the release runtime",
    )
    parser.add_argument(
        "--profile-artifact",
        type=Path,
        help="exact SYNCBASE_RETRIEVAL_PROFILE_CANONICAL_V1 JSON bytes",
    )
    parser.add_argument(
        "--acknowledge-one-shot-exposure",
        help=(
            "must equal the frozen dataset_sha256; acknowledges that all 30 holdout "
            "queries will be exposed once and the evidence directory cannot be rerun"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        if arguments.timeout_seconds <= 0 or arguments.timeout_seconds > 60:
            raise CollectionError(
                "timeout must be greater than zero and at most 60 seconds",
                code="INVALID_TIMEOUT",
            )
        evaluator = load_evaluator()
        dataset, dataset_bytes = load_dataset(
            arguments.dataset, evaluator, mode=arguments.mode
        )
        database_identity = load_database_identity_payload(
            arguments.database_identity_json
        )
        retrieval_artifacts = load_retrieval_artifacts(
            arguments.model_artifact,
            arguments.tokenizer_artifact,
            arguments.profile_artifact,
        )
        release_hash = canonical_sha256(source_release_payload(dataset))
        database_hash = canonical_sha256(database_identity)
        if arguments.mode == "collect" and (
            arguments.acknowledge_one_shot_exposure != dataset["dataset_sha256"]
        ):
            raise CollectionError(
                "one-shot acknowledgment must equal the frozen dataset_sha256",
                code="ACKNOWLEDGMENT_REQUIRED",
            )
        if arguments.mode == "collect" and dataset["bindings"].get(
            "source_release_sha256"
        ) != release_hash:
            raise CollectionError(
                "frozen source_release_sha256 does not match repository revisions",
                code="SOURCE_RELEASE_BINDING_MISMATCH",
            )
        if arguments.mode == "collect" and dataset["bindings"].get(
            "database_identity_sha256"
        ) != database_hash:
            raise CollectionError(
                "frozen database_identity_sha256 does not match the supplied payload",
                code="DATABASE_IDENTITY_BINDING_MISMATCH",
            )
        if arguments.mode == "collect":
            for name, observed in retrieval_binding_values(
                retrieval_artifacts
            ).items():
                if dataset["bindings"].get(name) != observed:
                    raise CollectionError(
                        f"frozen {name} does not match the supplied exact artifact",
                        code=f"{name.upper()}_MISMATCH",
                    )
        base_url = normalize_local_base_url(arguments.base_url)
        source_origin = normalize_expected_source_origin(
            arguments.expected_source_origin
        )
        validate_cookie_file(arguments.session_cookie_file)
        client = LocalRestClient(
            base_url,
            arguments.session_cookie_file,
            arguments.timeout_seconds,
        )
        evidence_dir = prepare_evidence_directory(arguments.evidence_dir)
        seal_retrieval_artifacts(evidence_dir, retrieval_artifacts)
        if arguments.mode == "preflight":
            run_preflight_only(
                dataset,
                dataset_bytes,
                database_identity,
                retrieval_artifacts,
                client,
                evidence_dir,
                source_origin,
            )
            print("HOLDOUT_PREFLIGHT_COMPLETE")
        else:
            run_collection(
                dataset,
                dataset_bytes,
                database_identity,
                retrieval_artifacts,
                evaluator,
                client,
                evidence_dir,
                source_origin,
            )
            print("FROZEN_OBSERVATIONS_COMPLETE")
        print("benchmark_result=NOT_EVALUATED")
        print("claim_eligible=false")
        return 0
    except CollectionError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

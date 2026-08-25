#!/usr/bin/env python3
"""Generate deterministic synthetic V2 PDFs for the five Round-1 draft cases."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber
import pypdf
import reportlab
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


EXPECTED_PROTOCOL = {
    "strategy": "APPEND_ONE_INVARIANT_PDF_PAGE",
    "v1_bytes": "UNCHANGED_PUBLIC_BASE_PDF",
    "v2_bytes": "V1_PLUS_ONE_CANONICAL_MARKER_PAGE",
    "generator_contract": "REPORTLAB_INVARIANT_1_THEN_PYPDF_APPEND",
    "release_gate": "GENERATE_HASH_RENDER_HUMAN_APPROVE_BEFORE_FREEZE",
}
PLAN_IDS = ["VP01", "VP02", "VP03", "VP04", "VP05"]
EMBEDDED_FONT_NAME = "SyncBaseNanumGothic"
EMBEDDED_FONT_PATH = (
    Path(__file__).parent
    / "assets"
    / "fonts"
    / "nanum-gothic"
    / "NanumGothic-Regular.ttf"
)
EMBEDDED_FONT_SHA256 = (
    "76f45ef4a6bcff344c837c95a7dcc26e017e38b5846d5ae0cdcb5b86be2e2d31"
)
EMBEDDED_FONT_SOURCE_REVISION = "ec626514f79f831f1ab848a82114a0ce7e2d6372"


class FixtureError(ValueError):
    """A deterministic fixture precondition or verification failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def normalized_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def marker_page_bytes(marker: str, v2_only_text: str) -> bytes:
    """Return one canonical A4 marker page using ReportLab invariant mode."""

    if not EMBEDDED_FONT_PATH.is_file() or sha256_bytes(
        EMBEDDED_FONT_PATH.read_bytes()
    ) != EMBEDDED_FONT_SHA256:
        raise FixtureError("embedded Nanum Gothic font is missing or changed")
    if EMBEDDED_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont(EMBEDDED_FONT_NAME, str(EMBEDDED_FONT_PATH), validate=1)
        )
    output = io.BytesIO()
    page = canvas.Canvas(
        output,
        pagesize=A4,
        pageCompression=1,
        invariant=1,
    )
    page.setAuthor("Merge42 SyncBase")
    page.setCreator("SyncBase deterministic Round-1 fixture generator")
    page.setSubject("Synthetic supersession fixture; not a natural revision")
    page.setTitle(marker)
    width, height = A4
    page.setFillColor(HexColor("#111827"))
    page.setFont("Helvetica-Bold", 28)
    page.drawCentredString(width / 2, height * 0.56, marker)
    page.setFillColor(HexColor("#374151"))
    page.setFont(EMBEDDED_FONT_NAME, 16)
    page.drawCentredString(width / 2, height * 0.49, v2_only_text)
    page.showPage()
    page.save()
    return output.getvalue()


def incremental_append(v1_bytes: bytes, marker_bytes: bytes) -> bytes:
    """Append one page while keeping every original V1 byte as an exact prefix."""

    v1_stream = io.BytesIO(v1_bytes)
    marker_stream = io.BytesIO(marker_bytes)
    writer = PdfWriter(v1_stream, incremental=True)
    marker_reader = PdfReader(marker_stream)
    if len(marker_reader.pages) != 1:
        raise FixtureError("canonical marker PDF must contain exactly one page")
    writer.add_page(marker_reader.pages[0])
    output = io.BytesIO()
    writer.write(output)
    content = output.getvalue()
    if not content.startswith(v1_bytes):
        raise FixtureError("incremental append did not preserve the V1 byte prefix")
    return content


def safe_source_path(source_root: Path, relative_path: str) -> Path:
    root = source_root.resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise FixtureError("fixture source path escapes source root") from error
    if not path.is_file():
        raise FixtureError("fixture V1 source does not exist")
    return path


def validate_active_ground_truth_links(
    dataset: dict[str, Any], plans: list[dict[str, Any]]
) -> None:
    """Keep F/I ground truth on an active V2 after a fixture supersedes V1."""

    ready_by_v1 = {
        plan.get("v1_source_sha256"): plan
        for plan in plans
        if plan.get("status") == "READY"
    }
    ready_by_v2 = {
        plan.get("v2_source_sha256"): plan
        for plan in plans
        if plan.get("status") == "READY"
    }
    for query in dataset.get("queries", []):
        if not isinstance(query, dict) or query.get("category") not in {
            "factual_paraphrase",
            "exact_identifier",
        }:
            continue
        query_id = query.get("id", "UNKNOWN")
        evidence_items = query.get("candidate_evidence")
        expected = query.get("expected")
        relevant = expected.get("relevant") if isinstance(expected, dict) else None
        forbidden = expected.get("forbidden") if isinstance(expected, dict) else None
        if not isinstance(evidence_items, list) or len(evidence_items) != 1:
            raise FixtureError(f"{query_id} candidate evidence linkage is invalid")
        if not isinstance(relevant, list) or len(relevant) != 1:
            raise FixtureError(f"{query_id} relevant target linkage is invalid")
        evidence = evidence_items[0]
        target = relevant[0]
        if not isinstance(evidence, dict) or not isinstance(target, dict):
            raise FixtureError(f"{query_id} active ground-truth linkage is invalid")
        evidence_sha256 = evidence.get("source_sha256")
        target_sha256 = target.get("source_sha256")
        if evidence_sha256 in ready_by_v1 or target_sha256 in ready_by_v1:
            plan = ready_by_v1.get(evidence_sha256) or ready_by_v1[target_sha256]
            raise FixtureError(
                f"{query_id} still targets READY fixture V1 {plan.get('id')}"
            )
        plan = ready_by_v2.get(evidence_sha256)
        if plan is None:
            if target_sha256 in ready_by_v2:
                raise FixtureError(
                    f"{query_id} candidate evidence does not match its READY fixture V2"
                )
            continue
        evidence_page = evidence.get("page")
        marker_page = plan.get("v2_page")
        if (
            evidence.get("source_file") != plan.get("v2_source_file")
            or target_sha256 != plan.get("v2_source_sha256")
            or target.get("version") != 2
            or target.get("pages") != [evidence_page]
            or not isinstance(evidence_page, int)
            or isinstance(evidence_page, bool)
            or not isinstance(marker_page, int)
            or isinstance(marker_page, bool)
            or evidence_page >= marker_page
        ):
            raise FixtureError(
                f"{query_id} does not bind the preserved original page to "
                f"READY fixture V2 {plan.get('id')}"
            )
        if forbidden:
            raise FixtureError(
                f"{query_id} must not carry fixture V1 as a forbidden target"
            )


def validate_inputs(
    dataset: dict[str, Any], source_root: Path
) -> list[dict[str, Any]]:
    if dataset.get("status") != "DRAFT":
        raise FixtureError("version fixture generator accepts DRAFT datasets only")
    if dataset.get("version_fixture_protocol") != EXPECTED_PROTOCOL:
        raise FixtureError("version fixture protocol does not match the accepted contract")
    plans = dataset.get("version_fixture_plans")
    if not isinstance(plans, list) or [plan.get("id") for plan in plans] != PLAN_IDS:
        raise FixtureError("version fixture plans must be VP01 through VP05 in order")
    validate_active_ground_truth_links(dataset, plans)

    prepared: list[dict[str, Any]] = []
    for plan in plans:
        if plan.get("status") not in {"PLANNED_NOT_GENERATED", "READY"}:
            raise FixtureError(f"{plan['id']} has an unsupported status")
        base_source = plan.get("base_source")
        if not isinstance(base_source, dict):
            raise FixtureError(f"{plan['id']} base source is invalid")
        source_file = base_source.get("source_file")
        if not isinstance(source_file, str):
            raise FixtureError(f"{plan['id']} source path is invalid")
        path = safe_source_path(source_root, source_file)
        v1_bytes = path.read_bytes()
        actual_sha256 = sha256_bytes(v1_bytes)
        if (
            actual_sha256 != base_source.get("source_sha256")
            or actual_sha256 != plan.get("v1_source_sha256")
        ):
            raise FixtureError(f"{plan['id']} V1 SHA-256 mismatch")
        page_count = len(PdfReader(io.BytesIO(v1_bytes)).pages)
        if plan.get("v2_page") != page_count + 1:
            raise FixtureError(f"{plan['id']} V2 page is not V1 page count plus one")
        marker = plan.get("v2_marker")
        v2_only_text = plan.get("v2_only_text")
        if not isinstance(marker, str) or not isinstance(v2_only_text, str):
            raise FixtureError(f"{plan['id']} marker text is invalid")
        if marker not in v2_only_text:
            raise FixtureError(f"{plan['id']} V2 text does not contain its marker")
        prepared.append(
            {
                "plan": plan,
                "v1_path": path,
                "v1_bytes": v1_bytes,
                "v1_sha256": actual_sha256,
                "v1_page_count": page_count,
            }
        )
    return prepared


def verify_v2(
    *,
    plan: dict[str, Any],
    v1_bytes: bytes,
    v2_bytes: bytes,
) -> None:
    if not v2_bytes.startswith(v1_bytes):
        raise FixtureError(f"{plan['id']} does not preserve the V1 byte prefix")
    reader = PdfReader(io.BytesIO(v2_bytes))
    if len(reader.pages) != plan["v2_page"]:
        raise FixtureError(f"{plan['id']} V2 page count mismatch")
    with pdfplumber.open(io.BytesIO(v2_bytes)) as pdf:
        final_text = pdf.pages[-1].extract_text() or ""
    if plan["v2_marker"] not in final_text:
        raise FixtureError(f"{plan['id']} final page marker extraction failed")
    if normalized_text(plan["v2_only_text"]) not in normalized_text(final_text):
        raise FixtureError(f"{plan['id']} final page V2 text extraction failed")


def write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        Path(temporary_name).replace(path)
    except Exception:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def render_final_page(
    *, pdftoppm: Path, pdf_path: Path, page_number: int, output_prefix: Path
) -> Path:
    completed = subprocess.run(
        [
            str(pdftoppm),
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-singlefile",
            "-r",
            "144",
            "-png",
            str(pdf_path),
            str(output_prefix),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise FixtureError("pdftoppm could not render a fixture final page")
    rendered = output_prefix.with_suffix(".png")
    if not rendered.is_file() or rendered.stat().st_size < 1024:
        raise FixtureError("rendered fixture page is missing or empty")
    with Image.open(rendered) as image:
        extrema = image.convert("L").getextrema()
        if image.width < 1000 or image.height < 1000 or extrema is None:
            raise FixtureError("rendered fixture page dimensions are invalid")
        if extrema[0] == extrema[1]:
            raise FixtureError("rendered fixture page has no visible content")
    return rendered


def relative_artifact_path(path: Path, artifact_root: Path) -> str:
    return path.resolve().relative_to(artifact_root.resolve()).as_posix()


def generate_all(
    *,
    dataset: dict[str, Any],
    source_root: Path,
    output_dir: Path,
    render_dir: Path,
    temp_root: Path,
    pdftoppm: Path,
) -> dict[str, Any]:
    """Generate and independently render five V2 fixtures after full preflight."""

    prepared = validate_inputs(dataset, source_root)
    if not pdftoppm.is_file() or not os.access(pdftoppm, os.X_OK):
        raise FixtureError("pdftoppm executable is unavailable")
    artifact_root = Path(os.path.commonpath([output_dir, render_dir]))
    temp_root.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="round1-v2-", dir=temp_root) as directory:
        temporary = Path(directory)
        for item in prepared:
            plan = item["plan"]
            marker_first = marker_page_bytes(plan["v2_marker"], plan["v2_only_text"])
            marker_second = marker_page_bytes(plan["v2_marker"], plan["v2_only_text"])
            if marker_first != marker_second:
                raise FixtureError(f"{plan['id']} marker page generation is not deterministic")
            v2_first = incremental_append(item["v1_bytes"], marker_first)
            v2_second = incremental_append(item["v1_bytes"], marker_second)
            if v2_first != v2_second:
                raise FixtureError(f"{plan['id']} V2 generation is not deterministic")
            verify_v2(plan=plan, v1_bytes=item["v1_bytes"], v2_bytes=v2_first)
            v2_sha256 = sha256_bytes(v2_first)
            if plan["status"] == "READY" and plan.get("v2_source_sha256") != v2_sha256:
                raise FixtureError(f"{plan['id']} generated V2 SHA-256 changed")

            fixture_name = f"{plan['query_id']}-synthetic-supersession-v2.pdf"
            staged_pdf = temporary / fixture_name
            staged_pdf.write_bytes(v2_first)
            render_prefix = temporary / f"{plan['query_id']}-final-page"
            staged_render = render_final_page(
                pdftoppm=pdftoppm,
                pdf_path=staged_pdf,
                page_number=plan["v2_page"],
                output_prefix=render_prefix,
            )
            with Image.open(staged_render) as rendered:
                render_width, render_height = rendered.size
            staged.append(
                {
                    "plan": plan,
                    "item": item,
                    "v2_bytes": v2_first,
                    "v2_sha256": v2_sha256,
                    "fixture_name": fixture_name,
                    "render_name": f"{plan['query_id']}-final-page.png",
                    "render_bytes": staged_render.read_bytes(),
                    "render_width": render_width,
                    "render_height": render_height,
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for staged_item in staged:
        plan = staged_item["plan"]
        item = staged_item["item"]
        final_pdf = output_dir / staged_item["fixture_name"]
        final_render = render_dir / staged_item["render_name"]
        write_bytes_atomic(final_pdf, staged_item["v2_bytes"])
        write_bytes_atomic(final_render, staged_item["render_bytes"])
        if sha256_bytes(item["v1_path"].read_bytes()) != item["v1_sha256"]:
            raise FixtureError(f"{plan['id']} V1 changed during generation")
        results.append(
            {
                "plan_id": plan["id"],
                "query_id": plan["query_id"],
                "fixture_relative_path": relative_artifact_path(
                    final_pdf, artifact_root
                ),
                "render_relative_path": relative_artifact_path(
                    final_render, artifact_root
                ),
                "v1_source_file": plan["base_source"]["source_file"],
                "v1_sha256": item["v1_sha256"],
                "v1_page_count": item["v1_page_count"],
                "v2_sha256": staged_item["v2_sha256"],
                "v2_page_count": plan["v2_page"],
                "v2_marker": plan["v2_marker"],
                "v2_only_text": plan["v2_only_text"],
                "v1_byte_prefix_preserved": True,
                "two_generation_runs_byte_identical": True,
                "final_page_text_extracted": True,
                "final_page_rendered": True,
                "render_sha256": sha256_bytes(staged_item["render_bytes"]),
                "render_width": staged_item["render_width"],
                "render_height": staged_item["render_height"],
            }
        )

    return {
        "schema_version": "1.0",
        "artifact_kind": "DRAFT_VERSION_FIXTURE_MANIFEST",
        "status": "MACHINE_READY_HUMAN_REVIEW_PENDING",
        "claim_eligible": False,
        "dataset_id": dataset["dataset_id"],
        "generator_contract": EXPECTED_PROTOCOL["generator_contract"],
        "generator_source_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "generated_at": utc_now(),
        "dependencies": {
            "reportlab": reportlab.Version,
            "pypdf": pypdf.__version__,
            "font_resource": "NanumGothic-Regular.ttf",
            "font_sha256": EMBEDDED_FONT_SHA256,
            "font_license": "OFL-1.1",
            "font_source_revision": EMBEDDED_FONT_SOURCE_REVISION,
            "renderer": "pdftoppm",
        },
        "fixture_count": len(results),
        "fixtures": results,
        "human_review_required": [
            "Open every rendered final-page PNG and confirm marker/text legibility.",
            "Open every V2 PDF and confirm the original V1 pages precede one marker page.",
            "Approve the five V1/V2 worksheet rows before global dataset approval.",
        ],
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    content = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    write_bytes_atomic(path, content)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pdftoppm", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        try:
            dataset = json.loads(arguments.dataset.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FixtureError("dataset is unavailable or invalid JSON") from error
        if not isinstance(dataset, dict):
            raise FixtureError("dataset must be a JSON object")
        renderer = arguments.pdftoppm or (
            Path(found) if (found := shutil.which("pdftoppm")) else None
        )
        if renderer is None:
            raise FixtureError("pdftoppm executable is unavailable")
        manifest = generate_all(
            dataset=dataset,
            source_root=arguments.source_root,
            output_dir=arguments.output_dir,
            render_dir=arguments.render_dir,
            temp_root=arguments.temp_root,
            pdftoppm=renderer,
        )
        write_manifest(arguments.manifest, manifest)
        print(arguments.manifest)
        return 0
    except (FixtureError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

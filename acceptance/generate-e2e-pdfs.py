#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable


FIXTURES = {
    "syncbase-e2e-v1.pdf": [
        [
            "Operations Recovery Policy",
            "Primary failover requires search verification.",
            "Committed data loss is not allowed.",
        ]
    ],
    "syncbase-e2e-v2.pdf": [
        [
            "Operations Recovery Policy Version Two",
            "Every incident records an owner and an escalation path.",
            "Recovery exercises are reviewed each quarter.",
        ],
        [
            "Primary Failover Evidence",
            "Primary failover search verification requires zero committed data loss.",
            "Recovery evidence must be retained after service restoration.",
        ],
    ],
}


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def pdf_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def content_stream(lines: Iterable[str]) -> bytes:
    commands = ["BT", "/F1 18 Tf", "72 760 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.extend(["0 -32 Td", "/F1 12 Tf"])
        commands.append(f"({pdf_string(line)}) Tj")
    commands.append("ET")
    return ("\n".join(commands) + "\n").encode("ascii")


def build_pdf(pages: list[list[str]]) -> bytes:
    font_id = 3
    page_ids = [4 + index * 2 for index in range(len(pages))]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Count {len(pages)} /Kids "
            f"[{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] >>"
        ).encode("ascii"),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, lines in enumerate(pages):
        page_id = page_ids[index]
        stream_id = page_id + 1
        stream = content_stream(lines)
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {stream_id} 0 R >>"
        ).encode("ascii")
        objects[stream_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream"
        )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (max(objects) + 1)
    for object_id in sorted(objects):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def generate(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for file_name, pages in FIXTURES.items():
        content = build_pdf(pages)
        target = output_dir / file_name
        target.write_bytes(content)
        generated.append(
            {
                "file": file_name,
                "file_sha256": sha256(content),
                "page_count": len(pages),
                "page_text_sha256": [
                    sha256("\n".join(page).encode("utf-8")) for page in pages
                ],
            }
        )
    manifest = {"fixture": "syncbase-portable-e2e", "documents": generated}
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gate_manifest = {
        "fixture_id": "syncbase-portable-e2e",
        "iterations": 2,
        "fixtures": [
            {
                "id": document["file"].removesuffix(".pdf"),
                "file": document["file"],
                "expectation": "VALID_TEXT_PDF",
                "page_sha256": document["page_text_sha256"],
            }
            for document in generated
        ],
    }
    (output_dir / "pdf-gate-manifest.json").write_text(
        json.dumps(gate_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output/pdf/syncbase-e2e"))
    args = parser.parse_args()
    print(json.dumps(generate(args.output), sort_keys=True))


if __name__ == "__main__":
    main()

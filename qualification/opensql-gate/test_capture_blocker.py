from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("capture_blocker.py")
SPEC = importlib.util.spec_from_file_location("capture_blocker", MODULE_PATH)
assert SPEC and SPEC.loader
capture_blocker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_blocker)


class CaptureBlockerTest(unittest.TestCase):
    def test_all_evidence_artifacts_exist(self) -> None:
        for path in capture_blocker.artifact_paths():
            self.assertTrue(path.is_file(), path)
            self.assertEqual(len(capture_blocker.sha256(path)), 64)

    @mock.patch.object(
        capture_blocker.subprocess,
        "run",
        side_effect=FileNotFoundError,
    )
    def test_missing_orbctl_produces_a_blocked_snapshot(self, _: mock.Mock) -> None:
        observed = capture_blocker.snapshot()
        self.assertEqual(observed["package_files"], 0)
        self.assertEqual(observed["executables"]["opensql"], "MISSING")
        self.assertEqual(observed["snapshot_error"], "orbctl is not installed")

    @mock.patch.object(
        capture_blocker.subprocess,
        "run",
        side_effect=subprocess.CalledProcessError(7, ["orbctl"]),
    )
    def test_failed_vm_command_does_not_abort_evidence_capture(self, _: mock.Mock) -> None:
        observed = capture_blocker.snapshot()
        self.assertEqual(observed["db_listeners"], 0)
        self.assertEqual(observed["snapshot_error"], "orbctl exited with status 7")

    def test_result_uses_the_shared_round1_schema_and_five_revisions(self) -> None:
        revisions = {
            "frontend": "1" * 40,
            "embedding": "2" * 40,
            "was": "3" * 40,
            "infra": "4" * 40,
            "mcp": "5" * 40,
        }
        result = capture_blocker.build_result(
            capture_blocker.unavailable_snapshot("fixture unavailable"),
            revisions,
            run_id="fixture-run",
            started_at="2026-08-25T00:00:00Z",
            completed_at="2026-08-25T00:00:01Z",
        )

        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["task_id"], "C3_OPENSQL_SMOKE")
        self.assertEqual(result["overall_result"], "BLOCKED")
        self.assertEqual(result["evidence_grade"], "UNAVAILABLE")
        self.assertEqual(result["repository_revisions"], revisions)
        self.assertEqual(result["run_id"], "fixture-run")
        self.assertEqual(len(result["result_sha256"]), 64)
        self.assertNotIn("overall_verdict", result)
        self.assertNotIn("source_revision", result)

    def test_cli_writes_a_schema_valid_blocked_result_from_a_captured_snapshot(self) -> None:
        snapshot = capture_blocker.unavailable_snapshot("fixture unavailable")
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            snapshot_path = temporary / "snapshot.json"
            output_path = temporary / "result.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "--snapshot-json",
                    str(snapshot_path),
                    "--output",
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["overall_result"], "BLOCKED")
            self.assertEqual(result["evidence_grade"], "UNAVAILABLE")
            self.assertEqual(set(result["repository_revisions"]), {
                "frontend", "embedding", "was", "infra", "mcp"
            })
            self.assertEqual(completed.stdout.strip(), str(output_path))


if __name__ == "__main__":
    unittest.main()

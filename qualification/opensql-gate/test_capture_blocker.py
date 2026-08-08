from __future__ import annotations

import importlib.util
import subprocess
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

    @mock.patch.object(
        capture_blocker.subprocess,
        "run",
        side_effect=FileNotFoundError,
    )
    def test_missing_git_uses_no_vcs_revision(self, _: mock.Mock) -> None:
        self.assertEqual(capture_blocker.source_revision(), "NO_VCS")


if __name__ == "__main__":
    unittest.main()

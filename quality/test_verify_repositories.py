from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_repositories.py")


def load_module():
    spec = importlib.util.spec_from_file_location("verify_repositories", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VerifyRepositoriesTest(unittest.TestCase):
    def make_repository(self, workspace: Path, directory: str) -> str:
        repository = workspace / directory
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        (repository / "README.md").write_text(f"# {directory}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Round1 Test",
                "-c",
                "user.email=round1@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_cli_reports_all_five_full_revisions(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            expected = {
                repository_id: self.make_repository(workspace, directory)
                for repository_id, directory in module.REPOSITORIES.items()
            }
            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "--workspace-root",
                    str(workspace),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["overall_result"], "PASS")
            self.assertEqual(result["evidence_grade"], "SOURCE_BASELINE")
            self.assertEqual(result["repository_revisions"], expected)
            self.assertNotIn(str(workspace), completed.stdout)

    def test_cli_fails_without_printing_local_paths_when_a_repository_is_missing(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            for repository_id, directory in module.REPOSITORIES.items():
                if repository_id != "mcp":
                    self.make_repository(workspace, directory)
            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "--workspace-root",
                    str(workspace),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 1)
            result = json.loads(completed.stdout)
            self.assertEqual(result["overall_result"], "FAIL")
            self.assertEqual(result["repository_revisions"]["mcp"], "MISSING")
            self.assertNotIn(str(workspace), completed.stdout)

    def test_allow_dirty_can_inventory_development_but_cannot_claim_source_baseline(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            for directory in module.REPOSITORIES.values():
                self.make_repository(workspace, directory)
            (workspace / module.REPOSITORIES["was"] / "DIRTY.txt").write_text(
                "not committed\n", encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "--workspace-root",
                    str(workspace),
                    "--allow-dirty",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["overall_result"], "PASS")
            self.assertEqual(result["task_id"], "C0_DEVELOPMENT_INVENTORY")
            self.assertEqual(result["evidence_grade"], "DEVELOPMENT_ONLY")


if __name__ == "__main__":
    unittest.main()

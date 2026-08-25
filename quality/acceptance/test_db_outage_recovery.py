#!/usr/bin/env python3
"""Mocked contract tests for the isolated single-node outage diagnostic."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


INFRA_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = INFRA_ROOT / "acceptance" / "run-db-outage-recovery.sh"
MCP_SENTINEL = "sb_mcp_v1_TEST_SENTINEL_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
CSRF_SENTINEL = "csrf_TEST_SENTINEL_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
COOKIE_SENTINEL = "cookie_TEST_SENTINEL_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class DBOutageRecoveryTest(unittest.TestCase):
    maxDiff = None

    def make_fixture(self, root: Path, *, mode: str = "success") -> dict[str, str]:
        mock_bin = root / "mock-bin"
        mock_bin.mkdir()
        state = root / "state"
        state.mkdir()
        evidence = root / "evidence"
        evidence.mkdir()
        command_log = root / "commands.tsv"
        command_log.touch()

        compose_env = root / "compose.env"
        compose_env.write_text("TEST_ONLY_COMPOSE_INPUT=true\n", encoding="utf-8")
        compose_env.chmod(0o600)
        cookie_jar = root / "session.cookies"
        cookie_jar.write_text(COOKIE_SENTINEL + "\n", encoding="utf-8")
        cookie_jar.chmod(0o600)
        mcp_token = root / "mcp.token"
        mcp_token.write_text(MCP_SENTINEL + "\n", encoding="utf-8")
        mcp_token.chmod(0o600)
        sample_pdf = root / "sample.pdf"
        sample_pdf.write_bytes(b"%PDF-1.4\n% mocked boundary fixture\n")

        write_executable(
            mock_bin / "docker",
            r"""
            #!/usr/bin/env bash
            set -euo pipefail
            for argument in "$@"; do
              case "$argument" in
                *TEST_SENTINEL*) exit 97 ;;
              esac
            done
            {
              printf 'docker'
              printf '\t%s' "$@"
              printf '\n'
            } >>"$MOCK_COMMAND_LOG"

            if [[ "${1:-}" == "inspect" ]]; then
              container_id="${@: -1}"
              if [[ "$*" == *'{{.State.Running}}'* ]]; then
                if [[ -f "$MOCK_STATE_DIR/postgres-stopped" ]]; then
                  echo 'false'
                else
                  echo 'true'
                fi
                exit 0
              fi
              case "$container_id" in
                a*) echo '2026-08-25T00:00:01.000000000Z' ;;
                b*) echo '2026-08-25T00:00:02.000000000Z' ;;
                c*) echo '2026-08-25T00:00:03.000000000Z' ;;
                d*) echo '2026-08-25T00:00:04.000000000Z' ;;
                e*) echo '2026-08-25T00:00:05.000000000Z' ;;
                *) exit 1 ;;
              esac
              exit 0
            fi

            command_name=''
            service=''
            for argument in "$@"; do
              case "$argument" in
                ps|pause|unpause|stop|start)
                  command_name="$argument"
                  ;;
                api|web|worker|mcp|postgres)
                  service="$argument"
                  ;;
              esac
            done
            case "$command_name:$service" in
              ps:api) printf '%064d\n' 0 | tr '0' 'b' ;;
              ps:web) printf '%064d\n' 0 | tr '0' 'c' ;;
              ps:worker) printf '%064d\n' 0 | tr '0' 'd' ;;
              ps:mcp) printf '%064d\n' 0 | tr '0' 'a' ;;
              ps:postgres) printf '%064d\n' 0 | tr '0' 'e' ;;
              stop:postgres) touch "$MOCK_STATE_DIR/postgres-stopped" ;;
              start:postgres)
                touch "$MOCK_STATE_DIR/postgres-started"
                rm -f "$MOCK_STATE_DIR/postgres-stopped"
                ;;
              pause:worker) touch "$MOCK_STATE_DIR/worker-paused" ;;
              unpause:worker) touch "$MOCK_STATE_DIR/worker-unpaused" ;;
              *) exit 2 ;;
            esac
            """,
        )
        write_executable(
            mock_bin / "curl",
            rf"""
            #!/usr/bin/env bash
            set -euo pipefail
            for argument in "$@"; do
              case "$argument" in
                *TEST_SENTINEL*) exit 97 ;;
              esac
            done
            {{
              printf 'curl'
              printf '\t%s' "$@"
              printf '\n'
            }} >>"$MOCK_COMMAND_LOG"

            output=''
            url=''
            write_out=false
            while (($#)); do
              case "$1" in
                --output|-o)
                  output="$2"
                  shift 2
                  ;;
                --write-out|-w)
                  write_out=true
                  shift 2
                  ;;
                --config|-K|--data-binary|--data-urlencode|--form|--form-string)
                  shift 2
                  ;;
                --get)
                  shift
                  ;;
                http://*|https://*)
                  url="$1"
                  shift
                  ;;
                *)
                  shift
                  ;;
              esac
            done
            [[ -n "$output" && -n "$url" ]] || exit 96

            status=200
            body='{{}}'
            case "$url" in
              */mcp)
                count_file="$MOCK_STATE_DIR/mcp-count"
                count=0
                [[ ! -f "$count_file" ]] || count="$(<"$count_file")"
                count=$((count + 1))
                printf '%s\n' "$count" >"$count_file"
                case "$count" in
                  1|3)
                    body='{{"jsonrpc":"2.0","id":7,"result":{{"isError":false,"structuredContent":{{"grounding_status":"SUPPORTED","results":[{{"document_id":"11111111-1111-1111-1111-111111111111","document_version":2}}]}}}}}}'
                    ;;
                  2)
                    if [[ "$MOCK_MODE" == "bad-mcp-outage" ]]; then
                      body='{{"jsonrpc":"2.0","id":7,"result":{{"isError":true,"content":[{{"type":"text","text":"INTERNAL"}}]}}}}'
                    else
                      body='{{"jsonrpc":"2.0","id":7,"result":{{"isError":false,"structuredContent":{{"grounding_status":"INSUFFICIENT_EVIDENCE","grounding_reason":"SOURCE_UNAVAILABLE","results":[]}}}}}}'
                    fi
                    ;;
                  *) exit 95 ;;
                esac
                ;;
              */api/v1/session)
                body='{{"csrfToken":"{CSRF_SENTINEL}"}}'
                ;;
              */api/v1/documents)
                status=201
                body='{{"documentId":"22222222-2222-2222-2222-222222222222"}}'
                ;;
              */api/v1/documents/22222222-2222-2222-2222-222222222222)
                count_file="$MOCK_STATE_DIR/document-count"
                count=0
                [[ ! -f "$count_file" ]] || count="$(<"$count_file")"
                count=$((count + 1))
                printf '%s\n' "$count" >"$count_file"
                case "$count" in
                  1)
                    body='{{"activeVersion":null,"versions":[{{"status":"QUEUED"}}]}}'
                    ;;
                  2)
                    body='{{"activeVersion":null,"versions":[{{"status":"PROCESSING","runId":"33333333-3333-3333-3333-333333333333"}}]}}'
                    ;;
                  *)
                    body='{{"activeVersion":1,"versions":[{{"status":"ACTIVE","pageCount":3}}]}}'
                    ;;
                esac
                ;;
              */api/v1/search*)
                status=503
                body='{{"error":{{"code":"TEMPORARILY_UNAVAILABLE","retryable":true}}}}'
                ;;
              */readyz)
                body='{{"status":"ready"}}'
                ;;
              *) exit 94 ;;
            esac
            printf '%s\n' "$body" >"$output"
            if [[ "$write_out" == true ]]; then
              printf '%s' "$status"
            fi
            """,
        )
        write_executable(
            mock_bin / "openssl",
            """
            #!/usr/bin/env bash
            set -euo pipefail
            [[ "$*" == "rand -hex 16" ]] || exit 2
            printf '0123456789abcdef0123456789abcdef\n'
            """,
        )

        environment = os.environ.copy()
        environment.update(
            {
                "PATH": str(mock_bin) + os.pathsep + environment["PATH"],
                "MOCK_COMMAND_LOG": str(command_log),
                "MOCK_MODE": mode,
                "MOCK_STATE_DIR": str(state),
                "SYNCBASE_OUTAGE_ENVIRONMENT": "isolated-test",
                "SYNCBASE_COMPOSE_PROJECT_NAME": "syncbase-round1-threshold",
                "SYNCBASE_COMPOSE_ENV_FILE": str(compose_env),
                "SYNCBASE_EVIDENCE_DIR": str(evidence),
                "SYNCBASE_RUN_ID": "mocked-contract",
                "SYNCBASE_WEB_URL": "http://127.0.0.1:8080",
                "SYNCBASE_SESSION_COOKIE_JAR": str(cookie_jar),
                "SYNCBASE_MCP_URL": "http://127.0.0.1:8081",
                "SYNCBASE_MCP_TOKEN_FILE": str(mcp_token),
                "SYNCBASE_SEARCH_QUERY": "mocked search boundary",
                "SYNCBASE_SAMPLE_PDF": str(sample_pdf),
                "SYNCBASE_SAMPLE_DOCUMENT_NAME": "Mocked recovery Document",
                "SYNCBASE_EXPECTED_DOCUMENT_VERSION": "2",
            }
        )
        return environment

    def run_fixture(self, root: Path, *, mode: str = "success") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=INFRA_ROOT,
            env=self.make_fixture(root, mode=mode),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_non_isolated_environment_is_rejected_before_protected_files_are_read(self) -> None:
        environment = os.environ.copy()
        environment["SYNCBASE_OUTAGE_ENVIRONMENT"] = "development"
        environment["SYNCBASE_MCP_TOKEN_FILE"] = "/does/not/exist"
        completed = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=INFRA_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 64)
        self.assertIn("isolated-test", completed.stderr)
        self.assertNotIn("unreadable", completed.stderr)

    def test_non_loopback_or_path_bearing_origins_are_rejected_before_boundaries(self) -> None:
        rejected_origins = (
            "https://example.test:8080",
            "http://localhost:8080/api",
            "http://user@127.0.0.1:8080",
            "http://127.0.0.1:8080?debug=true",
            "http://127.0.0.1:8080#fragment",
        )
        for origin in rejected_origins:
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                environment = self.make_fixture(root)
                environment["SYNCBASE_WEB_URL"] = origin
                completed = subprocess.run(
                    ["bash", str(SCRIPT)],
                    cwd=INFRA_ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(completed.returncode, 64)
                self.assertIn("uncredentialed loopback origin", completed.stderr)
                self.assertEqual((root / "commands.tsv").read_text(encoding="utf-8"), "")

    def test_success_publishes_sanitized_atomic_diagnostic_without_app_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            completed = self.run_fixture(root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            combined_output = completed.stdout + completed.stderr
            self.assertIn("safe_run_order=after-frozen-benchmark-or-separate-corpus-project", combined_output)
            for sentinel in (MCP_SENTINEL, CSRF_SENTINEL, COOKIE_SENTINEL):
                self.assertNotIn(sentinel, combined_output)

            evidence_root = root / "evidence"
            evidence_directories = [path for path in evidence_root.iterdir() if path.is_dir()]
            self.assertEqual(len(evidence_directories), 1)
            self.assertFalse(any(path.name.startswith(".") for path in evidence_root.iterdir()))
            result_path = evidence_directories[0] / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            rendered_result = json.dumps(result)
            for sentinel in (MCP_SENTINEL, CSRF_SENTINEL, COOKIE_SENTINEL):
                self.assertNotIn(sentinel, rendered_result)
            self.assertNotRegex(rendered_result, re.compile(r"\b(?:HA|OpenSQL)\b", re.IGNORECASE))

            self.assertEqual(result["overall_result"], "PASS")
            self.assertEqual(
                result["evidence_grade"],
                "ISOLATED_SINGLE_NODE_DIAGNOSTIC_NOT_RELEASE_CLAIM_GRADE",
            )
            self.assertFalse(result["claim_eligible"])
            self.assertEqual(result["environment"], "isolated-test")
            self.assertEqual(result["topology"], "single-node")
            self.assertEqual(result["release_bindings"]["status"], "NOT_SUPPLIED")
            self.assertTrue(result["corpus_impact"]["registers_additional_document"])
            self.assertEqual(
                result["corpus_impact"]["safe_run_order"],
                "AFTER_FROZEN_BENCHMARK_OR_SEPARATE_CORPUS_PROJECT",
            )
            self.assertEqual(result["facts"]["outage"]["api_search"]["http_status"], 503)
            self.assertEqual(
                result["facts"]["outage"]["api_search"]["error_code"],
                "TEMPORARILY_UNAVAILABLE",
            )
            self.assertTrue(result["facts"]["outage"]["api_search"]["retryable"])
            self.assertEqual(result["facts"]["outage"]["mcp_search"]["http_status"], 200)
            self.assertEqual(
                result["facts"]["outage"]["mcp_search"]["grounding_status"],
                "INSUFFICIENT_EVIDENCE",
            )
            self.assertEqual(
                result["facts"]["outage"]["mcp_search"]["grounding_reason"],
                "SOURCE_UNAVAILABLE",
            )
            self.assertTrue(result["facts"]["recovery"]["app_containers_unchanged"])
            self.assertEqual(result["facts"]["recovery"]["search_active_version"], 2)
            self.assertTrue(result["facts"]["recovery"]["processing"]["recovered"])
            self.assertEqual(
                result["facts"]["before"]["app_containers"]["mcp"]["id"], "a" * 64
            )
            self.assertEqual(
                result["facts"]["before"]["app_containers"]["worker"]["started_at"],
                "2026-08-25T00:00:04.000000000Z",
            )
            self.assertLessEqual(result["measurements"]["database_readiness_seconds"], 30)
            self.assertLessEqual(result["measurements"]["processing_recovery_seconds"], 120)

            command_log = (root / "commands.tsv").read_text(encoding="utf-8")
            for sentinel in (MCP_SENTINEL, CSRF_SENTINEL, COOKIE_SENTINEL):
                self.assertNotIn(sentinel, command_log)
            self.assertIn("--project-name\tsyncbase-round1-threshold", command_log)
            self.assertIn(str(INFRA_ROOT / "compose.yml"), command_log)
            self.assertIn(str(INFRA_ROOT / "environments/local/compose.yml"), command_log)
            self.assertIn(str(INFRA_ROOT / "environments/local/build-was.yml"), command_log)
            self.assertIn(str(INFRA_ROOT / "environments/local/build-mcp.yml"), command_log)
            self.assertIn(str(INFRA_ROOT / "environments/local/build-frontend.yml"), command_log)
            self.assertNotRegex(
                command_log,
                re.compile(r"docker\tcompose.*\t(?:start|stop|restart)\t(?:api|web|worker|mcp)(?:\t|$)"),
            )

    def test_world_readable_protected_input_is_rejected_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = self.make_fixture(root)
            Path(environment["SYNCBASE_MCP_TOKEN_FILE"]).chmod(0o644)
            completed = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=INFRA_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 66)
            self.assertIn("must not be group- or world-accessible", completed.stderr)
            self.assertEqual((root / "commands.tsv").read_text(encoding="utf-8"), "")
            self.assertNotIn(MCP_SENTINEL, completed.stdout + completed.stderr)

    def test_failure_after_database_stop_runs_both_restore_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            completed = self.run_fixture(root, mode="bad-mcp-outage")

            self.assertNotEqual(completed.returncode, 0)
            command_lines = (root / "commands.tsv").read_text(encoding="utf-8").splitlines()
            stop_index = next(
                index
                for index, line in enumerate(command_lines)
                if "\tstop\tpostgres" in line
            )
            later_lines = command_lines[stop_index + 1 :]
            self.assertTrue(any("\tstart\tpostgres" in line for line in later_lines))
            self.assertTrue(any("\tunpause\tworker" in line for line in later_lines))
            for sentinel in (MCP_SENTINEL, CSRF_SENTINEL, COOKIE_SENTINEL):
                self.assertNotIn(sentinel, completed.stdout + completed.stderr)

    def test_script_keeps_hard_stop_loss_constants_and_no_secret_header_argv(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("database_deadline=$((SECONDS + 30))", source)
        self.assertIn("processing_deadline=$((SECONDS + 120))", source)
        self.assertNotRegex(source, re.compile(r'--header\s+"Authorization: Bearer \$'))
        self.assertNotRegex(source, re.compile(r'--header\s+"X-CSRF-Token: \$'))
        self.assertNotIn('mcp_token="$(<', source)
        self.assertNotIn('mcp_token="$(tr', source)
        self.assertNotIn('cat "$api_outage_body"', source)
        self.assertNotRegex(source, re.compile(r"\b(?:HA|OpenSQL)\b", re.IGNORECASE))
        self.assertGreaterEqual(source.count("'noproxy = \"*\"'"), 2)


if __name__ == "__main__":
    unittest.main()

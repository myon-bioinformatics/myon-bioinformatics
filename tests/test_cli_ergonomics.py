"""Real process runs for opt-in JSON errors, full flags, and guiding hints.

Default text and exit codes stay compatible with vendored callers. These tests
spawn the tools; they do not replace the in-process suites.
"""
from __future__ import annotations

import functools
import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(script: str, argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", str(ROOT / script), *argv],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
    )


def test_gh_ops_help_documents_exit_codes_and_refuses_abbreviations():
    help_run = run("gh_ops.py", ["--help"])
    assert help_run.returncode == 0, help_run.stderr
    assert "Exit codes" in help_run.stdout
    assert "--json-errors" in help_run.stdout
    assert "issue-comments" in help_run.stdout

    abbreviated = run("gh_ops.py", ["--js", "pr-status", "octo/demo", "1"])
    assert abbreviated.returncode == 2
    assert abbreviated.stdout == ""
    assert "unrecognized arguments" in abbreviated.stderr

    plain = run("gh_ops.py", ["pr-status", "bad repo", "1"])
    assert plain.returncode == 2 and plain.stdout == ""
    assert "repository must look like" in plain.stderr

    guided = run("gh_ops.py", ["--json-errors", "pr-status", "bad repo", "1"])
    assert guided.returncode == 2
    assert "repository must look like" in guided.stderr
    payload = json.loads(guided.stdout)
    assert payload["ok"] is False and payload["exit_code"] == 2
    assert "OWNER/REPO" in payload["hint"]
    assert "bad repo" not in payload["hint"]


def test_publish_check_json_against_a_local_server(tmp_path):
    served = tmp_path / "site"
    served.mkdir()
    (served / "index.html").write_text("hello-page\n", encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        result = run("publish_check.py", ["--json", "--site", base, "index.html", "--contains", "hello-page", "--no-bust"])
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["verdict"] == "ok"
    assert any(row["state"] == "ok" and "hello-page" in row["line"] for row in payload["checks"])

    missing = run("publish_check.py", ["--json"])
    assert missing.returncode == 2
    assert "at least one target" in missing.stderr


def test_py_matrix_json_when_uv_is_missing():
    help_run = run("py_matrix.py", ["--help"])
    assert help_run.returncode == 0
    assert "Exit codes" in help_run.stdout and "--json" in help_run.stdout
    if shutil.which("uv"):
        return
    result = run("py_matrix.py", ["--json", "3.12"])
    assert result.returncode == 2
    assert "uv not found" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False and payload["exit_code"] == 2
    assert "Install uv" in payload["hint"]


def test_pytest_brief_json_runs_a_real_test(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    result = run("pytest_brief.py", ["--json", "--", "test_sample.py"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["exit_code"] == 0
    assert any("passed" in line for line in payload["lines"])


def test_ci_status_json_explains_a_bad_repository_without_network():
    result = run("ci_status.py", ["not a repo", "--json", "--explain"])
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["error"] == "ValueError"
    assert "invalid repository" in payload["errors"][0]["detail"]
    assert "Exit 2" in payload["errors"][0]["hint"]


def test_gh_workflow_rejects_inputs_without_echoing_them():
    secret = "super-secret-token-value"
    result = run("gh_workflow.py", ["o/r", "ci.yml", "--inputs", "{" + secret])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "invalid_argument"
    assert "JSON object" in payload["hint"]
    assert secret not in result.stdout + result.stderr

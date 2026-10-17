"""Offline end-to-end regressions for the scheduled duplicate-files adapter."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "duplicate_files_report.py"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/vendor-lock-reconciliation.yml"

# Fake pinned collector runs as a real child process; its renderer is imported
# separately by the adapter after the CLI's stdout has been written to disk.
FAKE_IDENTITY = '''import os, sys, time

def render_duplicates_markdown(report, top=30):
    if os.getenv("FAKE_RENDER_FAIL") == "1":
        raise RuntimeError("renderer failed")
    return "### Duplicate files (fake)\\n"

if __name__ == "__main__":
    sys.stdout.write(os.getenv("FAKE_JSON", ""))
    sys.stdout.flush()
    sys.stderr.write("collector diagnostic\\n")
    sys.stderr.flush()
    if os.getenv("FAKE_HANG") == "1":
        time.sleep(60)
    sys.exit(int(os.getenv("FAKE_EXIT", "0")))
'''


@pytest.fixture
def execute(tmp_path):
    identity = tmp_path / ".gh-identity" / "gh_identity.py"
    identity.parent.mkdir()
    identity.write_text(FAKE_IDENTITY, encoding="utf-8")
    output = tmp_path / "reports" / "duplicate-files.json"
    summary = tmp_path / "step-summary.md"

    def invoke(payload, collector_exit=0, render_fail=False, hang=False, timeout=900):
        env = os.environ.copy()
        env.update(FAKE_JSON=payload, FAKE_EXIT=str(collector_exit),
                   FAKE_RENDER_FAIL="1" if render_fail else "0",
                   FAKE_HANG="1" if hang else "0")
        result = subprocess.run(
            [sys.executable, "-S", str(SCRIPT), "--identity", str(identity),
             "--output", str(output), "--summary", str(summary), "--owner", "fake",
             "--timeout", str(timeout)],
            env=env, capture_output=True, text=True, check=False,
        )
        return result, output, output.with_suffix(".md"), summary
    return invoke


def payload(complete, errors=None):
    return json.dumps({"schema": "gh-identity-duplicate-files/1", "complete": complete,
                       "errors": [] if errors is None else errors, "groups": [], "counts": {}})


def test_complete_success(execute):
    result, raw, markdown, summary = execute(payload(True))
    assert result.returncode == 0
    assert json.loads(raw.read_text())["complete"] is True
    assert markdown.read_text() == summary.read_text().strip() + "\n"
    assert "### Duplicate files" in summary.read_text()
    assert "INCOMPLETE" not in summary.read_text()


def test_incomplete_preserves_json_and_collector_status(execute):
    errors = [{"repository": "myon-bioinformatics/a", "detail": "truncated_tree"},
              {"repository": "myon-bioinformatics/b", "detail": "http_403"}]
    result, raw, markdown, summary = execute(payload(False, errors), collector_exit=1)
    assert result.returncode == 1
    assert json.loads(raw.read_text())["errors"] == errors
    assert "INCOMPLETE" in summary.read_text()
    assert "myon-bioinformatics/a" in summary.read_text()
    assert "truncated_tree" in markdown.read_text()
    assert "http_403" in summary.read_text()
    assert "Collector exit status: `1`" in markdown.read_text()
    assert raw.with_suffix(".stderr.log").read_text() == "collector diagnostic\n"


def test_wall_clock_timeout_keeps_partial_output_and_diagnostics(execute):
    partial = '{"schema": "gh-identity-duplicate-files/1",'
    result, raw, markdown, summary = execute(partial, hang=True, timeout=1)
    assert result.returncode == 124
    assert raw.read_text() == partial
    assert raw.with_suffix(".stderr.log").read_text() == "collector diagnostic\n"
    assert "INCOMPLETE" in summary.read_text()
    assert "wall-clock timeout" in markdown.read_text()
    assert "Collector exit status: `124`" in markdown.read_text()


@pytest.mark.parametrize("timeout", [0, -1])
def test_nonpositive_timeout_is_rejected_before_collection(execute, timeout):
    result, raw, _, _ = execute(payload(True), timeout=timeout)
    assert result.returncode == 2
    assert "--timeout must be greater than zero" in result.stderr
    assert not raw.exists()


@pytest.mark.parametrize("invalid", ["", "{bad json", "[]"])
def test_malformed_json_fails_and_keeps_raw_evidence(execute, invalid):
    result, raw, markdown, summary = execute(invalid)
    assert result.returncode != 0
    assert raw.read_text() == invalid
    assert "INCOMPLETE" in summary.read_text()
    assert "Report JSON" in markdown.read_text()


def test_markdown_render_failure_keeps_json_and_reports_error(execute):
    valid = payload(True)
    result, raw, markdown, summary = execute(valid, render_fail=True)
    assert result.returncode == 1
    assert raw.read_text() == valid
    assert "Markdown report generation failed" in summary.read_text()
    assert "renderer failed" in markdown.read_text()


def test_legacy_pin_missing_complete_rejected(execute):
    legacy = json.dumps({"errors": [], "groups": [], "counts": {}})
    result, raw, markdown, summary = execute(legacy)
    assert result.returncode == 1
    assert raw.read_text() == legacy
    assert "lacks the #60 completeness contract" in summary.read_text()
    assert "INCOMPLETE" in markdown.read_text()
    assert "### Duplicate files" not in summary.read_text()


def test_status_inconsistency_is_error(execute):
    assert execute(payload(False, [{"repository": "fake/a", "detail": "truncated_tree"}]))[0].returncode == 1
    assert execute(payload(True), collector_exit=3)[0].returncode == 3


def test_workflow_runs_offline_regressions_and_uploads_even_on_failure():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "tests/test_duplicate_files_report.py" in workflow
    assert 'GH_IDENTITY_SHA: a85022b88ca808dec7ef497ead1e3469906010a4' in workflow
    job = workflow.split("\n  duplicate-files:\n", 1)[1]
    assert "duplicate_files_report.py" in job
    assert "if: always()" in job.split("- name: Upload duplicate-files report", 1)[1]
    assert "reports/duplicate-files.json" in job
    assert "reports/duplicate-files.md" in job
    assert "reports/duplicate-files.stderr.log" in job
    assert "--timeout 900" in job
    assert "actions/checkout@v7" in job


def test_weekly_scan_event_and_permission_boundaries():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": "30 0 * * 1"}]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["duplicate-files"]
    assert job["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert "needs" not in job  # A separate reconciliation failure cannot suppress the scan.
    assert "permissions" not in job  # Inherit the read-only boundary.
    assert int(job["timeout-minutes"]) * 60 > 900  # Leave time to render and upload evidence.
    for step in job["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] == "false"
    upload = job["steps"][-1]
    assert upload["if"] == "always()"
    assert upload["with"]["retention-days"] == "14"

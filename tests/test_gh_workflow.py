import base64
import json
import subprocess
from types import SimpleNamespace

import pytest

import gh_workflow as gw

SHA = "a" * 40
TARGET = "b" * 40
YAML = "name: CI\non:\n  workflow_dispatch:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"


@pytest.fixture
def fake_gh(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        endpoint = next(a for a in argv if a.startswith("repos/"))
        method = argv[argv.index("--method") + 1]
        if method == "PUT":
            data = None
        elif method == "POST":
            data = {"workflow_run_id": 99}
        elif endpoint.endswith("/actions/runs/99"):
            data = {"head_sha": TARGET, "status": "queued", "conclusion": None}
        elif "/contents/" in endpoint:
            data = {"encoding": "base64", "content": base64.b64encode(YAML.encode()).decode()}
        elif "/commits/" in endpoint:
            data = {"sha": SHA if endpoint.endswith("/main") else TARGET}
        elif "/actions/workflows/" in endpoint:
            data = {"id": 7, "path": ".github/workflows/codeql.yml", "state": "disabled_inactivity"}
        else:
            data = {"default_branch": "main"}
        return SimpleNamespace(returncode=0, stdout="" if data is None else json.dumps(data), stderr="")
    monkeypatch.setattr(gw.subprocess, "run", run)
    return calls


def test_plan_is_read_only_and_pins_yaml_reads(fake_gh):
    result = gw.execute_workflow("o/r", "codeql.yml", "feature/foo")
    assert result["status"] == "planned"
    assert result["target_sha"] == TARGET
    assert result["default_sha"] == SHA
    assert all(a[a.index("--method") + 1] == "GET" for a, _ in fake_gh)
    assert any("feature%2Ffoo" in a[-1] for a, _ in fake_gh)
    assert sum("/contents/" in a[-1] for a, _ in fake_gh) == 2


def test_enable_dispatch_and_exact_run_receipt(fake_gh):
    result = gw.execute_workflow("o/r", "codeql.yml", "feature", apply=True,
                                 inputs={"name": "@secret-file", "flag": False})
    assert result["status"] == "dispatched"
    assert result["enabled"]
    assert result["run_id"] == 99
    assert result["run_url"] == "https://github.com/o/r/actions/runs/99"
    assert result["sha_matches"]
    assert result["conclusion"] is None
    mutations = [(a, k) for a, k in fake_gh if a[a.index("--method") + 1] != "GET"]
    assert [a[a.index("--method") + 1] for a, k in mutations] == ["PUT", "POST"]
    assert json.loads(mutations[1][1]["input"])["inputs"] == {"name": "@secret-file", "flag": False}
    assert "@secret-file" not in json.dumps(result)
    for argv, kw in fake_gh:
        assert argv[:2] == ["gh", "api"] and "--hostname" in argv
        assert kw["shell"] is False and kw["timeout"] == 30
        assert kw["env"]["GH_PROMPT_DISABLED"] == "1"


@pytest.mark.parametrize("state", ["active", "disabled_manually"])
def test_enable_only_if_needed(fake_gh, monkeypatch, state):
    original = gw.subprocess.run
    def run(argv, **kwargs):
        obj = original(argv, **kwargs)
        if '"disabled_inactivity"' in obj.stdout:
            obj.stdout = obj.stdout.replace('"disabled_inactivity"', json.dumps(state))
        return obj
    monkeypatch.setattr(gw.subprocess, "run", run)
    result = gw.execute_workflow("o/r", "codeql.yml", "feature", apply=True)
    assert result["enabled"] == (state != "active")


@pytest.mark.parametrize("yaml,expected", [
    ("on:\n  workflow_dispatch:\n  push:\n", True),
    ('"on":\r\n  "workflow_dispatch": # manual\r\n', True),
    ("on: [push, workflow_dispatch]\n", True),
    ("on: workflow_dispatch\n", True),
    ("on:\n  push:\n  schedule:\n    - cron: '0 0 * * *'\n", False),
    ("on: [push, pull_request]\n", False),
    ("on:\n  push:\njobs:\n  workflow_dispatch:\n", False),
    ("# on: workflow_dispatch\non: push\n", False),
    ("on: {workflow_dispatch: {}}\n", None),
    ("on: *events\n", None),
    ("on: push\non: workflow_dispatch\n", None),
    ("name: |\n  on: workflow_dispatch\n", None),
    ("on:\n  push:\n    paths:\n      - workflow_dispatch\n", False),
    ("on:\n  workflow_dispatch:\n  workflow_dispatch:\n", None),
])
def test_trigger_detection(yaml, expected):
    assert gw._dispatch_trigger(yaml) is expected


@pytest.mark.parametrize("trigger", [False, None])
def test_missing_or_unknown_trigger_blocks_before_enable(fake_gh, monkeypatch, trigger):
    monkeypatch.setattr(gw, "_dispatch_trigger", lambda text: trigger)
    result = gw.execute_workflow("o/r", "codeql.yml", apply=True)
    assert result["status"] == "blocked"
    assert result["reasons"]
    assert all(a[a.index("--method") + 1] == "GET" for a, _ in fake_gh)


@pytest.mark.parametrize("failure,code", [(FileNotFoundError(), "gh_not_found"),
                                         (subprocess.TimeoutExpired("gh", 30), "timeout")])
def test_process_failures(monkeypatch, failure, code):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(gw.subprocess, "run", fail)
    with pytest.raises(gw.WorkflowError) as error:
        gw.inspect_workflow("o/r", "codeql.yml")
    assert error.value.code == code


@pytest.mark.parametrize("status,code", [(401, "authentication_required"),
    (403, "permission_or_rate_limit"), (404, "not_found_or_inaccessible"), (422, "dispatch_rejected")])
def test_api_failures_do_not_leak_stderr(monkeypatch, status, code):
    monkeypatch.setattr(gw.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr=f"gh: private-token HTTP {status}"))
    with pytest.raises(gw.WorkflowError, match=code) as error:
        gw.inspect_workflow("o/r", "codeql.yml")
    assert "private-token" not in str(error.value)


def test_dispatch_timeout_records_partial_enable_and_never_retries(fake_gh, monkeypatch):
    original = gw.subprocess.run
    def run(argv, **kwargs):
        if "POST" in argv:
            fake_gh.append((argv, kwargs))
            raise subprocess.TimeoutExpired(argv, 30)
        return original(argv, **kwargs)
    monkeypatch.setattr(gw.subprocess, "run", run)
    result = gw.execute_workflow("o/r", "codeql.yml", apply=True)
    assert result["status"] == "mutation_uncertain"
    assert result["enabled"] and result["failed_stage"] == "dispatch"
    assert sum("POST" in a for a, _ in fake_gh) == 1


def test_legacy_empty_response_does_not_guess_latest_run(fake_gh, monkeypatch):
    original = gw.subprocess.run
    def run(argv, **kwargs):
        obj = original(argv, **kwargs)
        if "POST" in argv:
            obj.stdout = ""
        return obj
    monkeypatch.setattr(gw.subprocess, "run", run)
    result = gw.execute_workflow("o/r", "codeql.yml", apply=True)
    assert result["status"] == "dispatched_run_unresolved" and result["run_id"] is None
    assert not any("/actions/runs/" in a[-1] for a, _ in fake_gh)


def test_branch_movement_is_reported(fake_gh, monkeypatch):
    result = gw.execute_workflow("o/r", "codeql.yml", "main", apply=True)
    assert result["status"] == "dispatched_ref_changed"
    assert result["executed_sha"] == TARGET and not result["sha_matches"]


def test_run_observation_failure_preserves_dispatch(fake_gh, monkeypatch):
    original = gw.subprocess.run
    def run(argv, **kwargs):
        if "/actions/runs/99" in argv[-1]:
            raise subprocess.TimeoutExpired(argv, 30)
        return original(argv, **kwargs)
    monkeypatch.setattr(gw.subprocess, "run", run)
    result = gw.execute_workflow("o/r", "codeql.yml", apply=True)
    assert result["status"] == "observation_failed"
    assert result["run_id"] == 99 and result["failed_stage"] == "observe_run"


@pytest.mark.parametrize("repo,workflow,ref", [
    ("https://github.com/o/r", "ci.yml", None), ("o/r", "../ci.yml", None),
    ("o/r", "ci.yml", "--help"), ("o/r", "ci.yml", "a\x00b"),
])
def test_validation_precedes_network(fake_gh, repo, workflow, ref):
    with pytest.raises(ValueError):
        gw.inspect_workflow(repo, workflow, ref)
    # ref is checked after reading default branch; all calls are still GET.
    assert all(a[a.index("--method") + 1] == "GET" for a, _ in fake_gh)


def test_cli_jsonl_and_failure_exit(fake_gh, tmp_path, capsys):
    path = tmp_path / "nested" / "receipts.jsonl"
    assert gw.main(["o/r", "codeql.yml", "--receipt", str(path)]) == 0
    assert gw.main(["bad", "codeql.yml", "--receipt", str(path)]) == 1
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["status"] for r in rows] == ["planned", "preflight_failed"]
    assert len(capsys.readouterr().out.splitlines()) == 2


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_timeout(fake_gh, timeout):
    with pytest.raises(ValueError):
        gw.inspect_workflow("o/r", "ci.yml", timeout=timeout)
    assert not fake_gh


@pytest.mark.parametrize("data", [None, [], {}, {"default_branch": None}])
def test_malformed_repository_response(monkeypatch, data):
    monkeypatch.setattr(gw, "_api", lambda *a, **k: data)
    with pytest.raises(gw.WorkflowError, match="invalid_repository_response"):
        gw.inspect_workflow("o/r", "ci.yml")


def test_actual_cli_missing_gh_writes_receipt(tmp_path):
    from pathlib import Path
    import os
    import sys
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    receipt = tmp_path / "receipt.jsonl"
    proc = subprocess.run([sys.executable, str(Path(gw.__file__).resolve()),
                           "o/r", "ci.yml", "--receipt", str(receipt)],
                          capture_output=True, text=True, env=env, timeout=5)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["error"] == "gh_not_found"
    assert json.loads(receipt.read_text())["error"] == "gh_not_found"

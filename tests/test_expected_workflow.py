import json
from unittest.mock import Mock

import pytest

import ci_status


SHA = "a" * 40
ARGS = ["--verify-workflow", "gh_identity", "ci.yml", "--head-sha", SHA,
        "--event", "pull_request", "--run-id", "12", "--attempt", "1"]
IDENTITY = dict(run_id=12, attempt=1, head_sha=SHA)


@pytest.fixture
def evidence(monkeypatch):
    ghi = Mock()
    ghi.workflow.return_value = dict(id=34, path=".github/workflows/ci.yml")
    ghi.run.return_value = dict(**IDENTITY, workflow_id=34, event="pull_request",
                                status="completed", conclusion="success")
    rows = [
        dict(**IDENTITY, job_id=56, name="test", status="completed", conclusion="success"),
        dict(**IDENTITY, job_id=57, name="optional", status="completed", conclusion="skipped"),
    ]
    ghi.jobs.return_value = dict(complete=True, count=len(rows), run_id=12, attempt=1, jobs=rows)
    monkeypatch.setattr(ci_status, "ghi", ghi)
    return ghi


def test_exact_success_may_include_unrequired_skips(evidence, capsys):
    assert ci_status.main(ARGS + ["--require-job", "test"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "success"
    assert result["jobs"][0]["job_id"] == 56
    name = "myon-bioinformatics/gh_identity"
    evidence.workflow.assert_called_once_with(name, "ci.yml")
    evidence.run.assert_called_once_with(name, 12, attempt=1)
    evidence.jobs.assert_called_once_with(name, 12, attempt=1)


@pytest.mark.parametrize(("field", "received"), [
    ("event", "push"), ("attempt", 2), ("run_id", 13), ("head_sha", "b" * 40),
    ("workflow_id", 35),
])
def test_wrong_run_never_certified(evidence, capsys, field, received):
    evidence.run.return_value[field] = received
    assert ci_status.main(ARGS) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "run_identity_mismatch"
    evidence.jobs.assert_not_called()


def test_exact_failed_attempt(evidence, capsys):
    evidence.run.return_value["conclusion"] = "failure"
    assert ci_status.main(ARGS) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "run_did_not_pass"


@pytest.mark.parametrize(("case", "reason"), [
    ("wrong_job_head", "job_identity_mismatch"),
    ("incomplete", "incomplete_jobs"),
    ("empty", "incomplete_jobs"),
    ("missing_job", "required_job_missing_or_ambiguous"),
    ("duplicate_job", "required_job_missing_or_ambiguous"),
    ("required_skip", "required_job_success_unverified"),
    ("only_skips", "no_successful_job_observed"),
    ("optional_failure", "unexpected_job_conclusion"),
    ("optional_unknown", "unexpected_job_conclusion"),
    ("api_error", "observation_error"),
])
def test_missing_or_inconsistent_evidence_not_green(evidence, capsys, case, reason):
    jobs = evidence.jobs.return_value
    options = []
    if case == "wrong_job_head":
        jobs["jobs"][0]["head_sha"] = "b" * 40
    elif case == "incomplete":
        jobs["complete"] = False
    elif case == "empty":
        jobs.update(jobs=[], count=0)
    elif case == "missing_job":
        options = ["--require-job", "absent"]
    elif case == "duplicate_job":
        jobs["jobs"][1]["name"] = "test"
        options = ["--require-job", "test"]
    elif case == "required_skip":
        options = ["--require-job", "optional"]
    elif case == "only_skips":
        jobs["jobs"][0]["conclusion"] = "skipped"
    elif case in ("optional_failure", "optional_unknown"):
        jobs["jobs"][1]["conclusion"] = "failure" if case == "optional_failure" else "unrecognized"
    elif case == "api_error":
        evidence.jobs.side_effect = RuntimeError("not forwarded")
    assert ci_status.main(ARGS + options) == 2
    result = json.loads(capsys.readouterr().out)
    assert (result["state"], result["reason"]) == ("unknown", reason)

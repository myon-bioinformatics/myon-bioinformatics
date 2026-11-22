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
    ghi.step_url_from_jobs = ci_status.ghi.step_url_from_jobs
    ghi.resolve_ref.return_value = {"sha": SHA}
    ghi.workflow.return_value = dict(id=34, path=".github/workflows/ci.yml")
    ghi.run.return_value = dict(**IDENTITY, workflow_id=34, event="pull_request",
                                status="completed", conclusion="success")
    rows = [
        dict(**IDENTITY, job_id=56, name="test", status="completed", conclusion="success",
             steps=[dict(name="unit", number=4, status="completed", conclusion="success")]),
        dict(**IDENTITY, job_id=57, name="optional", status="completed", conclusion="skipped"),
    ]
    ghi.jobs.return_value = dict(schema="gh-identity-jobs/1", repository="myon-bioinformatics/gh_identity",
                                 complete=True, count=len(rows), run_id=12, attempt=1, jobs=rows)
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


def test_resolve_ref_and_report_step_permalink(evidence, capsys):
    args = list(ARGS)
    args[3:5] = ["--ref", "feature/one"]
    assert ci_status.main(args + ["--require-step", "test", "unit"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ref_sha"] == result["head_sha"] == SHA
    evidence.resolve_ref.assert_called_once_with("myon-bioinformatics/gh_identity", "feature/one")
    assert result["steps"] == [dict(job="test", job_id=56, name="unit", number=4, status="completed",
                                    conclusion="success", url="https://github.com/myon-bioinformatics/"
                                    "gh_identity/actions/runs/12/job/56#step:4")]


def test_moved_ref_excludes_old_cancelled_run(evidence, capsys):
    evidence.resolve_ref.return_value = {"sha": "b" * 40}
    evidence.run.return_value["conclusion"] = "cancelled"
    assert ci_status.main(ARGS + ["--ref", "main"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "ref_sha_mismatch"
    evidence.run.assert_not_called()


def test_check_observation_resolves_explicit_ref_once(evidence, capsys):
    evidence.checks_for_sha.return_value = {"complete": True, "checks": []}
    assert ci_status.main(["gh_identity", "--ref", "feature/one", "--json"]) == 0
    row, = json.loads(capsys.readouterr().out)["repositories"]
    assert (row["target"], row["head_sha"]) == ("feature/one", SHA)
    evidence.checks_for_sha.assert_called_once_with("myon-bioinformatics/gh_identity", SHA)
    evidence.resolve_ref.assert_called_once_with("myon-bioinformatics/gh_identity", "feature/one")


def test_pr_and_ref_are_exclusive(evidence):
    with pytest.raises(SystemExit) as exc:
        ci_status.main(["gh_identity", "--ref", "main", "--pr", "12"])
    assert exc.value.code == 2
    evidence.resolve_ref.assert_not_called()


@pytest.mark.parametrize(("case", "exit_code", "reason"), [
    ("missing", 2, "required_step_missing_or_ambiguous"),
    ("duplicate_name", 2, "required_step_missing_or_ambiguous"),
    ("duplicate_number", 2, "observation_error"),
    ("skipped", 2, "required_step_success_unverified"),
    ("in_progress", 2, "required_step_success_unverified"),
    ("failure", 1, "required_step_did_not_pass"),
])
def test_required_step_is_scoped_to_job(evidence, capsys, case, exit_code, reason):
    job, other = evidence.jobs.return_value["jobs"]
    step = job["steps"][0]
    other["steps"] = [dict(step)]
    if case == "missing":
        job["steps"] = []
    elif case == "duplicate_name":
        job["steps"].append(dict(step, number=5))
    elif case == "duplicate_number":
        job["steps"].append(dict(step, name="other"))
    elif case == "in_progress":
        step.update(status=case, conclusion=None)
    else:
        step["conclusion"] = case
    assert ci_status.main(ARGS + ["--require-step", "test", "unit"]) == exit_code
    assert json.loads(capsys.readouterr().out)["reason"] == reason


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

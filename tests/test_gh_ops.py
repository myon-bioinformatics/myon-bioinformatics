import base64
import importlib.util
import io
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT
spec = importlib.util.spec_from_file_location("gh_ops", SCRIPTS / "gh_ops.py")
gh_ops = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules["gh_ops"] = gh_ops
spec.loader.exec_module(gh_ops)

gh_identity = gh_ops.gh_identity

REPO = "octo/demo"
HEAD = "ecfd0ba1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7"
OTHER = "ecfd0ba9999999999999999999999999999999aa"


def reply(data=None, status=200, link=None):
    return gh_ops.Response(status, data, {"link": link} if link else {})


class Stub:
    """Transport stub: routes (METHOD, path) to a reply, a reply list, or a callable."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        self.calls.append({"method": method, "path": parts.path, "query": urllib.parse.parse_qs(parts.query),
                           "body": body, "headers": headers})
        route = self.routes[(method, parts.path)]
        if callable(route):
            return route(parts, body)
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route

    @property
    def methods(self):
        return [call["method"] for call in self.calls]


def client_for(routes, token="t0ken"):
    stub = Stub(routes)
    return gh_ops.Client(token=token, transport=stub), stub


def check_run(name, conclusion="success", status="completed", annotations=0, run_id=1):
    return {"id": run_id, "name": name, "status": status, "conclusion": conclusion, "head_sha": HEAD,
            "output": {"annotations_count": annotations}}


def pr_payload(**overrides):
    data = {"state": "open", "merged": False, "mergeable": True, "mergeable_state": "clean", "draft": False,
            "head": {"sha": HEAD, "ref": "feature"}, "base": {"ref": "main"}, "commits": 2, "changed_files": 3,
            "additions": 10, "deletions": 2, "html_url": "https://github.com/octo/demo/pull/11", "body": ""}
    data.update(overrides)
    return data


# --- issue-comments ---------------------------------------------------------------

def comment(index, author="alice", body=None):
    return {"id": 100 + index, "created_at": f"2026-09-2{index % 10}T00:00:00Z", "user": {"login": author},
            "body": body if body is not None else f"comment {index}", "html_url": f"https://x/{index}"}


def comments_routes(total=150, since=None):
    items = [comment(i, author="bot" if i % 2 else "alice") for i in range(total)]
    items[0]["body"] = "line one\n\nline   two " + "x" * 300

    def pages(parts, body):
        query = urllib.parse.parse_qs(parts.query)
        if since is not None:
            assert query["since"] == [since]
        page = int(query.get("page", ["1"])[0])
        chunk = items[(page - 1) * 100: page * 100]
        link = None
        if page * 100 < total:
            link = f'<https://api.github.com/repos/octo/demo/issues/24/comments?per_page=100&page={page + 1}>; rel="next"'
        return reply(chunk, link=link)

    return {
        ("GET", "/repos/octo/demo/issues/24"): reply({"title": "Roadmap", "state": "open", "body": "b" * 42}),
        ("GET", "/repos/octo/demo/issues/24/comments"): pages,
    }


def test_issue_comments_paginates_and_digests_without_full_bodies():
    client, stub = client_for(comments_routes())
    result = gh_ops.issue_comments(REPO, 24, client=client, preview=20)
    assert result["total_comments"] == 150
    assert len(result["comments"]) == 150
    first = result["comments"][0]
    assert first["preview"] == "line one line two x…"
    assert first["chars"] == len("line one\n\nline   two " + "x" * 300)
    assert result["shown"] == {}
    assert result["body_chars"] == 42 and result["is_pull_request"] is False
    assert [call["query"].get("page") for call in stub.calls[1:]] == [None, ["2"]]
    assert set(stub.methods) == {"GET"}


def test_issue_comments_filters_show_and_save(tmp_path):
    client, _ = client_for(comments_routes(total=5, since="2026-09-20T00:00:00Z"))
    target = tmp_path / "out" / "comments.json"
    result = gh_ops.issue_comments(REPO, 24, client=client, since="2026-09-20T00:00:00Z", author="bot",
                                   last=1, show=(2,), save=str(target))
    assert [row["index"] for row in result["comments"]] == [3]
    assert result["shown"] == {2: "comment 2"}
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["issue"]["title"] == "Roadmap" and len(saved["comments"]) == 5


def test_issue_comments_rejects_unknown_show_index():
    client, _ = client_for(comments_routes(total=3))
    with pytest.raises(gh_ops.GhOpsError, match="no comment with index 7"):
        gh_ops.issue_comments(REPO, 24, client=client, show=(7,))


def test_issue_comments_cli_prints_one_line_per_comment(capsys):
    client, _ = client_for(comments_routes(total=3))
    assert gh_ops.main(["issue-comments", REPO, "24", "--show", "1"], client=client) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == 'issue #24 open "Roadmap" -- 3 comment(s), body 42 chars'
    assert out[1].startswith("[0] 2026-09-20T00:00:00Z alice ")
    assert out[3].startswith("[2] ")
    assert out[4:] == ["--- [1] ---", "comment 1"]




def test_ghi_pr_contract_matches_legacy_pr_status_identity(monkeypatch):
    payload = pr_payload()
    client, _ = client_for({("GET", "/repos/octo/demo/pulls/11"): reply(payload)})
    legacy = gh_ops.pr_status(REPO, 11, client=client)
    def fake_request(method, path, payload=None, transport="auto", timeout=30, mutating=False):
        assert method == "GET"
        assert path == "repos/octo/demo/pulls/11"
        return pr_payload()
    monkeypatch.setattr(gh_identity, "request", fake_request)
    current = gh_identity.pr(REPO, 11)
    assert current["head_sha"] == legacy["head_sha"]
    assert current["head_ref"] == legacy["head_ref"]
    assert current["base_ref"] == legacy["base_ref"]
    assert current["state"] == legacy["state"]
    assert current["draft"] == legacy["draft"]
    assert current["merged"] == legacy["merged"]


@pytest.mark.parametrize("conclusions,state", [
    (("success", "skipped"), "green"),
    (("skipped", "skipped"), "failed"),
    (("neutral", "neutral"), "failed"),
    (("success", "failure"), "failed"),
    (("success", "cancelled"), "failed"),
])
def test_ghi_checks_contract_matches_compatibility_shape(monkeypatch, conclusions, state):
    runs = [check_run("unit", conclusion=conclusions[0], annotations=3),
            check_run("lint", conclusion=conclusions[1], run_id=2)]
    client, _ = client_for({("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply({"check_runs": runs})})
    legacy = gh_ops.checks_status(REPO, HEAD, min_checks=2, client=client)
    def fake_request(method, path, payload=None, transport="auto", timeout=30, mutating=False):
        assert method == "GET"
        return {"total_count": len(runs), "check_runs": runs}
    monkeypatch.setattr(gh_identity, "request", fake_request)
    current = gh_identity.checks_for_sha(REPO, HEAD, min_checks=2)
    assert current["sha"] == legacy["sha"]
    assert current["state"] == legacy["state"] == state
    assert current["count"] == legacy["total"] == 2
    assert current["checks"][0]["annotations_count"] == legacy["runs"][0]["annotations_count"] == 3

# --- pr-status / runs / workflow-state ----------------------------------------------

def test_pr_status_summary(capsys):
    client, _ = client_for({("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload())})
    assert gh_ops.main(["pr-status", REPO, "11"], client=client) == 0
    assert capsys.readouterr().out.strip() == (
        "#11 open draft=False merged=False mergeable=True (clean) head=ecfd0ba1c2d3 feature -> main "
        "commits=2 files=3 +10/-2"
    )


def test_gh_ops_loads_adjacent_ghi_without_scripts_on_sys_path(tmp_path):
    code = (
        "import importlib.util, pathlib, sys; "
        "p=pathlib.Path(r'" + str(SCRIPTS / "gh_ops.py") + "'); "
        "spec=importlib.util.spec_from_file_location('isolated_gh_ops', p); "
        "m=importlib.util.module_from_spec(spec); sys.modules['isolated_gh_ops']=m; "
        "spec.loader.exec_module(m); "
        "print(m.gh_identity.__file__)"
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    done = subprocess.run([sys.executable, "-S", "-c", code], cwd=tmp_path,
                          env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().replace("\\", "/").endswith("vendor/gh_identity.py")


def test_pr_head_identity_uses_ghi_comparison_contract():
    client, stub = client_for({("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload())})
    result = gh_ops.compare_pr_head_identity(REPO, 11, {"sha": HEAD.upper()}, client=client)
    assert result["schema"] == "gh-identity-comparison/1"
    assert result["comparable"] is True and result["same"] is True
    assert result["local_sha"] == HEAD and result["remote_sha"] == HEAD
    assert result["head_ref"] == "feature" and result["base_ref"] == "main"
    assert stub.methods == ["GET"]


def test_pr_head_identity_preserves_unknown_and_mismatch():
    for local, expected in [({"sha": None}, None), ({"sha": OTHER}, False)]:
        client, _ = client_for({("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload())})
        result = gh_ops.compare_pr_head_identity(REPO, 11, local, client=client)
        assert result["same"] is expected
        assert result["comparable"] is (expected is not None)



def observe_routes(*, checks=None, issue_comments=(), reviews=(), review_comments=(), pr=None):
    return {
        ("GET", "/repos/octo/demo/pulls/11"): reply(pr or pr_payload()),
        ("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply(
            {"check_runs": checks if checks is not None else [check_run("unit")]}),
        ("GET", "/repos/octo/demo/issues/11/comments"): reply(list(issue_comments)),
        ("GET", "/repos/octo/demo/pulls/11/reviews"): reply(list(reviews)),
        ("GET", "/repos/octo/demo/pulls/11/comments"): reply(list(review_comments)),
    }


def test_pr_observe_binds_green_checks_and_activity_to_current_head(capsys):
    issue = {"id": 501, "updated_at": "2026-10-04T10:00:00Z"}
    review = {"id": 601, "submitted_at": "2026-10-04T10:01:00Z", "state": "APPROVED"}
    inline = {"id": 701, "updated_at": "2026-10-04T10:02:00Z"}
    client, stub = client_for(observe_routes(
        checks=[check_run("unit"), check_run("lint", conclusion="skipped", run_id=2)],
        issue_comments=[issue], reviews=[review], review_comments=[inline]))
    result = gh_ops.pr_observe(REPO, 11, min_checks=2, client=client)
    assert result["schema"] == "gh-ops-pr-observation/1"
    assert result["head_sha"] == HEAD and result["checks"]["sha"] == HEAD
    assert result["checks"]["state"] == "green" and result["checks"]["ok"] is True
    assert result["activity"]["issue_comments"] == {
        "count": 1, "latest_id": 501, "latest_at": "2026-10-04T10:00:00Z"}
    assert result["activity"]["reviews"]["latest_id"] == 601
    assert result["activity"]["reviews"]["states"] == {"APPROVED": 1}
    assert result["activity"]["review_comments"]["latest_id"] == 701
    assert set(stub.methods) == {"GET"}
    assert gh_ops.main(["pr-observe", REPO, "11", "--min", "2"], client=client_for(observe_routes(
        checks=[check_run("unit"), check_run("lint", conclusion="skipped", run_id=2)],
        issue_comments=[issue], reviews=[review], review_comments=[inline]))[0]) == 0
    assert "checks=green" in capsys.readouterr().out


def test_check_summary_delegates_classification_to_ghi(monkeypatch):
    calls = []
    real = gh_ops.gh_identity.summarize_checks
    def wrapped(rows, expected_count, min_checks=1):
        calls.append((rows, expected_count, min_checks))
        return real(rows, expected_count, min_checks)
    monkeypatch.setattr(gh_ops.gh_identity, "summarize_checks", wrapped)
    runs = [check_run("unit", annotations=3)]
    result = gh_ops._summarize_checks(runs, 1)
    assert result["ok"] is True
    assert result["runs"][0]["head_sha"] == HEAD
    assert result["runs"][0]["annotations_count"] == 3
    assert calls == [(runs, 1, 1)]


def test_pr_observe_zero_checks_is_pending_not_green():
    client, _ = client_for(observe_routes(checks=[]))
    result = gh_ops.pr_observe(REPO, 11, client=client)
    assert result["ok"] is True
    assert result["checks"]["state"] == "pending"
    assert result["checks"]["ok"] is False
    assert "only 0 check run" in result["checks"]["reason"]


def test_pr_observe_rejects_snapshot_if_head_changes_during_reads():
    changed = pr_payload(head={"sha": OTHER, "ref": "feature"})
    routes = observe_routes()
    routes[("GET", "/repos/octo/demo/pulls/11")] = [reply(pr_payload()), reply(changed)]
    client, stub = client_for(routes)
    result = gh_ops.pr_observe(REPO, 11, client=client)
    assert result["ok"] is False and result["stale"] is True
    assert result["observed_head_sha"] == HEAD and result["current_head_sha"] == OTHER
    assert result["identity"]["schema"] == "gh-identity-comparison/1"
    assert result["identity"]["comparable"] is True and result["identity"]["same"] is False
    assert "discard this snapshot" in result["reason"]
    assert stub.methods.count("GET") == 6


def observation(**overrides):
    data = {
        "ok": True, "schema": "gh-ops-pr-observation/1", "repo": REPO, "number": 11,
        "state": "open", "draft": True, "merged": False, "head_sha": HEAD,
        "checks": {"state": "pending"},
        "activity": {
            "issue_comments": {"count": 1, "latest_id": 1, "latest_at": "a"},
            "reviews": {"count": 0, "latest_id": None, "latest_at": None},
            "review_comments": {"count": 0, "latest_id": None, "latest_at": None},
        },
    }
    data.update(overrides)
    return data


def test_pr_observation_diff_emits_meaningful_events():
    before = observation()
    after = observation(state="closed", draft=False, merged=True, head_sha=OTHER,
                        checks={"state": "green"},
                        activity={
                            "issue_comments": {"count": 2, "latest_id": 2, "latest_at": "b"},
                            "reviews": {"count": 1, "latest_id": 3, "latest_at": "c"},
                            "review_comments": {"count": 0, "latest_id": None, "latest_at": None},
                        })
    result = gh_ops.pr_observation_diff(before, after)
    kinds = [event["type"] for event in result["events"]]
    assert result["changed"] is True
    assert kinds == ["head_changed", "state_changed", "draft_changed", "merged",
                     "ci_became_green", "issue_comments_changed", "reviews_changed"]


def test_pr_observation_diff_identical_is_quiet_and_rejects_wrong_pr():
    before = observation()
    result = gh_ops.pr_observation_diff(before, json.loads(json.dumps(before)))
    assert result["ok"] is True and result["changed"] is False and result["events"] == []
    with pytest.raises(gh_ops.GhOpsError, match="different pull requests"):
        gh_ops.pr_observation_diff(before, observation(number=12))


def test_pr_diff_cli_reads_snapshots_offline(tmp_path, capsys):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(observation()), encoding="utf-8")
    after.write_text(json.dumps(observation(checks={"state": "failed"})), encoding="utf-8")
    assert gh_ops.main(["pr-diff", str(before), str(after)]) == 0
    assert "ci_failed" in capsys.readouterr().out


def test_issue_comment_dry_run_verifies_pr_and_never_posts():
    client, stub = client_for({("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload())})
    result = gh_ops.issue_comment_create(REPO, 11, body="hello", client=client)
    assert result["ok"] is True and result["dry_run"] is True and result["posted"] is False
    assert result["head_sha"] == HEAD and result["would_post"]["body"] == {"body": "hello"}
    assert stub.methods == ["GET"]


def test_issue_comment_write_posts_then_verifies_exact_body():
    routes = {
        ("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload()),
        ("POST", "/repos/octo/demo/issues/11/comments"): reply(
            {"id": 900, "html_url": "https://x/comment/900"}, status=201),
        ("GET", "/repos/octo/demo/issues/comments/900"): reply(
            {"id": 900, "body": "hello", "html_url": "https://x/comment/900"}),
    }
    client, stub = client_for(routes)
    result = gh_ops.issue_comment_create(REPO, 11, body="hello", write=True, client=client)
    assert result["ok"] is True and result["posted"] is True and result["verified"] is True
    assert result["comment_id"] == 900
    assert stub.methods == ["GET", "POST", "GET"]
    assert stub.calls[1]["body"] == {"body": "hello"}


def test_issue_comment_posted_but_verification_mismatch_warns_before_retry():
    routes = {
        ("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload()),
        ("POST", "/repos/octo/demo/issues/11/comments"): reply({"id": 900}, status=201),
        ("GET", "/repos/octo/demo/issues/comments/900"): reply({"id": 900, "body": "different"}),
    }
    client, _ = client_for(routes)
    result = gh_ops.issue_comment_create(REPO, 11, body="hello", write=True, client=client)
    assert result["ok"] is False and result["posted"] is True and result["verified"] is False
    assert "inspect before retrying" in result["reason"]


def test_issue_comment_empty_body_fails_before_http():
    client, stub = client_for({})
    with pytest.raises(gh_ops.GhOpsError, match="must not be empty"):
        gh_ops.issue_comment_create(REPO, 11, body="\n  ", client=client)
    assert stub.calls == []


def test_runs_by_sha_and_workflow():
    data = {"total_count": 1, "workflow_runs": [{"id": 9, "event": "pull_request", "head_sha": HEAD,
                                                 "status": "completed", "conclusion": "success", "html_url": "u"}]}
    client, stub = client_for({
        ("GET", "/repos/octo/demo/actions/runs"): reply(data),
        ("GET", "/repos/octo/demo/actions/workflows/codeql.yml/runs"): reply(data),
    })
    assert gh_ops.runs(REPO, sha=HEAD, client=client)["runs"][0]["id"] == 9
    assert stub.calls[0]["query"]["head_sha"] == [HEAD]
    assert gh_ops.runs(REPO, workflow="codeql.yml", client=client)["total_count"] == 1
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.runs(REPO, client=client)


def test_workflow_state_exit_codes():
    client, _ = client_for({("GET", "/repos/octo/demo/actions/workflows/codeql.yml"): reply({"state": "disabled_inactivity"})})
    assert gh_ops.main(["workflow-state", REPO, "codeql.yml"], client=client) == 1


# --- checks-wait ----------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def wait(routes, **kwargs):
    client, stub = client_for(routes)
    clock = FakeClock()
    result = gh_ops.checks_wait(REPO, HEAD, client=client, sleep=clock.sleep, clock=clock, interval=10, **kwargs)
    return result, stub, clock


def test_zero_checks_until_timeout_is_not_green():
    result, _, clock = wait({("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply({"check_runs": []})},
                            min_checks=1, timeout=30)
    assert result["ok"] is False and result["timed_out"] is True
    assert result["reason"].startswith("timeout: only 0 check run(s)")
    assert clock.sleeps == [10, 10, 10]


def test_checks_wait_polls_until_complete():
    path = ("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs")
    result, _, clock = wait({path: [
        reply({"check_runs": [check_run("unit", conclusion=None, status="in_progress")]}),
        reply({"check_runs": [check_run("unit"), check_run("lint", conclusion="skipped", run_id=2)]}),
    ]}, min_checks=2)
    assert result["ok"] is True and result["succeeded"] == 1 and clock.sleeps == [10]


def test_checks_wait_failure_reports_annotations(capsys):
    routes = {
        ("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply({"check_runs": [
            check_run("unit"), check_run("e2e", conclusion="failure", annotations=1, run_id=7)]}),
        ("GET", "/repos/octo/demo/check-runs/7/annotations"): reply([
            {"annotation_level": "failure", "title": "tests", "message": "3 failed (py3.12, tested ecfd0ba)"}]),
    }
    client, _ = client_for(routes)
    assert gh_ops.main(["checks-wait", REPO, HEAD, "--min", "2"], client=client) == 1
    out = capsys.readouterr().out
    assert "1 check run(s) failed: e2e" in out
    assert "[failure] tests: 3 failed (py3.12, tested ecfd0ba)" in out


def test_all_skipped_is_not_green():
    result, _, _ = wait({("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply(
        {"check_runs": [check_run("a", conclusion="skipped")]})})
    assert result["ok"] is False and "no check run concluded success" in result["reason"]


def test_min_checks_must_be_positive():
    with pytest.raises(gh_ops.GhOpsError):
        wait({}, min_checks=0)


# --- workflow-dispatch ----------------------------------------------------------------

def dispatch_routes(state="active"):
    return {
        ("GET", "/repos/octo/demo/actions/workflows/codeql.yml"): reply({"state": state}),
        ("POST", "/repos/octo/demo/actions/workflows/codeql.yml/dispatches"): reply(None, status=204),
    }


def test_workflow_dispatch_dry_run_and_inactive_never_post():
    client, stub = client_for(dispatch_routes())
    assert gh_ops.workflow_dispatch(REPO, "codeql.yml", ref="main", client=client)["dry_run"] is True
    client2, stub2 = client_for(dispatch_routes(state="disabled_inactivity"))
    assert gh_ops.workflow_dispatch(REPO, "codeql.yml", ref="main", write=True, client=client2)["ok"] is False
    assert set(stub.methods) == set(stub2.methods) == {"GET"}


def test_workflow_dispatch_write_posts_ref():
    client, stub = client_for(dispatch_routes())
    assert gh_ops.workflow_dispatch(REPO, "codeql.yml", ref="main", write=True, client=client)["dispatched"] is True
    assert stub.calls[-1]["method"] == "POST" and stub.calls[-1]["body"] == {"ref": "main"}


# --- pr-body-replace ------------------------------------------------------------------

def body_routes(body, after=None):
    bodies = [reply({"body": body})] + ([reply({"body": after})] if after is not None else [])
    return {
        ("GET", "/repos/octo/demo/pulls/11"): bodies,
        ("PATCH", "/repos/octo/demo/pulls/11"): reply({"body": after}),
    }


@pytest.mark.parametrize("body,count", [("no anchor here", 0), ("A-ANCHOR and A-ANCHOR", 2)])
def test_pr_body_replace_requires_exactly_one_anchor(body, count):
    client, stub = client_for(body_routes(body))
    result = gh_ops.pr_body_replace(REPO, 11, old="A-ANCHOR", new="B", write=True, client=client)
    assert result["ok"] is False and result["anchor_count"] == count
    assert set(stub.methods) == {"GET"}


def test_pr_body_replace_mentions_crlf_bodies():
    client, _ = client_for(body_routes("line1\r\nline2"))
    result = gh_ops.pr_body_replace(REPO, 11, old="line1\nline2", new="x", client=client)
    assert "CRLF" in result["reason"]


def test_pr_body_replace_dry_run_then_write_and_verify():
    client, stub = client_for(body_routes("keep [OLD] keep"))
    assert gh_ops.pr_body_replace(REPO, 11, old="[OLD]", new="[NEW]", client=client)["dry_run"] is True
    assert set(stub.methods) == {"GET"}
    client, stub = client_for(body_routes("keep [OLD] keep", after="keep [NEW] keep"))
    result = gh_ops.pr_body_replace(REPO, 11, old="[OLD]", new="[NEW]", write=True, client=client)
    assert result["ok"] is True and result["verified"] is True
    assert stub.calls[1] == {**stub.calls[1], "method": "PATCH", "body": {"body": "keep [NEW] keep"}}


# --- pr-merge -------------------------------------------------------------------------

def merge_routes(pr=None, commits=(HEAD,), runs=None, merge_reply=None):
    return {
        ("GET", "/repos/octo/demo/pulls/11"): reply(pr or pr_payload()),
        ("GET", "/repos/octo/demo/pulls/11/commits"): reply([{"sha": sha} for sha in commits]),
        ("GET", f"/repos/octo/demo/commits/{HEAD}/check-runs"): reply(
            {"check_runs": runs if runs is not None else [check_run(f"c{i}", run_id=i) for i in range(5)]}),
        ("PUT", "/repos/octo/demo/pulls/11/merge"): merge_reply or reply({"merged": True, "sha": "m3rg3"}),
    }


@pytest.mark.parametrize(
    "routes,sha,needle",
    [
        (merge_routes(pr=pr_payload(head={"sha": "0" * 40, "ref": "f"})), "ecfd0ba", "does not match"),
        (merge_routes(commits=(OTHER, HEAD)), "ecfd0ba", "matches 2 commits"),
        (merge_routes(commits=("1" * 40,)), "ecfd0ba", "does not contain the head commit"),
        (merge_routes(pr=pr_payload(mergeable_state="blocked")), "ecfd0ba", "not 'clean'"),
        (merge_routes(pr=pr_payload(state="closed")), "ecfd0ba", "not open"),
        (merge_routes(runs=[]), "ecfd0ba", "only 0 check run(s)"),
        (merge_routes(runs=[check_run(f"c{i}", run_id=i) for i in range(4)] + [check_run("bad", "failure", run_id=9)]),
         "ecfd0ba", "failed: bad"),
        (merge_routes(runs=[check_run(f"c{i}", run_id=i) for i in range(4)] + [check_run("p", None, "queued", run_id=9)]),
         "ecfd0ba", "still pending"),
    ],
)
def test_pr_merge_never_writes_when_a_precondition_fails(routes, sha, needle):
    client, stub = client_for(routes)
    result = gh_ops.pr_merge(REPO, 11, sha=sha, min_checks=5, write=True, client=client)
    assert result["ok"] is False and result["merged"] is False
    assert any(needle in failure for failure in result["failures"]), result["failures"]
    assert "PUT" not in stub.methods


def test_pr_merge_dry_run_describes_the_pinned_merge():
    client, stub = client_for(merge_routes())
    result = gh_ops.pr_merge(REPO, 11, sha="ecfd0ba", min_checks=5, message="msg", client=client)
    assert result["ok"] is True and result["dry_run"] is True
    assert result["would_merge"] == {"sha": HEAD, "merge_method": "squash", "commit_message": "msg"}
    assert "PUT" not in stub.methods


def test_pr_merge_write_pins_head_sha_and_reports_merge_commit():
    client, stub = client_for(merge_routes())
    result = gh_ops.pr_merge(REPO, 11, sha="ecfd0ba", min_checks=5, method="squash", write=True, client=client)
    assert result == {**result, "ok": True, "merged": True, "merge_sha": "m3rg3"}
    put = [call for call in stub.calls if call["method"] == "PUT"]
    assert len(put) == 1 and put[0]["body"]["sha"] == HEAD


def test_pr_merge_head_moved_during_merge_is_a_condition_failure():
    client, _ = client_for(merge_routes(merge_reply=reply({"message": "Head branch was modified"}, status=409)))
    result = gh_ops.pr_merge(REPO, 11, sha="ecfd0ba", min_checks=5, write=True, client=client)
    assert result["ok"] is False and "409" in result["failures"][0]


def test_pr_merge_cli_one_line_dry_run_exit_code(tmp_path, capsys):
    message = tmp_path / "msg.txt"
    message.write_text("Squash title body\n", encoding="utf-8")
    client, stub = client_for(merge_routes())
    code = gh_ops.main(["pr-merge", REPO, "11", "--sha", "ecfd0ba", "--min-checks", "5",
                        "--message-file", str(message)], client=client)
    assert code == 0 and "PUT" not in stub.methods
    assert capsys.readouterr().out.startswith("OK pr-merge (dry run)")


@pytest.mark.parametrize("sha", ["xyz", "abc", "g" * 7])
def test_invalid_sha_is_an_input_error(sha):
    client, _ = client_for(merge_routes())
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.pr_merge(REPO, 11, sha=sha, client=client)


# --- tokens and HTTP error attribution ---------------------------------------------------

def test_token_is_sent_but_never_printed(monkeypatch, capsys):
    secret = "ghp_" + "S3cr3t" * 5
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    stub = Stub({("GET", "/repos/octo/demo/pulls/11"): reply({"message": f"Bad credentials {secret}"}, status=401)})
    code = gh_ops.main(["--json", "pr-status", REPO, "11"], client=gh_ops.Client(transport=stub))
    captured = capsys.readouterr()
    assert code == 2
    assert stub.calls[0]["headers"]["Authorization"] == f"Bearer {secret}"
    assert secret not in captured.out + captured.err
    assert "HTTP 401" in captured.err


def test_forbidden_is_not_reported_as_unconfigured():
    client, _ = client_for({("GET", "/repos/octo/demo/actions/workflows/codeql.yml"): reply({"message": "Resource not accessible"}, 403)})
    with pytest.raises(gh_ops.GhOpsError, match="not evidence that the feature is unconfigured"):
        gh_ops.workflow_state(REPO, "codeql.yml", client=client)


# --- stdlib-only / one-line import ---------------------------------------------------------

def test_runs_with_python_dash_s_and_imports_in_one_line():
    help_run = subprocess.run([sys.executable, "-S", str(SCRIPTS / "gh_ops.py"), "--help"], capture_output=True, text=True)
    assert help_run.returncode == 0 and "issue-comments" in help_run.stdout
    one_line = subprocess.run([sys.executable, "-S", "-c", "from gh_ops import pr_merge, issue_comments; print('ok')"],
                              cwd=SCRIPTS, capture_output=True, text=True)
    assert one_line.stdout.strip() == "ok", one_line.stderr


# --- sync-main (git) ------------------------------------------------------------------------

def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(key, value)
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", str(remote), str(work))
    (work / "a.txt").write_text("base\n", encoding="utf-8")
    git(work, "add", "a.txt"); git(work, "commit", "-m", "base"); git(work, "push", "origin", "HEAD:main")
    git(work, "checkout", "-b", "feature")
    (work / "b.txt").write_text("feature\n", encoding="utf-8")
    git(work, "add", "b.txt"); git(work, "commit", "-m", "feature"); git(work, "push", "-u", "origin", "feature")
    return remote, work


def advance_main(remote, tmp_path, filename, text):
    other = tmp_path / f"other-{filename}"
    git(tmp_path, "clone", "-b", "main", str(remote), str(other))
    (other / filename).write_text(text, encoding="utf-8")
    git(other, "add", filename); git(other, "commit", "-m", f"main {filename}"); git(other, "push", "origin", "main")


def test_sync_main_dry_run_then_merge(repos, tmp_path):
    remote, work = repos
    advance_main(remote, tmp_path, "c.txt", "main\n")
    assert gh_ops.sync_main(cwd=str(work)) == {"ok": True, "branch": "feature", "behind": 1, "merged": False, "dry_run": True}
    result = gh_ops.sync_main(cwd=str(work), write=True, push=True)
    assert result["merged"] is True and result["pushed"] is True
    assert git(work, "rev-parse", "HEAD") == git(work, "rev-parse", "origin/feature")
    assert (work / "c.txt").exists()


def test_sync_main_conflict_aborts_cleanly(repos, tmp_path):
    remote, work = repos
    advance_main(remote, tmp_path, "b.txt", "conflicting\n")
    result = gh_ops.sync_main(cwd=str(work), write=True)
    assert result["ok"] is False and result["conflicts"] == ["b.txt"]
    assert git(work, "status", "--porcelain") == ""
    assert (work / "b.txt").read_text(encoding="utf-8") == "feature\n"


def test_sync_main_refuses_diverged_local_or_dirty_tree(repos):
    _, work = repos
    (work / "b.txt").write_text("dirty\n", encoding="utf-8")
    assert gh_ops.sync_main(cwd=str(work), write=True)["reason"] == "working tree has uncommitted changes"
    git(work, "commit", "-am", "local only")
    assert "differs from origin/feature" in gh_ops.sync_main(cwd=str(work), write=True)["reason"]


# --- comments-file (offline digest of a saved JSON dump) ----------------------------

def _saved_comments(count=43, size=2000):
    return [{"id": n, "created_at": f"2026-09-20T14:{n:02d}:00Z",
             "user": {"login": "claude[bot]" if n == 0 else "myon"}, "body": ("本文 " * size)[:size],
             "html_url": f"https://github.com/o/r/issues/24#issuecomment-{n}"} for n in range(count)]


def test_comments_file_digests_an_oversized_saved_tool_result(tmp_path, capsys):
    path = tmp_path / "mcp-github-issue_read.txt"
    path.write_text(json.dumps(_saved_comments(), ensure_ascii=False), encoding="utf-8")  # ~90k chars, 1 line
    assert gh_ops.main(["comments-file", str(path), "--preview", "40"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"{path} -- 43 comment(s)"
    assert len(lines) == 44 and max(len(line) for line in lines[1:]) < 120
    assert lines[1].startswith("[0] 2026-09-20T14:00:00Z claude[bot] 2000 chars: 本文")


def test_comments_file_reads_save_output_from_stdin_and_filters(monkeypatch):
    saved = {"issue": {"title": "Roadmap", "state": "open"}, "comments": _saved_comments(3, 10)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(saved)))
    result = gh_ops.comments_file("-", author="myon", last=1, show=(0,))
    assert (result["title"], result["total_comments"]) == ("Roadmap", 3)
    assert [row["index"] for row in result["comments"]] == [2]
    assert result["shown"] == {0: saved["comments"][0]["body"]}


def test_comments_file_rejects_non_comment_json(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"not": "comments"}', encoding="utf-8")
    assert gh_ops.main(["comments-file", str(path)]) == 2
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(gh_ops.GhOpsError, match="not JSON"):
        gh_ops.comments_file(str(path))


def test_comments_file_runs_offline_without_a_token(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(_saved_comments(2, 5)), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {"GITHUB_TOKEN", "GH_TOKEN"}}
    env.update(PYTHONIOENCODING="utf-8", HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
    done = subprocess.run([sys.executable, "-S", str(SCRIPTS / "gh_ops.py"), "comments-file", str(path),
                           "--last", "1"], capture_output=True, text=True, encoding="utf-8", env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[1].startswith("[1] 2026-09-20T14:01:00Z myon 5 chars: ")


# --- pr-for-branch -----------------------------------------------------------------

def test_pr_for_branch_lists_matches_with_an_owner_qualified_head(capsys):
    pulls = [{"number": 61, "state": "closed", "merged_at": "2026-09-26T17:00:00Z", "draft": False,
              "head": {"sha": "f534c03af3d5aaaa"}, "base": {"ref": "main"}, "title": "feat: P7",
              "html_url": "https://github.com/octo/demo/pull/61"}]
    client, stub = client_for({("GET", "/repos/octo/demo/pulls"): reply(pulls)})
    assert gh_ops.main(["pr-for-branch", REPO, "claude/x"], client=client) == 0
    assert capsys.readouterr().out.strip() == (
        '#61 merged head=f534c03af3d5 -> main "feat: P7" https://github.com/octo/demo/pull/61')
    assert stub.calls[0]["query"]["head"] == ["octo:claude/x"]
    assert stub.calls[0]["query"]["state"] == ["all"]


def test_pr_for_branch_without_a_pr_is_exit_1(capsys):
    client, stub = client_for({("GET", "/repos/octo/demo/pulls"): reply([])})
    assert gh_ops.main(["pr-for-branch", REPO, "fork:feature", "--state", "open"], client=client) == 1
    assert capsys.readouterr().out.strip() == "no open PR with head fork:feature"
    assert stub.calls[0]["query"]["head"] == ["fork:feature"]


# --- open-prs (cross-repository) ----------------------------------------------------

def test_open_prs_lists_across_repositories(capsys):
    items = [{"repository_url": "https://api.github.com/repos/octo/web", "number": 7, "draft": True,
              "updated_at": "2026-09-26T16:00:00Z", "user": {"login": "octo"}, "title": "feat: a",
              "html_url": "https://github.com/octo/web/pull/7"},
             {"repository_url": "https://api.github.com/repos/octo/demo", "number": 3,
              "updated_at": "2026-09-25T10:00:00Z", "user": {"login": "bot"}, "title": "fix: b",
              "html_url": "https://github.com/octo/demo/pull/3"}]
    client, stub = client_for({("GET", "/search/issues"): reply({"total_count": 5, "items": items})})
    assert gh_ops.main(["open-prs", "octo", "--limit", "2"], client=client) == 0
    assert capsys.readouterr().out.splitlines() == [
        "5 open PR(s) for octo (showing 2)",
        'octo/web#7 draft 2026-09-26 octo "feat: a" https://github.com/octo/web/pull/7',
        'octo/demo#3 2026-09-25 bot "fix: b" https://github.com/octo/demo/pull/3',
    ]
    assert stub.calls[0]["query"]["q"] == ["is:pr is:open archived:false user:octo"]


def test_open_prs_org_qualifier_and_none_is_exit_1(capsys):
    client, stub = client_for({("GET", "/search/issues"): reply({"total_count": 0, "items": []})})
    assert gh_ops.main(["open-prs", "acme", "--org"], client=client) == 1
    assert stub.calls[0]["query"]["q"] == ["is:pr is:open archived:false org:acme"]


def test_open_prs_repos_use_repository_scoped_endpoints(capsys):
    client, stub = client_for({
        ("GET", "/repos/octo/web/pulls"): reply([{"number": 1, "updated_at": "2026-09-20T00:00:00Z",
                                                  "user": {"login": "a"}, "title": "old", "html_url": "u1"}]),
        ("GET", "/repos/octo/demo/pulls"): reply([{"number": 9, "updated_at": "2026-09-26T00:00:00Z", "draft": True,
                                                   "user": {"login": "b"}, "title": "new", "html_url": "u9"}]),
    })
    assert gh_ops.main(["open-prs", "octo", "--repos", "web,demo"], client=client) == 0
    assert capsys.readouterr().out.splitlines() == [
        "2 open PR(s) for octo", 'octo/demo#9 draft 2026-09-26 b "new" u9', 'octo/web#1 2026-09-20 a "old" u1']
    assert [call["query"]["state"] for call in stub.calls] == [["open"], ["open"]]


# --- pr-body-set ------------------------------------------------------------------

def test_pr_body_set_unchanged_is_ok_without_any_write():
    client, stub = client_for({("GET", "/repos/octo/demo/pulls/11"): reply({"body": "same text"})})
    result = gh_ops.pr_body_set(REPO, 11, new="same text", write=True, client=client)
    assert result == {"ok": True, "changed": False, "old_chars": 9, "new_chars": 9,
                      "lines_differ": 0, "reason": "unchanged"}
    assert stub.methods == ["GET"]


def test_pr_body_set_dry_run_reports_sizes_and_line_diff_without_writing():
    client, stub = client_for({("GET", "/repos/octo/demo/pulls/11"): reply({"body": "a\nb\nc"})})
    result = gh_ops.pr_body_set(REPO, 11, new="a\nx\nc", client=client)
    assert result == {"ok": True, "dry_run": True, "changed": False, "old_chars": 5, "new_chars": 5,
                      "lines_differ": 1}
    assert stub.methods == ["GET"]


def test_pr_body_set_write_then_verify():
    client, stub = client_for(body_routes("a\nb\nc", after="a\nx\nc"))
    result = gh_ops.pr_body_set(REPO, 11, new="a\nx\nc", write=True, client=client)
    assert result["ok"] is True and result["verified"] is True and result["lines_differ"] == 1
    assert stub.methods == ["GET", "PATCH", "GET"]
    assert stub.calls[1] == {**stub.calls[1], "method": "PATCH", "body": {"body": "a\nx\nc"}}


def test_pr_body_set_write_reports_unverified_when_body_does_not_match():
    client, stub = client_for(body_routes("old", after="something else"))
    result = gh_ops.pr_body_set(REPO, 11, new="new", write=True, client=client)
    assert result["ok"] is False and result["verified"] is False
    assert "does not match" in result["reason"]


def test_pr_body_set_cli_file_flag_ignores_one_trailing_newline(tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text("hello\n", encoding="utf-8")
    client, stub = client_for({("GET", "/repos/octo/demo/pulls/11"): reply({"body": "hello"})})
    code = gh_ops.main(["pr-body-set", REPO, "11", "--file", str(body_file)], client=client)
    assert code == 0 and stub.methods == ["GET"]
    assert capsys.readouterr().out.strip() == "OK pr-body-set (applied): unchanged"


# --- pr-edit --------------------------------------------------------------------

def edit_routes(before, after=None):
    gets = [reply(before)] + ([reply(after)] if after is not None else [])
    return {
        ("GET", "/repos/octo/demo/pulls/11"): gets,
        ("PATCH", "/repos/octo/demo/pulls/11"): reply(after or {}),
    }


def test_pr_edit_requires_at_least_one_field():
    client, _ = client_for(edit_routes(pr_payload()))
    with pytest.raises(gh_ops.GhOpsError, match="at least one"):
        gh_ops.pr_edit(REPO, 11, client=client)


def test_pr_edit_rejects_an_invalid_state():
    client, _ = client_for(edit_routes(pr_payload()))
    with pytest.raises(gh_ops.GhOpsError, match="--state"):
        gh_ops.pr_edit(REPO, 11, state="merged", client=client)


def test_pr_edit_cli_missing_fields_is_exit_2(capsys):
    client, stub = client_for(edit_routes(pr_payload()))
    code = gh_ops.main(["pr-edit", REPO, "11"], client=client)
    assert code == 2 and stub.methods == []
    assert "at least one" in capsys.readouterr().err


def test_pr_edit_dry_run_reports_current_to_new_and_never_writes():
    client, stub = client_for(edit_routes(pr_payload(title="Old title")))
    result = gh_ops.pr_edit(REPO, 11, title="New title", base="develop", client=client)
    assert result == {"ok": True, "dry_run": True, "changed": False,
                      "fields": {"title": {"from": "Old title", "to": "New title"},
                                 "base": {"from": "main", "to": "develop"}}}
    assert stub.methods == ["GET"]


def test_pr_edit_write_patches_only_given_fields_and_verifies():
    client, stub = client_for(edit_routes(pr_payload(title="Old"), after=pr_payload(title="New")))
    result = gh_ops.pr_edit(REPO, 11, title="New", write=True, client=client)
    assert result["ok"] is True and result["verified"] is True
    assert result["fields"] == {"title": {"from": "Old", "to": "New"}}
    assert stub.methods == ["GET", "PATCH", "GET"]
    assert stub.calls[1]["body"] == {"title": "New"}


def test_pr_edit_write_reports_unverified_on_mismatch():
    client, stub = client_for(edit_routes(pr_payload(state="open"), after=pr_payload(state="open")))
    result = gh_ops.pr_edit(REPO, 11, state="closed", write=True, client=client)
    assert result["ok"] is False and result["verified"] is False
    assert "do not match" in result["reason"]


# --- file-put -------------------------------------------------------------------

def contents_routes(existing=None, default_branch="main", commit_sha="c0mm1t"):
    routes = {("GET", "/repos/octo/demo"): reply({"default_branch": default_branch})}
    if existing is None:
        routes[("GET", "/repos/octo/demo/contents/docs/x.md")] = reply({"message": "Not Found"}, status=404)
    else:
        encoded = base64.b64encode(existing).decode("ascii")
        routes[("GET", "/repos/octo/demo/contents/docs/x.md")] = reply({"sha": "abc123", "content": encoded})
    routes[("PUT", "/repos/octo/demo/contents/docs/x.md")] = reply(
        {"content": {"sha": "newblob"}, "commit": {"sha": commit_sha}}, status=201)
    return routes


def test_file_put_refuses_default_branch_without_override():
    client, stub = client_for(contents_routes(existing=b"old"))
    result = gh_ops.file_put(REPO, "docs/x.md", content=b"new", branch="main", message="m",
                             write=True, client=client)
    assert result["ok"] is False and "default branch" in result["reason"]
    assert stub.methods == ["GET"]


def test_file_put_allows_default_branch_with_override():
    client, stub = client_for(contents_routes(existing=b"old"))
    result = gh_ops.file_put(REPO, "docs/x.md", content=b"new", branch="main", message="m",
                             allow_default_branch=True, client=client)
    assert result["ok"] is True and result["dry_run"] is True and result["action"] == "update"
    assert set(stub.methods) == {"GET"} and "PUT" not in stub.methods


def test_file_put_unchanged_content_is_ok_without_any_write():
    client, stub = client_for(contents_routes(existing=b"same bytes"))
    result = gh_ops.file_put(REPO, "docs/x.md", content=b"same bytes", branch="feature", message="m",
                             write=True, client=client)
    assert result == {"ok": True, "changed": False, "action": "update", "path": "docs/x.md",
                      "branch": "feature", "local_size": 10, "sha": "abc123", "reason": "unchanged"}
    assert set(stub.methods) == {"GET"}


def test_file_put_dry_run_reports_create_when_absent_and_never_writes():
    client, stub = client_for(contents_routes(existing=None))
    result = gh_ops.file_put(REPO, "docs/x.md", content=b"new file", branch="feature", message="m", client=client)
    assert result == {"ok": True, "dry_run": True, "changed": False, "action": "create", "path": "docs/x.md",
                      "branch": "feature", "local_size": 8, "sha": None}
    assert "PUT" not in stub.methods


def test_file_put_write_sends_base64_and_returns_commit_sha():
    client, stub = client_for(contents_routes(existing=b"old bytes"))
    result = gh_ops.file_put(REPO, "docs/x.md", content=b"new bytes", branch="feature", message="update x",
                             write=True, client=client)
    assert result["ok"] is True and result["commit_sha"] == "c0mm1t" and result["action"] == "update"
    put = [call for call in stub.calls if call["method"] == "PUT"][0]
    assert put["body"] == {"message": "update x", "content": base64.b64encode(b"new bytes").decode("ascii"),
                           "branch": "feature", "sha": "abc123"}


def test_file_put_create_omits_sha_from_the_put_body():
    client, stub = client_for(contents_routes(existing=None))
    result = gh_ops.file_put(REPO, "docs/x.md", content=b"x", branch="feature", message="m",
                             write=True, client=client)
    assert result["ok"] is True and result["action"] == "create"
    put = [call for call in stub.calls if call["method"] == "PUT"][0]
    assert "sha" not in put["body"]


def test_file_put_cli_reads_local_file_via_from_flag(tmp_path):
    local = tmp_path / "local.md"
    local.write_bytes(b"hello world")
    client, stub = client_for(contents_routes(existing=None))
    code = gh_ops.main(["file-put", REPO, "docs/x.md", "--from", str(local), "--branch", "feature",
                        "--message", "add x"], client=client)
    assert code == 0 and "PUT" not in stub.methods


# --- url (pure string building; no network) -----------------------------------------

def test_url_pr_default_and_tabs():
    assert gh_ops.url_pr(REPO, 11) == {"ok": True, "web": "https://github.com/octo/demo/pull/11",
                                       "api": "https://api.github.com/repos/octo/demo/pulls/11"}
    assert gh_ops.url_pr(REPO, 11, tab="files") == {
        "ok": True, "web": "https://github.com/octo/demo/pull/11/files",
        "api": "https://api.github.com/repos/octo/demo/pulls/11/files"}
    assert gh_ops.url_pr(REPO, 11, tab="commits") == {
        "ok": True, "web": "https://github.com/octo/demo/pull/11/commits",
        "api": "https://api.github.com/repos/octo/demo/pulls/11/commits"}
    assert gh_ops.url_pr(REPO, 11, tab="checks") == {
        "ok": True, "web": "https://github.com/octo/demo/pull/11/checks", "api": None}


def test_url_pr_rejects_an_unknown_tab():
    with pytest.raises(gh_ops.GhOpsError, match="--tab"):
        gh_ops.url_pr(REPO, 11, tab="conversation")


def test_url_compare_quotes_slashes_and_spaces_but_keeps_them_in_path():
    assert gh_ops.url_compare(REPO, "main", "feature/my branch") == {
        "ok": True, "web": "https://github.com/octo/demo/compare/main...feature/my%20branch",
        "api": "https://api.github.com/repos/octo/demo/compare/main...feature/my%20branch"}


def test_url_blame_quotes_unicode_path_and_adds_line_anchor():
    result = gh_ops.url_blame(REPO, "main", "docs/café notes.md", line=42)
    assert result == {"ok": True, "api": None,
                      "web": "https://github.com/octo/demo/blame/main/docs/caf%C3%A9%20notes.md#L42"}


def test_url_blame_without_line_has_no_anchor():
    assert gh_ops.url_blame(REPO, "main", "a.py") == {
        "ok": True, "web": "https://github.com/octo/demo/blame/main/a.py", "api": None}


def test_url_history_web_path_and_api_query_string():
    result = gh_ops.url_history(REPO, "v1.0", "src/a b.py")
    assert result == {"ok": True, "web": "https://github.com/octo/demo/commits/v1.0/src/a%20b.py",
                      "api": "https://api.github.com/repos/octo/demo/commits?sha=v1.0&path=src%2Fa+b.py"}


def test_url_runs_plain_and_filtered():
    assert gh_ops.url_runs(REPO) == {"ok": True, "web": "https://github.com/octo/demo/actions",
                                     "api": "https://api.github.com/repos/octo/demo/actions/runs"}
    result = gh_ops.url_runs(REPO, workflow="ci.yml", branch="main", event="push", status="success")
    assert result == {
        "ok": True,
        "web": "https://github.com/octo/demo/actions/workflows/ci.yml?query=branch%3Amain+event%3Apush+is%3Asuccess",
        "api": "https://api.github.com/repos/octo/demo/actions/workflows/ci.yml/runs"
               "?branch=main&event=push&status=success",
    }


def test_url_search_types_and_unicode_query():
    assert gh_ops.url_search("café config", kind="code") == {
        "ok": True, "web": "https://github.com/search?q=caf%C3%A9+config&type=code",
        "api": "https://api.github.com/search/code?q=caf%C3%A9+config"}
    prs = gh_ops.url_search("is:open", kind="pullrequests")
    assert prs == {"ok": True, "web": "https://github.com/search?q=is%3Apr+is%3Aopen&type=issues",
                   "api": "https://api.github.com/search/issues?q=is%3Apr+is%3Aopen"}
    issues = gh_ops.url_search("bug", kind="issues")
    assert issues == {"ok": True, "web": "https://github.com/search?q=is%3Aissue+bug&type=issues",
                      "api": "https://api.github.com/search/issues?q=is%3Aissue+bug"}


def test_url_search_rejects_an_unknown_type():
    with pytest.raises(gh_ops.GhOpsError, match="--type"):
        gh_ops.url_search("x", kind="wat")


def test_url_raw_quotes_unicode_and_keeps_slashes():
    assert gh_ops.url_raw(REPO, "main", "docs/café/a b.md") == {
        "ok": True, "web": "https://raw.githubusercontent.com/octo/demo/main/docs/caf%C3%A9/a%20b.md",
        "api": "https://api.github.com/repos/octo/demo/contents/docs/caf%C3%A9/a%20b.md?ref=main"}


def test_url_cli_prints_web_then_api_line(capsys):
    assert gh_ops.main(["url", "pr", REPO, "11", "--tab", "files"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "https://github.com/octo/demo/pull/11/files",
        "api: https://api.github.com/repos/octo/demo/pulls/11/files",
    ]


def test_url_cli_omits_api_line_when_none_exists(capsys):
    assert gh_ops.main(["url", "blame", REPO, "main", "a.py"]) == 0
    assert capsys.readouterr().out.strip() == "https://github.com/octo/demo/blame/main/a.py"


def test_url_cli_needs_no_client_or_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert gh_ops.main(["url", "raw", REPO, "main", "a.py"]) == 0


# --- read-only repository counts / open Issues ------------------------------------

def count_routes(repo=REPO, issues=(), commits=(), branch="main"):
    return {
        ("GET", f"/repos/{repo}"): reply({"default_branch": branch, "open_issues_count": 999}),
        ("GET", f"/repos/{repo}/issues"): reply(list(issues)),
        ("GET", f"/repos/{repo}/commits"): reply(list(commits)),
    }


def test_repo_counts_zero_is_success_and_human_columns(capsys):
    client, stub = client_for(count_routes())
    assert gh_ops.main(["repo-counts", "octo", "--repos", "demo"], client=client) == 0
    assert capsys.readouterr().out.splitlines() == [
        "repo  open issues  open PRs  commits", "octo/demo  0  0  0"]
    assert stub.methods == ["GET"] * 3
    assert stub.calls[0]["query"] == {"per_page": ["100"], "state": ["open"]}
    assert stub.calls[-1]["query"]["sha"] == ["main"]


@pytest.mark.parametrize("org,endpoint", [(True, "/orgs/octo/repos"), (False, "/users/octo/repos")])
def test_repo_counts_empty_owner(org, endpoint):
    client, stub = client_for({("GET", endpoint): reply([])})
    result = gh_ops.repo_counts("octo", org=org, client=client)
    assert result["ok"] and result["repositories"] == []
    assert result["repository_count"] == 0
    assert result["totals"] == {"open_issues": 0, "open_prs": 0, "commits": 0}
    assert stub.methods == ["GET"]


def test_repo_counts_explicit_multiple_repos_mixed_and_json(capsys):
    routes = count_routes(issues=[{"number": 1}, {"number": 2, "pull_request": {}}], commits=[{"sha": HEAD}])
    routes.update(count_routes("octo/web", issues=[{"number": 3, "pull_request": None}],
                               commits=[{"sha": HEAD}, {"sha": OTHER}], branch="trunk"))
    client, stub = client_for(routes)
    assert gh_ops.main(["--json", "repo-counts", "octo", "--org", "--repos", " web,demo,web "], client=client) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["repositories"] == [
        {"repo": REPO, "open_issues": 1, "open_prs": 1, "commits": 1, "default_branch": "main"},
        {"repo": "octo/web", "open_issues": 0, "open_prs": 1, "commits": 2, "default_branch": "trunk"}]
    assert result["totals"] == {"open_issues": 1, "open_prs": 2, "commits": 3}
    assert result["commit_scope"] == "default_branch"
    assert result["repository_count"] == 2
    assert stub.methods == ["GET"] * 6
    assert stub.calls[-1]["query"]["sha"] == ["trunk"]


def test_repo_counts_paginates_repositories_issues_and_commits_beyond_fifty_pages():
    def pages(path, data):
        def route(parts, body):
            page = int(urllib.parse.parse_qs(parts.query).get("page", ["1"])[0])
            link = f'<https://api.github.com{path}?per_page=100&page={page + 1}>; rel="next"' if page < len(data) else None
            return reply(data[page - 1], link=link)
        return route
    routes = count_routes()
    routes.update(count_routes("octo/web"))
    routes[("GET", "/orgs/octo/repos")] = pages("/orgs/octo/repos", [
        [{"full_name": "octo/web", "archived": True}], [{"full_name": REPO}]])
    routes[("GET", f"/repos/{REPO}/issues")] = pages(f"/repos/{REPO}/issues", [
        [{"number": 1}, {"number": 2, "pull_request": {}}], [{"number": 3}]])
    routes[("GET", f"/repos/{REPO}/commits")] = pages(f"/repos/{REPO}/commits", [[{"sha": str(i)}] for i in range(51)])
    client, stub = client_for(routes)
    result = gh_ops.repo_counts("octo", org=True, client=client)
    assert result["totals"] == {"open_issues": 2, "open_prs": 1, "commits": 51}
    assert result["repository_count"] == 2
    assert set(stub.methods) == {"GET"}


def test_repo_counts_empty_git_repository_is_zero():
    routes = count_routes()
    routes[("GET", f"/repos/{REPO}/commits")] = reply({"message": "Git Repository is empty.", "status": "409"}, status=409)
    client, _ = client_for(routes)
    assert gh_ops.repo_counts("octo", repos=("demo",), client=client)["totals"]["commits"] == 0


@pytest.mark.parametrize("status", [403, 404, 409, 500])
def test_repo_counts_errors_do_not_become_zero(status, capsys):
    routes = count_routes()
    routes[("GET", f"/repos/{REPO}/commits")] = reply({"message": "unavailable"}, status=status)
    client, stub = client_for(routes)
    assert gh_ops.main(["--json", "repo-counts", "octo", "--repos", "demo"], client=client) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and f"HTTP {status}" in captured.err
    assert set(stub.methods) == {"GET"}


def test_open_issues_excludes_prs_and_paginates(capsys):
    routes = {
        ("GET", f"/repos/{REPO}/issues"): [
            reply([{"number": 1, "title": "todo", "html_url": "u1"}, {"number": 2, "pull_request": {}}],
                  link=f'<https://api.github.com/repos/{REPO}/issues?state=open&per_page=100&page=2>; rel="next"'),
            reply([{"number": 3, "title": "next", "html_url": "u3"}])],
        ("GET", "/repos/octo/web/issues"): reply([]),
    }
    client, stub = client_for(routes)
    assert gh_ops.main(["--json", "open-issues", "octo", "--repos", "demo,web"], client=client) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["total"] == 2
    assert [row["number"] for row in result["issues"]] == [1, 3]
    assert all(row["repo"] == REPO for row in result["issues"])
    assert stub.calls[1]["query"]["page"] == ["2"]
    assert set(stub.methods) == {"GET"}


def test_open_issues_zero_human_and_org_enumeration(capsys):
    client, _ = client_for({("GET", "/orgs/octo/repos"): reply([{"full_name": REPO}]),
                            ("GET", f"/repos/{REPO}/issues"): reply([])})
    assert gh_ops.main(["open-issues", "octo", "--org"], client=client) == 0
    assert capsys.readouterr().out.strip() == "0 open Issue(s) for octo"


@pytest.mark.parametrize("link,error", [
    ('<https://evil.example/items>; rel="next"', "outside the API host"),
    (f'<https://api.github.com/repos/{REPO}/issues?page=2>; rel="next"', "repeated pagination")])
def test_counts_reject_unsafe_or_cyclic_pagination(link, error):
    routes = count_routes()
    routes[("GET", f"/repos/{REPO}/issues")] = reply([], link=link)
    client, _ = client_for(routes)
    with pytest.raises(gh_ops.GhOpsError, match=error):
        gh_ops.repo_counts("octo", repos=("demo",), client=client)


def test_counts_validates_all_explicit_repos_before_any_http():
    client, stub = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.repo_counts("octo", repos=("demo", "../bad"), client=client)
    assert stub.calls == []


def test_counts_cli_stdlib_only_under_python_s():
    code = '''import gh_ops
calls = []
def transport(method, url, body, headers):
    calls.append(method)
    return gh_ops.Response(200, [])
assert gh_ops.main(["--json", "repo-counts", "octo", "--org"],
                   client=gh_ops.Client(transport=transport)) == 0
assert calls == ["GET"]
'''
    result = subprocess.run([sys.executable, "-S", "-c", code], cwd=SCRIPTS, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["totals"] == {"open_issues": 0, "open_prs": 0, "commits": 0}


def test_missing_remote_head_preserves_unknown_comparison():
    client, _ = client_for({("GET", "/repos/octo/demo/pulls/11"): reply(pr_payload(head={}))})
    result = gh_ops.compare_pr_head_identity(REPO, 11, {"sha": HEAD}, client=client)
    assert result["comparable"] is False and result["same"] is None


def test_both_checks_interfaces_reject_zero_minimum():
    client, stub = client_for({})
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.checks_status(REPO, HEAD, min_checks=0, client=client)
    with pytest.raises(ValueError):
        gh_identity.checks_for_sha(REPO, HEAD, min_checks=0)
    assert stub.calls == []


@pytest.mark.parametrize('case', ['limit', 'cycle', 'missing', 'changed'])
def test_incomplete_check_pagination_never_allows_merge(case):
    path = f"/repos/octo/demo/commits/{HEAD}/check-runs"
    def page(parts, body):
        n = int(urllib.parse.parse_qs(parts.query).get('page', ['1'])[0])
        total = 51 if case == 'limit' else 2
        if case == 'changed' and n == 2:
            total = 3
        next_page = 2 if case == 'cycle' else n + 1
        link = f'<https://api.github.com{path}?page={next_page}>; rel="next"'
        if case == 'missing' or (case == 'changed' and n == 2):
            link = None
        return reply({'total_count': total, 'check_runs': [check_run(str(n), run_id=n)]}, link=link)
    routes = merge_routes()
    routes[('GET', path)] = page
    client, stub = client_for(routes)
    with pytest.raises(gh_ops.GhOpsError, match='incomplete response'):
        gh_ops.pr_merge(REPO, 11, sha=HEAD, write=True, client=client)
    assert 'PUT' not in stub.methods


def test_last_check_page_failure_prevents_merge():
    path = f"/repos/octo/demo/commits/{HEAD}/check-runs"
    routes = merge_routes()
    routes[('GET', path)] = [
        reply({'total_count': 2, 'check_runs': [check_run('good')]},
              link=f'<https://api.github.com{path}?page=2>; rel="next"'),
        reply({'total_count': 2, 'check_runs': [check_run('bad', 'failure', run_id=2)]})]
    client, stub = client_for(routes)
    result = gh_ops.pr_merge(REPO, 11, sha=HEAD, write=True, client=client)
    assert not result['ok']
    assert 'PUT' not in stub.methods


def test_default_pr_status_uses_canonical_transport(monkeypatch):
    seen = []
    def request(method, path):
        seen.append((method, path))
        return pr_payload()
    monkeypatch.setattr(gh_identity, "request", request)
    assert gh_ops.pr_status(REPO, 11)["head_sha"] == HEAD
    assert seen == [("GET", "repos/octo/demo/pulls/11")]


def test_default_open_prs_uses_bounded_canonical_search(monkeypatch):
    seen = []
    def search(query, **kwargs):
        seen.append((query, kwargs))
        return {"items": [], "total_count": 7, "complete": False, "truncated": True}
    monkeypatch.setattr(gh_identity, "search", search)
    result = gh_ops.open_prs("octo", org=True, limit=5)
    assert result["total"] == 7 and result["truncated"] and not result["complete"]
    assert seen == [("is:open archived:false org:octo", {"kind": "pr", "max_items": 5})]

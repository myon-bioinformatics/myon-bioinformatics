"""pr-retarget in gh_ops: transport stub plus real git for empty commit messages."""
import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gh_ops", ROOT / "gh_ops.py")
gh_ops = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules["gh_ops"] = gh_ops
spec.loader.exec_module(gh_ops)

REPO = "octo/demo"
NUMBER = 7
HEAD_SHA = "b" * 40
OLD_TIP = "c" * 40
MAIN_TIP = "d" * 40
TREE_SHA = "t" * 40


def reply(data=None, status=200):
    return gh_ops.Response(status, data)


class Stub:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        self.calls.append({"method": method, "path": parts.path, "body": body})
        route = self.routes.get((method, parts.path))
        if route is None:
            raise AssertionError(f"unexpected {method} {parts.path}")
        if callable(route):
            return route(parts, body)
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route


def client_for(routes):
    stub = Stub(routes)
    return gh_ops.Client(token="t0ken", transport=stub), stub


def pr_payload(**over):
    data = {"state": "open", "merged": False, "draft": False, "html_url": f"https://github.com/{REPO}/pull/{NUMBER}",
            "base": {"ref": "main", "sha": MAIN_TIP}, "head": {"ref": "feat/x", "sha": HEAD_SHA}}
    if "base_ref" in over:
        data["base"] = {"ref": over.pop("base_ref"), "sha": over.pop("base_sha", OLD_TIP)}
    if "head_sha" in over:
        data["head"] = {"ref": over.pop("head_ref", "feat/x"), "sha": over.pop("head_sha")}
    data.update(over)
    return data


def ref_route(sha):
    return reply({"object": {"sha": sha}})


def commit_tree_route(tree_sha):
    return reply({"tree": {"sha": tree_sha}})


def compare_path(old_base, new_base="main"):
    return (f"/repos/{REPO}/compare/{urllib.parse.quote(new_base, safe='')}"
            f"...{urllib.parse.quote(old_base, safe='')}")


def retarget_routes(old_base, compare, *, new_base="main", pr_reads=None, after_base="main", compare_status=200,
                    old_tip=OLD_TIP, base_tip=MAIN_TIP, tree_sha=TREE_SHA, tree_match=False):
    if pr_reads is None:
        pr_reads = [
            pr_payload(base_ref=old_base),
            pr_payload(base_ref=old_base),
            pr_payload(base_ref=after_base),
        ]
    routes = {
        ("GET", f"/repos/{REPO}/pulls/{NUMBER}"): [reply(row) for row in pr_reads],
        ("PATCH", f"/repos/{REPO}/pulls/{NUMBER}"): reply({}),
        ("GET", compare_path(old_base, new_base)): reply(compare, status=compare_status),
    }
    if old_base:
        old_ref_path = f"/repos/{REPO}/git/ref/heads/{urllib.parse.quote(old_base, safe='')}"
        routes[("GET", old_ref_path)] = ([ref_route(old_tip), ref_route(old_tip)]
                                         if len(pr_reads) > 1 else ref_route(old_tip))
    new_ref_path = f"/repos/{REPO}/git/ref/heads/{urllib.parse.quote(new_base, safe='')}"
    routes[("GET", new_ref_path)] = ref_route(base_tip)
    old_tree = tree_sha if tree_match else "u" * 40
    routes[("GET", f"/repos/{REPO}/git/commits/{base_tip}")] = commit_tree_route(tree_sha if tree_match else TREE_SHA)
    routes[("GET", f"/repos/{REPO}/git/commits/{old_tip}")] = commit_tree_route(old_tree)
    return routes


def test_commit_message_subject_empty_whitespace_and_truncation():
    assert gh_ops._commit_message_subject("") == "(empty commit message)"
    assert gh_ops._commit_message_subject("   \n\t\n") == "(empty commit message)"
    assert gh_ops._commit_message_subject(None) == "(empty commit message)"
    assert gh_ops._commit_message_subject("subject\n\nbody") == "subject"
    long_line = "x" * 120
    assert len(gh_ops._commit_message_subject(long_line)) == 100
    assert gh_ops._commit_message_subject(long_line) == "x" * 100


def test_validate_branch_name_strips_and_rejects():
    assert gh_ops._validate_branch_name("  develop  ") == "develop"
    with pytest.raises(gh_ops.GhOpsError, match="refs/"):
        gh_ops._validate_branch_name("refs/heads/main")
    with pytest.raises(gh_ops.GhOpsError, match="\\.\\."):
        gh_ops._validate_branch_name("feat..parent")
    with pytest.raises(gh_ops.GhOpsError, match="whitespace"):
        gh_ops._validate_branch_name("feat parent")
    with pytest.raises(gh_ops.GhOpsError, match="\\.lock"):
        gh_ops._validate_branch_name("release.lock")


def test_pr_retarget_rejects_non_string_base():
    with pytest.raises(gh_ops.GhOpsError, match="base is required"):
        gh_ops.pr_retarget(REPO, NUMBER, 123)  # type: ignore[arg-type]


def test_pr_retarget_old_base_ref_fetch_failure_is_tolerated_for_plan():
    routes = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = reply({"message": "missing"}, status=404)
    client, _ = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "planned"


def test_branch_tip_trees_match_returns_false_on_git_errors():
    client, _ = client_for({("GET", f"/repos/{REPO}/git/ref/heads/main"): reply({}, status=404)})
    assert gh_ops._branch_tip_trees_match(client, REPO, "main", "feat/p") is False


def test_pr_retarget_plans_refuses_and_writes():
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[
        pr_payload(base_ref="feat/parent")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["ok"] is True and out["status"] == "planned" and out["dry_run"] is True
    assert out["old_base_unmerged_commits"] == 0 and "warning" not in out
    assert stub.calls[0]["method"] == "GET"

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["ok"] is True and out["status"] == "verified"
    patch_calls = [call for call in stub.calls if call["method"] == "PATCH"]
    assert patch_calls == [{"method": "PATCH", "path": f"/repos/{REPO}/pulls/{NUMBER}", "body": {"base": "main"}}]

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 3}))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["ok"] is False and out["status"] == "refused_unmerged_base"
    assert out["warning"] == "feat/parent has 3 commit(s) not in main; they would join the diff"
    assert all(call["method"] == "GET" for call in stub.calls)


def test_pr_retarget_tree_match_clears_unmerged_risk_after_squash_style_merge():
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 2}, tree_match=True,
                                             pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["unmerged_commit_tree_match"] is True
    assert "warning" not in out
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 2}, tree_match=True))
    out_write = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out_write["status"] == "verified"


def test_pr_retarget_compare_failure_requires_allow_compare_failure():
    client, stub = client_for(retarget_routes("gone", {}, compare_status=404, pr_reads=[
        pr_payload(base_ref="gone")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "not_found"
    assert out["warning"] == "could not compare gone with main"
    assert not any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("gone", {}, compare_status=404, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="main"),
    ]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verified"
    assert any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_verification_mismatch_ok_reason_dry_run_and_cli_exit():
    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
    ]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verification_mismatch"
    assert out["ok"] is False
    assert out["dry_run"] is False
    assert "after PATCH" in out["reason"]

    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
    ]))
    assert gh_ops.main(["pr-retarget", REPO, str(NUMBER), "--write", "--allow-compare-failure"],
                         client=client) == 1


def test_pr_retarget_empty_compare_commit_messages_do_not_crash():
    compare = {"ahead_by": 2, "commits": [
        {"commit": {"message": ""}},
        {"commit": {"message": "   \n"}},
        {"commit": {"message": "real subject\n\nbody"}},
    ]}
    client, _ = client_for(retarget_routes("feat/parent", compare, pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["old_base_commit_subjects"] == ["(empty commit message)", "(empty commit message)", "real subject"]


def test_pr_retarget_not_open_merged_reason():
    client, _ = client_for({("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(
        pr_payload(state="closed", merged=True, base_ref="feat/x"))})
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "not_open" and out["ok"] is False and out["reason"] == "merged"


def test_pr_retarget_compare_non_404_error_is_not_labeled_not_found():
    client, _ = client_for(retarget_routes(
        "feat/p", {"ahead_by": 1}, compare_status=500, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["compare_error"] != "not_found"
    assert "HTTP 500" in out["compare_error"]


def test_pr_retarget_old_base_commit_subjects_limited_to_five():
    commits = [{"commit": {"message": f"subject-{index}"}} for index in range(6)]
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 6, "commits": commits},
                                         pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["old_base_commit_subjects"] == [f"subject-{index}" for index in range(5)]
    assert "subject-5" not in out["old_base_commit_subjects"]


def test_pr_retarget_render_unknown_old_base(capsys):
    routes = {("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(
        {"state": "open", "merged": False, "html_url": f"https://github.com/{REPO}/pull/{NUMBER}",
         "base": {}, "head": {"ref": "feat/x", "sha": HEAD_SHA}}),
        ("GET", f"/repos/{REPO}/compare/main..."): reply({"ahead_by": 0, "commits": []})}
    client, _ = client_for(routes)
    gh_ops.main(["pr-retarget", REPO, str(NUMBER)], client=client)
    assert capsys.readouterr().out.startswith(f"planned {REPO}#{NUMBER} base (unknown) -> main")


def test_pr_retarget_includes_observed_at():
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert isinstance(out.get("observed_at"), str) and "T" in out["observed_at"]


@pytest.mark.parametrize("field,value,needle", [
    ("state", "closed", "PR is closed"),
    ("base_ref", "other", "base_ref changed"),
    ("head_sha", "e" * 40, "head SHA changed"),
])
def test_pr_retarget_refuses_stale_pr_before_patch(field, value, needle):
    before = pr_payload(base_ref="feat/parent")
    stale = pr_payload(base_ref="feat/parent")
    if field == "state":
        stale["state"] = value
    elif field == "base_ref":
        stale["base"] = {"ref": value, "sha": OLD_TIP}
    elif field == "head_sha":
        stale["head"] = {"ref": "feat/x", "sha": value}
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[before, stale]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr" and out["ok"] is False
    assert needle in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_refuses_stale_old_base_tip_sha():
    before = pr_payload(base_ref="feat/parent")
    stale = pr_payload(base_ref="feat/parent")
    routes = retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[before, stale])
    path = f"/repos/{REPO}/git/ref/heads/feat%2Fparent"
    routes[("GET", path)] = [ref_route(OLD_TIP), ref_route("f" * 40)]
    client, stub = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr"
    assert "old base tip SHA changed" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_expect_head_sha_mismatch_refuses_write():
    before = pr_payload(base_ref="feat/parent")
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[before, before]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, expect_head_sha="a" * 40, client=client)
    assert out["status"] == "refused_stale_pr"
    assert "expect-head-sha" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_noops_and_validation():
    client, stub = client_for({("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(pr_payload())})
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "already_satisfied" and stub.calls == [{"method": "GET", "path": f"/repos/{REPO}/pulls/{NUMBER}", "body": None}]

    client, _ = client_for({("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(pr_payload(state="closed", base_ref="x"))})
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "not_open" and out["ok"] is False

    with pytest.raises(gh_ops.GhOpsError, match="base is required"):
        gh_ops.pr_retarget(REPO, NUMBER, " ")
    with pytest.raises(gh_ops.GhOpsError, match="positive"):
        gh_ops.pr_retarget(REPO, 0, "main")
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 0}, new_base="develop",
                                         pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "  develop  ", client=client)
    assert out["to"] == "develop"


def test_pr_retarget_cli_dry_run_and_write_exit_codes(capsys):
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 1}, pr_reads=[pr_payload(base_ref="feat/p")]))
    assert gh_ops.main(["pr-retarget", REPO, str(NUMBER), "--base", "main"], client=client) == 0
    planned = capsys.readouterr().out
    assert planned.startswith(f"planned {REPO}#{NUMBER} base feat/p -> main")
    assert "feat/p has 1 commit(s) not in main; they would join the diff" in planned

    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 1}, pr_reads=[pr_payload(base_ref="feat/p")]))
    code = gh_ops.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write"], client=client)
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused_unmerged_base"


def test_real_git_empty_message_matches_subject_helper(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    subprocess.run(["git", "init", "-b", "main"], cwd=work, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=work, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True, env=env)
    (work / "f").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=work, check=True, env=env)
    subprocess.run(["git", "commit", "--allow-empty-message", "-m", ""], cwd=work, check=True, env=env)
    log = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=work, check=True, capture_output=True, text=True, env=env)
    assert gh_ops._commit_message_subject(log.stdout) == "(empty commit message)"


def test_subprocess_cli_without_injected_client():
    proc = subprocess.run([sys.executable, "-S", str(ROOT / "gh_ops.py"), "pr-retarget", "bad repo", "1"],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 2
    assert "repository must look like" in proc.stderr

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
                    old_tip=OLD_TIP, base_tip=MAIN_TIP, tree_sha=TREE_SHA, tree_match=False,
                    old_ref_missing=False):
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
        if old_ref_missing:
            missing = reply({"message": "missing"}, status=404)
            routes[("GET", old_ref_path)] = ([missing, missing] if len(pr_reads) > 1 else missing)
        else:
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
    with pytest.raises(gh_ops.GhOpsError, match="invalid characters"):
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


def test_branch_tip_tree_match_rejects_empty_new_base_tree_with_nonempty_old():
    routes = {
        ("GET", f"/repos/{REPO}/git/ref/heads/main"): ref_route(MAIN_TIP),
        ("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp"): ref_route(OLD_TIP),
        ("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}"): commit_tree_route(""),
        ("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}"): commit_tree_route(TREE_SHA),
    }
    client, _ = client_for(routes)
    info = gh_ops._branch_tip_tree_match(client, REPO, "main", "feat/p")
    assert info["matched"] is False
    assert info["new_base_tree_sha"] is None
    assert info["old_base_tree_sha"] == TREE_SHA


def test_pr_retarget_write_refuses_unmerged_when_new_base_tree_empty():
    routes = retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="main"),
    ])
    routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route("")
    client, stub = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_unmerged_base" and out["ok"] is False
    assert "unmerged_commit_tree_match" not in out
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_branch_tip_tree_match_returns_false_on_git_errors():
    client, _ = client_for({("GET", f"/repos/{REPO}/git/ref/heads/main"): reply({}, status=404)})
    assert gh_ops._branch_tip_tree_match(client, REPO, "main", "feat/p")["matched"] is False


@pytest.mark.parametrize("name", ["-x", "feat/", "a~1", "a:b", "a^", "a?", "a*", "a[b", "a@{1", "/a", "a//b",
                                  ".a", "a\\b", "a.", "a/.b", "a.lock/b", "@", "a\tb", "a\x7fb"])
def test_validate_branch_name_rejects_git_invalid_names(name):
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops._validate_branch_name(name)


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

    client, stub = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="main"),
    ]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verified"
    assert out["old_base_tip_sha"] is None
    assert any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_verification_mismatch_ok_reason_dry_run_and_cli_exit():
    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
    ]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verification_mismatch"
    assert out["ok"] is False
    assert out["dry_run"] is False
    assert "after PATCH" in out["reason"]

    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True, pr_reads=[
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


def test_pr_retarget_expect_head_sha_matching_verifies_and_cli_passes_flag():
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, expect_head_sha=HEAD_SHA, client=client)
    assert out["status"] == "verified" and out["ok"] is True
    assert any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    assert gh_ops.main(["pr-retarget", REPO, str(NUMBER), "--write", "--expect-head-sha", HEAD_SHA],
                         client=client) == 0
    assert any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_expect_head_sha_invalid_exits_2_before_network(capsys):
    code = gh_ops.main(["pr-retarget", REPO, str(NUMBER), "--expect-head-sha", "not-a-sha"])
    assert code == 2
    assert "SHA must be" in capsys.readouterr().err


def test_pr_retarget_allow_flags_are_not_interchangeable(capsys):
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "refused_unmerged_base" and out["ok"] is False

    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                           pr_reads=[pr_payload(base_ref="gone")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_unmerged_commits=True, client=client)
    assert out["status"] == "refused_compare_failure" and out["ok"] is False

    client, stub = client_for(retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")]))
    assert gh_ops.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write", "--allow-compare-failure"],
                         client=client) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused_unmerged_base"
    assert not any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                              pr_reads=[pr_payload(base_ref="gone")]))
    assert gh_ops.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write", "--allow-unmerged-commits"],
                         client=client) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused_compare_failure"
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_refused_compare_failure_ok_false_and_cli_exit_one():
    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                           pr_reads=[pr_payload(base_ref="gone")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure" and out["ok"] is False
    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                           pr_reads=[pr_payload(base_ref="gone")]))
    assert gh_ops.main(["pr-retarget", REPO, str(NUMBER), "--write"], client=client) == 1


def test_pr_retarget_missing_ahead_by_is_compare_failure():
    client, _ = client_for(retarget_routes("feat/p", {"commits": []}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "missing ahead_by in compare response"


def test_pr_retarget_empty_tree_shas_do_not_count_as_match():
    routes = retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route("")
    routes[("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}")] = commit_tree_route("")
    client, _ = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert "unmerged_commit_tree_match" not in out
    assert out["warning"] == "feat/p has 2 commit(s) not in main; they would join the diff"


def test_pr_retarget_stale_merged_before_patch():
    before = pr_payload(base_ref="feat/parent")
    merged = pr_payload(base_ref="feat/parent", state="closed", merged=True)
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[before, merged]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr" and out["reason"] == "merged"
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_old_tip_stale_matrix():
    sha = OLD_TIP
    assert gh_ops._pr_retarget_old_tip_stale(sha, sha, allow_both_missing=False) == ""
    assert gh_ops._pr_retarget_old_tip_stale(None, None, allow_both_missing=False) == (
        "old base tip could not be verified before PATCH")
    assert gh_ops._pr_retarget_old_tip_stale(None, None, allow_both_missing=True) == ""
    assert "consistently" in gh_ops._pr_retarget_old_tip_stale(None, sha, allow_both_missing=True)


def test_pr_retarget_compare_ok_allow_compare_failure_alone_does_not_bypass_missing_tips():
    routes = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="feat/p"),
    ])
    routes[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = [
        reply({"message": "missing"}, status=404),
        reply({"message": "missing"}, status=404),
    ]
    client, stub = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "refused_stale_pr"
    assert out["ok"] is False
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_compare_ok_both_tips_missing_without_allow_refuses_patch():
    routes = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="feat/p"),
    ])
    routes[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = [
        reply({"message": "missing"}, status=404),
        reply({"message": "missing"}, status=404),
    ]
    client, stub = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr"
    assert out["ok"] is False
    assert "old base tip could not be verified before PATCH" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_git_ref_tip_sha_raises_when_object_has_no_sha():
    client, _ = client_for({("GET", f"/repos/{REPO}/git/ref/heads/main"): reply({"object": {}})})
    with pytest.raises(gh_ops.GhOpsError, match="returned no SHA"):
        gh_ops._git_ref_tip_sha(client, REPO, "main")


def test_pr_retarget_tip_stale_refusal_ok_false_and_cli_exit_one(capsys):
    routes = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/p"), pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = [
        reply({"message": "missing"}, status=404),
        ref_route(OLD_TIP),
    ]
    client, _ = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr" and out["ok"] is False
    routes_cli = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/p"), pr_payload(base_ref="feat/p")])
    routes_cli[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = [
        reply({"message": "missing"}, status=404),
        ref_route(OLD_TIP),
    ]
    client, stub = client_for(routes_cli)
    assert gh_ops.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write"], client=client) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused_stale_pr"
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_negative_ahead_by_is_compare_failure():
    client, stub = client_for(retarget_routes("feat/p", {"ahead_by": -1}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "negative ahead_by in compare response"
    assert out["old_base_unmerged_commits"] is None
    assert not any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("feat/p", {"ahead_by": -1}, pr_reads=[
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="main"),
    ]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verified"
    assert any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_invalid_ahead_by_string_is_compare_failure():
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": "nope"}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "invalid ahead_by in compare response"


def test_pr_retarget_null_ahead_by_is_compare_failure():
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": None}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "null ahead_by in compare response"


def test_pr_retarget_tree_match_requires_nonempty_trees_on_each_side():
    routes = retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route("")
    routes[("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}")] = commit_tree_route(TREE_SHA)
    client, _ = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert "unmerged_commit_tree_match" not in out

    routes = retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route(TREE_SHA)
    routes[("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}")] = commit_tree_route("")
    client, _ = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert "unmerged_commit_tree_match" not in out


def test_pr_retarget_unknown_old_base_with_ahead_by_still_refuses_unmerged():
    routes = {("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(
        {"state": "open", "merged": False, "html_url": f"https://github.com/{REPO}/pull/{NUMBER}",
         "base": {}, "head": {"ref": "feat/x", "sha": HEAD_SHA}}),
        ("GET", f"/repos/{REPO}/compare/main..."): reply({"ahead_by": 2, "commits": []})}
    client, stub = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_unmerged_base" and out["ok"] is False
    assert "tree_compare" not in out
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_old_tip_first_missing_second_present_refuses_write():
    routes = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/p"), pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = [
        reply({"message": "missing"}, status=404),
        ref_route(OLD_TIP),
    ]
    client, stub = client_for(routes)
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr"
    assert "could not be verified consistently" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_tree_compare_records_tip_shas():
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 2}, tree_match=True,
                                             pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["tree_compare"]["new_base_tip_sha"] == MAIN_TIP
    assert out["tree_compare"]["old_base_tip_sha"] == OLD_TIP
    assert out["old_base_tip_sha"] == OLD_TIP


def test_subprocess_cli_without_injected_client():
    proc = subprocess.run([sys.executable, "-S", str(ROOT / "gh_ops.py"), "pr-retarget", "bad repo", "1"],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 2
    assert "repository must look like" in proc.stderr


OTHER_TIP = "e" * 40
OTHER_HEAD = "f" * 40


def _matrix_cells():
    """Decision-table cells for pr-retarget. Each cell is one observable outcome.

    Axes (fail-closed; --write is the only path that may PATCH):
    PR state open/closed/merged; base match/mismatch; missing base ref;
    compare ok/404/500; ahead_by 0/>0/null/missing/invalid/negative;
    old-base tip same/both missing/first missing/second missing/SHA changed/no SHA;
    tree equal/different/empty new/empty old/empty both;
    allow-unmerged and allow-compare each on/off; --write on/off;
    pre-PATCH PR same/closed/merged/base changed/head changed;
    --expect-head-sha absent/match/mismatch; post-PATCH base match/mismatch.
    """
    rows = []

    def add(name, **overrides):
        cell = {"state": "open", "merged": False, "old": "feat/p", "new": "main", "write": True,
                "allow_u": False, "allow_c": False, "compare": "ok", "ahead": 0, "tips": "same",
                "trees": "differ", "fresh": "same", "expect": None, "after": "new", "ncommits": 0}
        cell.update(overrides)
        cell["id"] = name
        rows.append(cell)

    for write in (False, True):
        add(f"closed-write-{int(write)}", state="closed", write=write)
        add(f"merged-write-{int(write)}", state="closed", merged=True, write=write)
        add(f"already-write-{int(write)}", old="main", new="main", write=write)
    for compare, ahead in (("ok", 0), ("ok", 2), ("404", 0), ("500", 0), ("ok", None), ("ok", "missing"),
                           ("ok", "bad"), ("ok", -1)):
        add(f"dry-{compare}-{ahead}", write=False, compare=compare, ahead=ahead, tips="same")
    add("dry-risky-allows-expect", write=False, ahead=2, allow_u=True, allow_c=True, expect="match", ncommits=6)
    for ahead in (0, 2):
        add(f"write-ok-{ahead}", ahead=ahead, trees="differ")
    for compare in ("404", "500"):
        add(f"write-{compare}", compare=compare, tips="both_missing")
    add("write-null-ahead", ahead=None)
    add("write-missing-ahead", ahead="missing")
    add("write-bad-ahead", ahead="bad")
    add("write-negative-ahead", ahead=-1)
    add("write-negative-ahead-allow", ahead=-1, allow_c=True)
    for allow_u, allow_c in ((True, False), (False, True), (True, True)):
        add(f"unmerged-flags-u{int(allow_u)}-c{int(allow_c)}", ahead=2, allow_u=allow_u, allow_c=allow_c)
    for allow_u, allow_c in ((False, False), (True, False), (False, True), (True, True)):
        add(f"compare404-flags-u{int(allow_u)}-c{int(allow_c)}", compare="404", tips="both_missing",
            allow_u=allow_u, allow_c=allow_c)
    add("compare404-ref-still-present", compare="404", tips="same", allow_c=True)
    for tips in ("same", "both_missing", "first_missing", "second_missing", "changed", "both_nosha"):
        add(f"tips-{tips}", ahead=0, tips=tips)
    add("tips-both-missing-allow-compare-ignored", ahead=0, tips="both_missing", allow_c=True)
    for trees in ("differ", "equal", "empty_new", "empty_old", "empty_both"):
        add(f"tree-{trees}", ahead=2, trees=trees, tips="same")
    add("tree-equal-allow-unmerged", ahead=2, trees="equal", allow_u=True)
    for fresh in ("closed", "merged", "base_changed", "head_changed"):
        add(f"fresh-{fresh}", ahead=0, fresh=fresh)
    add("expect-match", expect="match")
    add("expect-mismatch", expect="mismatch")
    add("verify-mismatch", after="old")
    add("missing-old-base-ahead", old=None, ahead=2)
    add("missing-old-base-zero", old=None, ahead=0)
    add("missing-old-base-compare-404-allow", old=None, compare="404", allow_c=True)
    add("subjects-truncated", ahead=0, ncommits=6, write=False)
    return rows


def _matrix_compare_failed(cell) -> bool:
    if cell["compare"] != "ok":
        return True
    ahead = cell["ahead"]
    if ahead is None or ahead in ("missing", "bad"):
        return True
    if isinstance(ahead, int) and ahead < 0:
        return True
    return not isinstance(ahead, int)


def _matrix_compare_error(cell) -> str | None:
    ahead = cell["ahead"]
    if ahead == "bad":
        return "invalid ahead_by in compare response"
    if ahead is None:
        return "null ahead_by in compare response"
    if ahead == "missing":
        return "missing ahead_by in compare response"
    if isinstance(ahead, int) and ahead < 0:
        return "negative ahead_by in compare response"
    return None


def _retarget_spec(cell):
    """Fail-closed expected outcome. PATCH only when every write guard passes."""
    if cell["state"] != "open":
        return {"status": "not_open", "ok": False, "exit": 1, "patches": 0}
    if cell["old"] == cell["new"]:
        return {"status": "already_satisfied", "ok": True, "exit": 0, "patches": 0}
    compare_failed = _matrix_compare_failed(cell)
    ahead = cell["ahead"] if isinstance(cell["ahead"], int) and cell["ahead"] >= 0 else 0
    risky = (not compare_failed) and ahead > 0
    if risky and cell["old"]:
        tips_ok = cell["tips"] in ("same", "changed", "second_missing")
        if tips_ok and cell["trees"] == "equal":
            risky = False
    if not cell["write"]:
        return {"status": "planned", "ok": True, "exit": 0, "patches": 0}
    if compare_failed and not cell["allow_c"]:
        return {"status": "refused_compare_failure", "ok": False, "exit": 1, "patches": 0}
    if risky and not cell["allow_u"]:
        return {"status": "refused_unmerged_base", "ok": False, "exit": 1, "patches": 0}
    tip = cell["tips"] if cell["old"] else "both_missing"
    allow_both = compare_failed and cell["allow_c"]
    if tip in ("both_missing", "both_nosha"):
        if not allow_both:
            return {"status": "refused_stale_pr", "ok": False, "exit": 1, "patches": 0}
    elif tip != "same":
        return {"status": "refused_stale_pr", "ok": False, "exit": 1, "patches": 0}
    if cell["fresh"] != "same" or cell["expect"] == "mismatch":
        return {"status": "refused_stale_pr", "ok": False, "exit": 1, "patches": 0}
    if cell["after"] == "old":
        return {"status": "verification_mismatch", "ok": False, "exit": 1, "patches": 1}
    return {"status": "verified", "ok": True, "exit": 0, "patches": 1}


def _matrix_pr(cell, *, base_ref, state, merged, head_sha):
    if base_ref is None:
        return {"state": state, "merged": merged, "html_url": f"https://github.com/{REPO}/pull/{NUMBER}",
                "base": {}, "head": {"ref": "feat/x", "sha": head_sha}}
    return pr_payload(base_ref=base_ref, state=state, merged=merged, head_sha=head_sha)


def _matrix_routes(cell):
    old, new = cell["old"], cell["new"]
    initial = _matrix_pr(cell, base_ref=old, state=cell["state"], merged=cell["merged"], head_sha=HEAD_SHA)
    fresh_state, fresh_merged, fresh_base, fresh_head = cell["state"], cell["merged"], old, HEAD_SHA
    if cell["fresh"] == "closed":
        fresh_state, fresh_merged = "closed", False
    elif cell["fresh"] == "merged":
        fresh_state, fresh_merged = "closed", True
    elif cell["fresh"] == "base_changed":
        fresh_base = "other"
    elif cell["fresh"] == "head_changed":
        fresh_head = OTHER_HEAD
    fresh = _matrix_pr(cell, base_ref=fresh_base, state=fresh_state, merged=fresh_merged, head_sha=fresh_head)
    after_base = new if cell["after"] == "new" else old
    after = _matrix_pr(cell, base_ref=after_base, state="open", merged=False, head_sha=HEAD_SHA)
    routes = {
        ("GET", f"/repos/{REPO}/pulls/{NUMBER}"): [reply(initial), reply(fresh), reply(after)],
        ("PATCH", f"/repos/{REPO}/pulls/{NUMBER}"): reply({}),
    }
    compare_old = old or ""
    compare_key = ("GET", compare_path(compare_old, new))
    if cell["compare"] == "404":
        routes[compare_key] = reply({"message": "missing"}, status=404)
    elif cell["compare"] == "500":
        routes[compare_key] = reply({"message": "boom"}, status=500)
    elif cell["ahead"] == "missing":
        routes[compare_key] = reply({"commits": []})
    elif cell["ahead"] == "bad":
        routes[compare_key] = reply({"ahead_by": "nope", "commits": []})
    elif cell["ahead"] is None:
        routes[compare_key] = reply({"ahead_by": None, "commits": []})
    else:
        commits = [{"commit": {"message": ("  padded subject  " if index == 0 else f"subject {index}")}}
                   for index in range(cell["ncommits"])]
        routes[compare_key] = reply({"ahead_by": cell["ahead"], "commits": commits})
    compare_failed = _matrix_compare_failed(cell)
    ahead_for_tree = cell["ahead"] if isinstance(cell["ahead"], int) else 0
    will_tree = (not compare_failed) and ahead_for_tree > 0 and bool(old)
    present, changed = ref_route(OLD_TIP), ref_route(OTHER_TIP)
    missing, nosha = reply({"message": "missing"}, status=404), reply({"object": {}})
    tip_rows = {
        "same": (present, present, present),
        "both_missing": (missing, missing, missing),
        "first_missing": (missing, missing, present),
        "second_missing": (present, present, missing),
        "changed": (present, present, changed),
        "both_nosha": (nosha, nosha, nosha),
    }
    if old:
        first, tree_tip, fresh_tip = tip_rows[cell["tips"]]
        script = [first, tree_tip, fresh_tip] if will_tree else [first, fresh_tip]
        routes[("GET", f"/repos/{REPO}/git/ref/heads/{urllib.parse.quote(old, safe='')}")] = script
    if will_tree:
        routes[("GET", f"/repos/{REPO}/git/ref/heads/{urllib.parse.quote(new, safe='')}")] = ref_route(MAIN_TIP)
        trees = {
            "differ": (TREE_SHA, "u" * 40),
            "equal": (TREE_SHA, TREE_SHA),
            "empty_new": ("", TREE_SHA),
            "empty_old": (TREE_SHA, ""),
            "empty_both": ("", ""),
        }
        base_tree, old_tree = trees[cell["trees"]]
        routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route(base_tree)
        routes[("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}")] = commit_tree_route(old_tree)
    return routes


def _matrix_argv(cell):
    argv = ["--json", "pr-retarget", REPO, str(NUMBER), "--base", cell["new"]]
    if cell["write"]:
        argv.append("--write")
    if cell["allow_u"]:
        argv.append("--allow-unmerged-commits")
    if cell["allow_c"]:
        argv.append("--allow-compare-failure")
    if cell["expect"] == "match":
        argv.extend(["--expect-head-sha", HEAD_SHA])
    elif cell["expect"] == "mismatch":
        argv.extend(["--expect-head-sha", "a" * 40])
    return argv


@pytest.mark.parametrize("cell", _matrix_cells(), ids=lambda cell: cell["id"])
def test_pr_retarget_decision_matrix(cell, capsys):
    expected = _retarget_spec(cell)
    client, stub = client_for(_matrix_routes(cell))
    code = gh_ops.main(_matrix_argv(cell), client=client)
    payload = json.loads(capsys.readouterr().out)
    patches = sum(1 for call in stub.calls if call["method"] == "PATCH")
    assert payload["status"] == expected["status"]
    assert payload["ok"] is expected["ok"]
    assert code == expected["exit"]
    assert patches == expected["patches"]
    if cell["id"] == "subjects-truncated":
        assert len(payload["old_base_commit_subjects"]) == 5
        assert payload["old_base_commit_subjects"][0] == "padded subject"
    if cell["compare"] == "500" and cell["write"]:
        assert payload["compare_error"] != "not_found"
        assert "500" in payload["compare_error"]
    if cell["id"] == "fresh-head_changed":
        assert HEAD_SHA[:12] in payload["reason"] and OTHER_HEAD[:12] in payload["reason"]
    if cell["id"] == "expect-mismatch":
        assert "does not match" in payload["reason"]
    if cell["id"] == "fresh-base_changed":
        assert "base_ref changed" in payload["reason"]
    expected_compare_error = _matrix_compare_error(cell)
    if expected_compare_error is not None:
        assert payload["compare_error"] == expected_compare_error


@pytest.mark.parametrize("name,message", [
    ("-x", "must not start with -"),
    ("feat/", "must not start or end with /"),
    ("/a", "must not start or end with /"),
    ("a//b", "must not contain //"),
    ("a.", "must not start or end with ."),
    (".a", "must not start or end with ."),
    ("a.lock", "must not end with .lock"),
    ("a/.b", "must not start or end with ."),
    ("a.lock/b", "must not end with .lock"),
    ("@", "invalid characters"),
    ("a~1", "invalid characters"),
    ("a:b", "invalid characters"),
    ("a^1", "invalid characters"),
    ("a?b", "invalid characters"),
    ("a*b", "invalid characters"),
    ("a[b", "invalid characters"),
    ("a@{1", "invalid characters"),
    ("a\\b", "invalid characters"),
    ("a\tb", "invalid characters"),
    ("a\x7fb", "invalid characters"),
    ("a\nb", "invalid characters"),
    ("refs/heads/x", "not a refs/ path"),
    ("a..b", "must not contain .."),
    ("  ", "is required"),
    ("", "is required"),
])
def test_pr_retarget_invalid_branch_exits_2_without_patch(name, message, capsys):
    client, stub = client_for({})
    code = gh_ops.main(["--json", "pr-retarget", REPO, str(NUMBER), f"--base={name}", "--write"], client=client)
    assert code == 2
    assert message in capsys.readouterr().err
    assert stub.calls == []


def test_other_commands_do_not_take_pr_retarget_dispatch():
    """Cover the command-dispatch arms that are not pr-retarget (branch coverage)."""
    assert gh_ops.main(["url", "compare", REPO, "main", "feature"]) == 0
    rendered = gh_ops._render("pr-edit", {"ok": True, "dry_run": True, "reason": ""})
    assert rendered.startswith("OK pr-edit")
    warning = "feat/p has 1 commit(s) not in main; they would join the diff"
    warned = gh_ops._render("pr-retarget", {
        "status": "planned", "repository": REPO, "number": NUMBER,
        "from": "feat/p", "to": "main", "warning": warning,
    })
    assert warned == f"planned {REPO}#{NUMBER} base feat/p -> main ({warning})"
    assert gh_ops._render("pr-retarget", {
        "status": "planned", "repository": REPO, "number": NUMBER,
        "from": None, "to": "main",
    }) == f"planned {REPO}#{NUMBER} base (unknown) -> main"


def test_validate_branch_segment_rejects_empty_segment():
    with pytest.raises(gh_ops.GhOpsError, match="must not contain empty path segments"):
        gh_ops._validate_branch_segment("", field="base")

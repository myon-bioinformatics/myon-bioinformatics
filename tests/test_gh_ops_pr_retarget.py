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
            "base": {"ref": "main"}, "head": {"ref": "feat/x", "sha": "b" * 40}}
    if "base_ref" in over:
        data["base"] = {"ref": over.pop("base_ref")}
    data.update(over)
    return data


def retarget_routes(old_base, compare, *, after_base="main", pr_over=None, compare_raises=False):
    pr_over = pr_over or {}
    gets = [reply(pr_payload(base_ref=old_base, **pr_over)),
            reply(pr_payload(base_ref=after_base, **pr_over))]
    routes = {("GET", f"/repos/{REPO}/pulls/{NUMBER}"): gets, ("PATCH", f"/repos/{REPO}/pulls/{NUMBER}"): reply({})}
    compare_path = (f"/repos/{REPO}/compare/{urllib.parse.quote('main', safe='')}"
                    f"...{urllib.parse.quote(old_base, safe='')}")
    if compare_raises:
        def _missing(_parts, _body):
            raise gh_ops.GhOpsError("GET /repos/octo/demo/compare/main...gone -> HTTP 404: not found")
        routes[("GET", compare_path)] = _missing
    else:
        routes[("GET", compare_path)] = reply(compare)
    return routes


def test_commit_message_subject_empty_and_whitespace():
    assert gh_ops._commit_message_subject("") == "(empty commit message)"
    assert gh_ops._commit_message_subject("   \n\t\n") == "(empty commit message)"
    assert gh_ops._commit_message_subject(None) == "(empty commit message)"
    assert gh_ops._commit_message_subject("subject\n\nbody") == "subject"


def test_pr_retarget_plans_refuses_and_writes():
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["ok"] is True and out["status"] == "planned" and out["dry_run"] is True
    assert out["old_base_unmerged_commits"] == 0 and "warning" not in out
    assert stub.calls == [{"method": "GET", "path": f"/repos/{REPO}/pulls/{NUMBER}", "body": None},
                          {"method": "GET", "path": f"/repos/{REPO}/compare/main...feat%2Fparent", "body": None}]

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["ok"] is True and out["status"] == "verified"
    patch_calls = [call for call in stub.calls if call["method"] == "PATCH"]
    assert patch_calls == [{"method": "PATCH", "path": f"/repos/{REPO}/pulls/{NUMBER}", "body": {"base": "main"}}]

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 3}))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["ok"] is False and out["status"] == "refused_unmerged_base"
    assert all(call["method"] == "GET" for call in stub.calls)

    client, stub = client_for(retarget_routes("gone", {}, after_base="gone", compare_raises=True))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", write=True, allow_unmerged=True, client=client)
    assert out["status"] == "verification_mismatch" and out["compare_error"] == "not_found"
    assert out["warning"] == "could not compare gone with main"


def test_pr_retarget_empty_compare_commit_messages_do_not_crash():
    compare = {"ahead_by": 2, "commits": [
        {"commit": {"message": ""}},
        {"commit": {"message": "   \n"}},
        {"commit": {"message": "real subject\n\nbody"}},
    ]}
    client, _ = client_for(retarget_routes("feat/parent", compare))
    out = gh_ops.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["old_base_commit_subjects"] == ["(empty commit message)", "(empty commit message)", "real subject"]


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


def test_pr_retarget_cli_dry_run_and_write_exit_codes(capsys):
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 1}))
    assert gh_ops.main(["pr-retarget", REPO, str(NUMBER), "--base", "main"], client=client) == 0
    assert capsys.readouterr().out.startswith(f"planned {REPO}#{NUMBER} base feat/p -> main")

    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 1}))
    assert gh_ops.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write"], client=client) == 1
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


def test_subprocess_cli_without_injected_client(capsys):
    """gh_ops.py pr-retarget fails fast on bad input without network (exit 2)."""
    proc = subprocess.run([sys.executable, "-S", str(ROOT / "gh_ops.py"), "pr-retarget", "bad repo", "1"],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 2
    assert "repository must look like" in proc.stderr

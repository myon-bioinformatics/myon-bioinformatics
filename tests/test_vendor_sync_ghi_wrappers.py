"""vendor_sync identity wrappers delegate to the vendored gh_identity."""
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

import vendor_sync as sync

ROOT = Path(sync.__file__).resolve().parent


def test_vendored_gh_identity_loads_under_python_S():
    code = ("import sys; sys.path.insert(0, %r); import vendor_sync as s; g = s._gh_identity(); "
            "print(all(hasattr(g, n) for n in ('list_owner_repositories', 'commit_pulls', 'request_stub', 'operation')))"
            % str(ROOT))
    out = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True"


def test_wrapper_is_read_only():
    g = sync._gh_identity()
    with pytest.raises(ValueError, match="read-only"):
        sync._ghi_call(g.request, "POST", "repos/x/y", fetch=lambda url: pytest.fail("network"))


def test_lookup_child_main_pull_number_uses_commit_pulls(monkeypatch):
    seen = []
    def fake_json(url, token=None):
        seen.append(url)
        return [{"number": 12, "merged_at": "t", "base": {"ref": "main"}, "head": {"sha": "a" * 40}}]
    monkeypatch.setattr(sync, "_github_json", fake_json)
    assert sync._lookup_child_main_pull_number("owner/tool", "a" * 40, "token") == 12
    assert seen and "/repos/owner/tool/commits/" + "a" * 40 + "/pulls" in seen[0]


def test_lookup_child_main_pull_number_http_error_is_none(monkeypatch):
    def fake_json(url, token=None):
        raise HTTPError(url, 404, "nf", None, None)
    monkeypatch.setattr(sync, "_github_json", fake_json)
    assert sync._lookup_child_main_pull_number("owner/tool", "a" * 40, "token") is None


def test_list_account_repositories_keeps_forks_and_rejects_truncation(monkeypatch):
    rows = [{"full_name": "acct/fork", "visibility": "public", "private": False, "archived": False, "fork": True},
            {"full_name": "other/x", "visibility": "public", "private": False, "archived": False}]
    monkeypatch.setattr(sync, "_github_api", lambda m, url, token=None, body=None: rows)
    assert sync.list_org_repositories("acct", token="token") == ["acct/fork"]
    full = [{"full_name": "acct/r%d" % i, "visibility": "public", "private": False, "archived": False}
            for i in range(100)]
    monkeypatch.setattr(sync, "_github_api", lambda m, url, token=None, body=None: full)
    with pytest.raises(ValueError, match="truncated"):
        sync.list_org_repositories("acct", token="token")


def test_bare_fetch_first_parent_delegates_to_gh_identity(tmp_path, monkeypatch):
    up = tmp_path / "up"
    up.mkdir()
    def run(*a):
        return subprocess.run(["git", "-C", str(up), "-c", "user.email=t@e", "-c", "user.name=t", *a],
                              check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q", "-b", "main"); (up / "f").write_text("1"); run("add", "f"); run("commit", "-qm", "1")
    run("switch", "-q", "-c", "pr"); (up / "g").write_text("2"); run("add", "g"); run("commit", "-qm", "2")
    inter = run("rev-parse", "HEAD")
    run("switch", "-q", "main"); run("merge", "-q", "--no-ff", "-m", "m", "pr")
    merge = run("rev-parse", "HEAD")
    g = sync._gh_identity()
    seen = []
    real = g.first_parent_check
    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(g, "first_parent_check", spy)
    remotes = {"owner/tool": str(up)}
    assert sync._commit_merged_via_git_bare_fetch("owner/tool", merge, remotes=remotes) is True
    with pytest.raises(ValueError, match="not merged"):
        sync._commit_merged_via_git_bare_fetch("owner/tool", inter, remotes=remotes)
    with pytest.raises(ValueError) as error:
        sync._commit_merged_via_git_bare_fetch("owner/tool", "1" * 40, remotes=remotes)
    assert str(error.value) == sync.PUBLIC_GIT_FETCH_ERROR
    assert seen and all(k["git_runner"] is sync._parent_git_runner and k["exact"] for k in seen)

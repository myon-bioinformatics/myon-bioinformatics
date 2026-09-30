import os
import subprocess
from pathlib import Path

import pytest

import git_inspector as gi


def git(root, *args, input=None):
    return subprocess.run(["git", "-C", str(root), *args], input=input,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Fixture User")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "space 日本語.txt").write_text("hello\n", encoding="utf-8")
    git(tmp_path, "add", "--", "a.txt", "space 日本語.txt")
    git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def test_status_clean_modified_staged_untracked_and_rename(repo):
    assert gi.status(repo) == {"clean": True, "records": [], "truncated": False}
    (repo / "a.txt").write_text("changed\ntwo\n", encoding="utf-8")
    (repo / "new file.txt").write_text("new", encoding="utf-8")
    dirty = gi.status(repo)
    assert not dirty["clean"]
    assert {r["kind"] for r in dirty["records"]} >= {"1", "?"}
    git(repo, "add", "--", "a.txt")
    git(repo, "mv", "space 日本語.txt", "renamed 日本語.txt")
    staged = gi.status(repo)
    assert any(r["kind"] == "2" for r in staged["records"])


def test_ls_files_is_nul_safe_and_bounded(repo):
    result = gi.ls_files(repo)
    assert result["paths"] == ["a.txt", "space 日本語.txt"]
    assert not result["truncated"]
    assert gi.ls_files(repo, max_files=1)["truncated"]


def test_diff_worktree_staged_revision_path_and_truncation(repo):
    first = git(repo, "rev-parse", "HEAD").strip()
    (repo / "a.txt").write_text("changed\ntwo\n", encoding="utf-8")
    worktree = gi.diff(repo, path="a.txt")
    assert "+changed" in worktree["patch"]
    git(repo, "add", "--", "a.txt")
    assert "+changed" in gi.diff(repo, staged=True)["patch"]
    git(repo, "commit", "-qm", "second")
    second = git(repo, "rev-parse", "HEAD").strip()
    assert "+changed" in gi.diff(repo, base=first, head=second, path="a.txt")["patch"]
    assert gi.diff(repo, base=first, head=second, max_bytes=10)["truncated"]
    with pytest.raises(ValueError):
        gi.diff(repo, staged=True, base=first)
    with pytest.raises(ValueError):
        gi.diff(repo, head=second)


def test_log_show_blame_and_grep(repo):
    rows = gi.log(repo, max_count=1)["commits"]
    assert len(rows) == 1 and rows[0]["subject"] == "initial"
    shown = gi.show(repo, "HEAD", path="a.txt")
    assert shown["content"] == "one\ntwo\n"
    blamed = gi.blame(repo, "a.txt", start=1, end=1)
    assert "\tone" in blamed["porcelain"]
    matches = gi.grep(repo, "hello")
    assert matches["paths"] == ["space 日本語.txt"]
    assert gi.grep(repo, "absent")["paths"] == []


def test_paths_are_option_separated(repo):
    (repo / "--looks-like-option").write_text("x\n", encoding="utf-8")
    git(repo, "add", "--", "--looks-like-option")
    git(repo, "commit", "-qm", "option path")
    assert gi.show(repo, "HEAD", path="--looks-like-option")["content"] == "x\n"
    assert gi.blame(repo, "--looks-like-option")["porcelain"]


@pytest.mark.parametrize("revision", ["", "-n1", "bad\0ref"])
def test_revision_validation(repo, revision):
    with pytest.raises(ValueError):
        gi.show(repo, revision)


def test_errors_are_not_clean(repo, tmp_path, monkeypatch):
    nonrepo = tmp_path / "not-a-repo"
    nonrepo.mkdir()
    with pytest.raises(gi.GitInspectionError):
        gi.status(nonrepo)
    with pytest.raises(ValueError):
        gi.status(tmp_path / "missing")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(gi.GitInspectionError):
        gi.status(repo)


def test_no_mutation_or_network_argv_reachable(monkeypatch, repo):
    seen = []
    real = subprocess.run
    def recording(argv, **kwargs):
        seen.append(tuple(argv))
        return real(argv, **kwargs)
    monkeypatch.setattr(gi.subprocess, "run", recording)
    gi.status(repo)
    gi.ls_files(repo)
    gi.diff(repo)
    gi.log(repo, max_count=1)
    gi.show(repo)
    gi.blame(repo, "a.txt")
    gi.grep(repo, "one")
    forbidden = {"add", "commit", "checkout", "switch", "restore", "reset",
                 "clean", "merge", "rebase", "fetch", "pull", "push"}
    for argv in seen:
        assert not forbidden.intersection(argv)


def test_check_ignore_is_nul_safe_and_distinguishes_not_ignored(repo):
    (repo / ".gitignore").write_text("build/\n*.secret\n", encoding="utf-8")
    result = gi.check_ignore(repo, ["build/out", "space 日本語.txt", "x.secret"])
    assert [row["path"] for row in result["records"]] == [
        "build/out", "space 日本語.txt", "x.secret"]
    assert [row["status"] for row in result["records"]] == [
        "ignored", "not_ignored", "ignored"]
    assert result["records"][0]["pattern"] == "build/"
    assert not result["truncated"]


def test_check_ignore_bounds(repo):
    with pytest.raises(ValueError):
        gi.check_ignore(repo, ["a", "b"], max_paths=1)
    with pytest.raises(ValueError):
        gi.check_ignore(repo, ["long-name"], max_bytes=2)

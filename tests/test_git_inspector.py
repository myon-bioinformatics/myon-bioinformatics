import io
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
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture User")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (root / "space 日本語.txt").write_text("hello\n", encoding="utf-8")
    git(root, "add", "--", "a.txt", "space 日本語.txt")
    git(root, "commit", "-qm", "initial")
    return root


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
    renames = [r for r in staged["records"] if r["kind"] == "2"]
    assert len(renames) == 1
    assert renames[0]["path"] == "renamed 日本語.txt"
    assert renames[0]["orig_path"] == "space 日本語.txt"
    assert "renamed 日本語.txt" in renames[0]["record"]
    assert not any(r["record"] == "space 日本語.txt" for r in staged["records"])


def test_ls_files_is_nul_safe_and_bounded(repo):
    result = gi.ls_files(repo)
    assert result["paths"] == ["a.txt", "space 日本語.txt"]
    assert not result["truncated"]
    assert gi.ls_files(repo, max_files=1)["truncated"]


def test_ls_files_can_include_untracked_but_not_ignored(repo):
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / "new 日本語.txt").write_text("new\n", encoding="utf-8")
    (repo / "ignored.log").write_text("ignored\n", encoding="utf-8")

    tracked = gi.ls_files(repo)
    assert "new 日本語.txt" not in tracked["paths"]
    assert "ignored.log" not in tracked["paths"]

    observed = gi.ls_files(repo, include_untracked=True)
    assert "a.txt" in observed["paths"]
    assert "space 日本語.txt" in observed["paths"]
    assert "new 日本語.txt" in observed["paths"]
    assert "ignored.log" not in observed["paths"]


def test_ls_files_combined_truncation_uses_git_output_prefix(repo):
    (repo / "0new.txt").write_text("new\n", encoding="utf-8")
    full = gi.ls_files(repo, include_untracked=True)
    limited = gi.ls_files(repo, include_untracked=True, max_files=1)
    # Git may emit --others before --cached. The contract is a bounded prefix
    # of Git's combined output, not a promise that tracked paths come first.
    assert limited == {"paths": full["paths"][:1], "truncated": True}
    assert "a.txt" in full["paths"]


def test_ls_files_include_untracked_requires_bool(repo):
    with pytest.raises(TypeError):
        gi.ls_files(repo, include_untracked=1)


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
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with pytest.raises(gi.GitInspectionError):
        gi.status(nonrepo)
    with pytest.raises(ValueError):
        gi.status(tmp_path / "missing")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(gi.GitInspectionError):
        gi.status(repo)


def test_no_mutation_or_network_argv_reachable(monkeypatch, repo):
    seen = []
    real = gi._spawn
    def recording(argv, **kwargs):
        seen.append(tuple(argv))
        return real(argv, **kwargs)
    monkeypatch.setattr(gi, "_spawn", recording)
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


def test_structured_nul_outputs_drop_partial_tail(monkeypatch, repo):
    original_run = gi._run

    def truncated_status(root, args, **kwargs):
        if "status" in args:
            # One complete record followed by a type-2 record whose original
            # path is cut before its terminating NUL.
            raw = (
                b"? complete.txt\0"
                b"2 R. N... 100644 100644 100644 "
                + b"0" * 40 + b" " + b"1" * 40
                + b" R100 renamed.txt\0old-na"
            )
            return raw, True, 0
        return original_run(root, args, **kwargs)

    monkeypatch.setattr(gi, "_run", truncated_status)
    result = gi.status(repo)
    assert result["truncated"] is True
    assert result["records"] == [
        {"kind": "?", "record": "? complete.txt", "path": "complete.txt"}]


def test_nul_inventory_does_not_publish_partial_path(monkeypatch, repo):
    def truncated_ls(root, args, **kwargs):
        return b"a.txt\0partial-na", True, 0

    monkeypatch.setattr(gi, "_run", truncated_ls)
    result = gi.ls_files(repo)
    assert result == {"paths": ["a.txt"], "truncated": True}



def test_log_and_grep_do_not_publish_partial_records(repo):
    log_result = gi.log(repo, max_count=1)
    assert log_result["commits"]
    assert gi.log(repo, max_count=1, max_bytes=5) == {
        "commits": [], "truncated": True}
    assert gi.grep(repo, "hello", max_bytes=3) == {
        "paths": [], "truncated": True}


def test_status_conflict_is_one_logical_record(repo):
    # Build a real content conflict without using inspector mutation paths.
    base = git(repo, "branch", "--show-current").strip()
    git(repo, "checkout", "-qb", "other")
    (repo / "a.txt").write_text("other\n", encoding="utf-8")
    git(repo, "commit", "-am", "other")
    git(repo, "checkout", "-q", base)
    (repo / "a.txt").write_text("main\n", encoding="utf-8")
    git(repo, "commit", "-am", "main")
    proc = subprocess.run(["git", "-C", str(repo), "merge", "other"],
                          capture_output=True, text=True)
    assert proc.returncode != 0
    result = gi.status(repo)
    conflicts = [row for row in result["records"] if row["kind"] == "u"]
    assert len(conflicts) == 1
    assert conflicts[0]["path"] == "a.txt"


def test_truncated_dirty_status_is_never_reported_clean(repo):
    (repo / "a.txt").write_text("dirty\n", encoding="utf-8")
    result = gi.status(repo, max_bytes=1)
    assert result["truncated"] is True
    assert result["clean"] is False


def test_check_ignore_handles_negation_and_truncated_unseen_paths(repo):
    (repo / ".gitignore").write_text("*.log\n!keep.log\n", encoding="utf-8")
    negated = gi.check_ignore(repo, ["drop.log", "keep.log"])
    assert [row["status"] for row in negated["records"]] == [
        "ignored", "not_ignored"
    ]
    keep = negated["records"][1]
    assert keep["pattern"] == "!keep.log"

    truncated = gi.check_ignore(
        repo, ["drop.log", "keep.log", "unseen.txt"], max_bytes=30
    )
    assert truncated["truncated"] is True
    assert all(
        row["status"] != "not_ignored"
        for row in truncated["records"]
        if row["path"] == "unseen.txt"
    )
    assert next(
        row for row in truncated["records"] if row["path"] == "unseen.txt"
    )["status"] == "not_measured"


def test_all_commands_disable_optional_locks_fsmonitor_and_routing_env(monkeypatch, repo):
    seen = []
    real = gi._spawn

    def recording(argv, **kwargs):
        seen.append((tuple(argv), dict(kwargs["env"])))
        return real(argv, **kwargs)

    monkeypatch.setenv("GIT_DIR", "/sentinel/not-this-repo")
    monkeypatch.setenv("GIT_INDEX_FILE", "/sentinel/index")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/sentinel/diff")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'alias.status=!false'")
    monkeypatch.setenv("GIT_COMMON_DIR", "/sentinel/common")
    monkeypatch.setenv("GIT_SHALLOW_FILE", "/sentinel/shallow")
    monkeypatch.setattr(gi, "_spawn", recording)
    gi.status(repo)
    gi.ls_files(repo)
    gi.diff(repo)
    gi.log(repo, max_count=1)
    gi.show(repo)
    gi.blame(repo, "a.txt")
    gi.grep(repo, "one")
    for argv, env in seen:
        assert "--no-optional-locks" in argv
        assert "core.fsmonitor=false" in argv
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert "GIT_DIR" not in env
        assert "GIT_INDEX_FILE" not in env
        assert "GIT_EXTERNAL_DIFF" not in env
        assert "GIT_CONFIG_PARAMETERS" not in env
        assert "GIT_COMMON_DIR" not in env
        assert "GIT_SHALLOW_FILE" not in env
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_CONFIG_SYSTEM"] == os.devnull
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_malformed_complete_type2_is_error(monkeypatch, repo):
    def malformed(root, args, **kwargs):
        return b"2 malformed\\0old.txt\\0", False, 0

    monkeypatch.setattr(gi, "_run", malformed)
    with pytest.raises(gi.GitInspectionError):
        gi.status(repo)


def test_text_observations_are_json_safe_when_utf8_is_cut(monkeypatch, repo):
    def cut(root, args, **kwargs):
        return b"prefix-\xe3\x81", True, 0

    monkeypatch.setattr(gi, "_run", cut)
    result = gi.diff(repo, max_bytes=8)
    assert result["truncated"] is True
    assert "\ufffd" in result["patch"]
    result["patch"].encode("utf-8")


def test_check_ignore_preserves_duplicate_input_positions(repo):
    (repo / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    result = gi.check_ignore(repo, ["a.tmp", "a.tmp", "plain.txt"])
    assert [row["path"] for row in result["records"]] == [
        "a.tmp", "a.tmp", "plain.txt"]
    assert [row["status"] for row in result["records"]] == [
        "ignored", "ignored", "not_ignored"]


def test_run_drains_large_stdout_and_stderr_with_bounded_retention(monkeypatch, repo):
    class FakeProcess:
        def __init__(self):
            self.stdout = io.BytesIO(b"x" * 1_000_000)
            self.stderr = io.BytesIO(b"e" * 1_000_000)
            self.stdin = None
            self.waited = False

        def wait(self):
            self.waited = True
            return 0

    process = FakeProcess()
    monkeypatch.setattr(gi, "_spawn", lambda *args, **kwargs: process)
    raw, truncated, code = gi._run(repo, ["status"], max_bytes=32)
    assert raw == b"x" * 32
    assert truncated is True
    assert code == 0
    assert process.waited is True


def test_run_drains_stderr_before_reporting_failure(monkeypatch, repo):
    class FakeProcess:
        def __init__(self):
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"failure" * 100_000)
            self.stdin = None
            self.waited = False

        def wait(self):
            self.waited = True
            return 7

    process = FakeProcess()
    monkeypatch.setattr(gi, "_spawn", lambda *args, **kwargs: process)
    with pytest.raises(gi.GitInspectionError, match="status 7"):
        gi._run(repo, ["status"], max_bytes=16)
    assert process.waited is True

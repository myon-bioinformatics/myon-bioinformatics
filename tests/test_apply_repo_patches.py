"""Real local Git regressions; no network, credentials, or external service."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply_repo_patches.py"
spec = importlib.util.spec_from_file_location("apply_repo_patches", SCRIPT)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Isolate test repos from developer signing/hooks/global Git configuration.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    root = tmp_path / "repo space 日本語"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Patch Test")
    git(root, "config", "user.email", "patch-test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "file.txt").write_text("".join(f"line {i}\n" for i in range(20)), encoding="utf-8")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "base")
    return root


def mailbox(repo, tmp_path, *, conflict=False, fallback=False):
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "switch", "-c", "patch-source")
    file = repo / "file.txt"
    file.write_text(file.read_text().replace("line 5\n", "patched 5\n"))
    git(repo, "commit", "-am", "Update shared pin")
    patch = tmp_path / "patch space 日本語.mbox"
    patch.write_text(git(repo, "format-patch", "--stdout", "--full-index", "-1").stdout, encoding="utf-8")
    git(repo, "switch", "main")
    if conflict or fallback:
        line = 5 if conflict else 8
        file.write_text(file.read_text().replace(f"line {line}\n", f"local {line}\n"))
        git(repo, "commit", "-am", "Keep local change")
    return patch, base


def args(repo, patch, tmp_path, apply=False):
    result = ["--target", str(repo), str(patch), "--branch", "chore/shared-pins",
              "--worktree-root", str(tmp_path / "worktrees")]
    return result + (["--apply"] if apply else [])


def test_plan_is_read_only(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    before = git(repo, "show-ref").stdout
    assert runner.main(args(repo, patch, tmp_path)) == 0
    assert git(repo, "show-ref").stdout == before
    assert not (tmp_path / "worktrees").exists()


def test_apply_preserves_original_checkout(repo, tmp_path):
    patch, base = mailbox(repo, tmp_path)
    assert runner.main(args(repo, patch, tmp_path, True)) == 0
    target = tmp_path / "worktrees" / repo.name
    assert "patched 5" in (target / "file.txt").read_text()
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == base
    assert git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert "patched 5" not in (repo / "file.txt").read_text()
    assert not git(target, "status", "--porcelain").stdout


def test_real_three_way_fallback_preserves_independent_change(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path, fallback=True)
    assert git(repo, "apply", "--check", str(patch), check=False).returncode != 0
    assert runner.main(args(repo, patch, tmp_path, True)) == 0
    text = (tmp_path / "worktrees" / repo.name / "file.txt").read_text()
    assert "patched 5" in text and "local 8" in text


def test_conflict_stops_and_keeps_main_unchanged(repo, tmp_path, capsys):
    patch, _ = mailbox(repo, tmp_path, conflict=True)
    before = git(repo, "rev-parse", "HEAD").stdout
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    target = tmp_path / "worktrees" / repo.name
    assert git(target, "ls-files", "--unmerged").stdout
    assert git(repo, "rev-parse", "HEAD").stdout == before
    assert "APPLIED" not in capsys.readouterr().out
    git(target, "am", "--abort")
    assert git(target, "rev-parse", "HEAD").stdout == before


@pytest.mark.parametrize("kind", ["modified", "staged", "untracked"])
def test_dirty_source_is_rejected(repo, tmp_path, kind):
    patch, _ = mailbox(repo, tmp_path)
    path = repo / ("new.txt" if kind == "untracked" else "file.txt")
    path.write_text("uncommitted\n")
    if kind == "staged":
        git(repo, "add", str(path))
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert not (tmp_path / "worktrees").exists()
    assert path.read_text() == "uncommitted\n"


def test_all_targets_preflight_before_any_mutation(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True) + ["--target", str(tmp_path / "missing"), str(patch)]
    assert runner.main(argv) == 1
    assert not (tmp_path / "worktrees").exists()
    assert not git(repo, "for-each-ref", "refs/heads/chore/shared-pins").stdout


def test_existing_branch_is_not_overwritten(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    git(repo, "branch", "chore/shared-pins")
    before = git(repo, "show-ref").stdout
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert git(repo, "show-ref").stdout == before


def test_duplicate_target_is_rejected(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True) + ["--target", str(repo), str(patch)]
    assert runner.main(argv) == 1
    assert not (tmp_path / "worktrees").exists()


def test_empty_patch_is_rejected(repo, tmp_path):
    patch = tmp_path / "empty.mbox"
    patch.touch()
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert not (tmp_path / "worktrees").exists()


def test_output_inside_source_is_rejected(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True)
    argv[argv.index("--worktree-root") + 1] = str(repo / "output")
    assert runner.main(argv) == 1
    assert not (repo / "output").exists()


def test_second_application_does_not_duplicate_commits(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True)
    assert runner.main(argv) == 0
    before = git(repo, "show-ref").stdout
    assert runner.main(argv) == 1
    assert git(repo, "show-ref").stdout == before


def test_inherited_index_override_does_not_hide_dirty_source(repo, tmp_path, monkeypatch):
    patch, _ = mailbox(repo, tmp_path)
    (repo / "file.txt").write_text("dirty\n")
    bogus = tmp_path / "unrelated-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(bogus))
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert not bogus.exists()


def test_stdlib_only_help():
    proc = subprocess.run([sys.executable, "-S", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--apply" in proc.stdout


def test_invalid_branch_fails_before_mutation(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    argv = args(repo, patch, tmp_path, True)
    argv[argv.index("--branch") + 1] = "bad..branch"
    assert runner.main(argv) == 1
    assert not (tmp_path / "worktrees").exists()


def test_pending_operation_is_not_overwritten(repo, tmp_path):
    patch, _ = mailbox(repo, tmp_path)
    marker = repo / git(repo, "rev-parse", "--git-path", "rebase-apply").stdout.strip()
    marker.mkdir()
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert marker.is_dir()
    assert not (tmp_path / "worktrees").exists()


def test_malformed_mailbox_is_not_reported_applied(repo, tmp_path, capsys):
    patch = tmp_path / "malformed.mbox"
    patch.write_text("This is not a format-patch mailbox.\n")
    before = git(repo, "rev-parse", "HEAD").stdout
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert git(repo, "rev-parse", "HEAD").stdout == before
    assert "APPLIED" not in capsys.readouterr().out


def test_batch_stops_on_conflict_without_touching_later_targets(repo, tmp_path, capsys):
    patch, _ = mailbox(repo, tmp_path)
    second, third = tmp_path / "second", tmp_path / "third"
    for root in (second, third):
        git(tmp_path, "clone", str(repo), str(root))
        git(root, "config", "user.name", "Patch Test")
        git(root, "config", "user.email", "patch-test@example.invalid")
        git(root, "config", "commit.gpgsign", "false")
    file = second / "file.txt"
    file.write_text(file.read_text().replace("line 5\n", "conflicting line\n"))
    git(second, "commit", "-am", "Conflict")
    before = {root: git(root, "rev-parse", "HEAD").stdout for root in (repo, second, third)}
    argv = args(repo, patch, tmp_path, True)
    argv += ["--target", str(second), str(patch), "--target", str(third), str(patch)]
    assert runner.main(argv) == 1
    assert capsys.readouterr().out.count("APPLIED ") == 1
    assert (tmp_path / "worktrees" / repo.name).exists()
    assert (tmp_path / "worktrees" / second.name).exists()
    assert not (tmp_path / "worktrees" / third.name).exists()
    for root in (repo, second, third):
        assert git(root, "rev-parse", "HEAD").stdout == before[root]
    assert not git(third, "for-each-ref", "refs/heads/chore/shared-pins").stdout


def test_missing_git_is_reported_without_traceback(repo, tmp_path, monkeypatch, capsys):
    patch, _ = mailbox(repo, tmp_path)
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("git not available")
    monkeypatch.setattr(runner.subprocess, "run", missing)
    assert runner.main(args(repo, patch, tmp_path, True)) == 1
    assert "Traceback" not in capsys.readouterr().err

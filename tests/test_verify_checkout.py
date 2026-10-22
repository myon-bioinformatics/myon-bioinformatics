"""Real Git tests for ordered verification of an immutable checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import verify_checkout as verifier


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   env=verifier._env(), text=True).strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Do not inherit developer hooks, signing, or Git directory/index overrides.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root = tmp_path / "repo space 日本語"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Verifier Test")
    git(root, "config", "user.email", "verifier@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "value.txt").write_text("committed", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "base")
    return root


def command(code):
    return ["{python}", "-S", "-c", code]


def test_verifies_committed_bytes_preserves_dirty_checkout_and_cleans_worktree(repo):
    head = git(repo, "rev-parse", "HEAD")
    before = git(repo, "worktree", "list", "--porcelain")
    (repo / "value.txt").write_text("dirty", encoding="utf-8")
    result = verifier.verify(repo, "main", [head], [command(
        "from pathlib import Path; assert Path('value.txt').read_text() == 'committed'; "
        "Path('artifact.txt').write_text('result'); print('ok')")])
    assert result["ok"] and result["head"] == head
    assert result["tree"] == git(repo, "rev-parse", "HEAD^{tree}")
    assert result["commands"][0]["stdout_tail"] == "ok" + os.linesep
    assert (repo / "value.txt").read_text() == "dirty"
    assert not (repo / "artifact.txt").exists()
    assert git(repo, "worktree", "list", "--porcelain") == before
    assert git(repo, "branch", "--show-current") == "main"


def test_unrelated_known_commit_runs_no_commands(repo, tmp_path):
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--orphan", "unrelated")
    git(repo, "commit", "-m", "unrelated")
    other = git(repo, "rev-parse", "HEAD")
    marker = tmp_path / "must-not-run"
    result = verifier.verify(repo, head, [other], [command(
        "from pathlib import Path; Path(%r).touch()" % str(marker))])
    assert not result["ok"] and not result["ancestry_passed"]
    assert result["commands"] == [] and not marker.exists()


@pytest.mark.parametrize("first,status", [
    (command("raise SystemExit(7)"), "fail"),
    (command("import time; time.sleep(10)"), "timeout"),
    (["no-such-verifier-program-4829"], "spawn_error"),
])
def test_failure_stops_following_commands_and_cleans_worktree(repo, tmp_path, first, status):
    before = git(repo, "worktree", "list", "--porcelain")
    marker = tmp_path / "must-not-run"
    result = verifier.verify(repo, "HEAD", [], [first, command(
        "from pathlib import Path; Path(%r).touch()" % str(marker))], timeout=0.1)
    assert not result["ok"] and len(result["commands"]) == 1
    assert result["commands"][0]["status"] == status
    assert not marker.exists()
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_logs_are_bounded(repo):
    head = git(repo, "rev-parse", "HEAD")
    report = verifier.verify(repo, "main", [], [command(
        "print('x' * 20000); import sys; print('y' * 20000, file=sys.stderr)")])
    assert report["ok"] and report["head"] == head
    row = report["commands"][0]
    for name in ("stdout", "stderr"):
        assert row[name + "_truncated"]
        assert len(row[name + "_tail"].encode()) == 8192


def test_moving_input_ref_does_not_change_tested_revision(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "value.txt").write_text("new commit", encoding="utf-8")
    git(repo, "commit", "-am", "second")
    newer = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", "moving", base)
    result = verifier.verify(repo, "moving", [], [command(
        "import subprocess; subprocess.run(['git', 'update-ref', "
        "'refs/heads/moving', %r], check=True)" % newer), command(
        "from pathlib import Path; assert Path('value.txt').read_text() == 'committed'")])
    assert result["ok"] and result["head"] == result["final_head"] == base
    assert git(repo, "rev-parse", "moving") == newer


def test_test_command_switching_head_cannot_report_success(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "value.txt").write_text("new commit", encoding="utf-8")
    git(repo, "commit", "-am", "second")
    before = git(repo, "worktree", "list", "--porcelain")
    report = verifier.verify(repo, "main", [], [command(
        "import subprocess; subprocess.run(['git', 'checkout', '--detach', %r], check=True)" % base)])
    assert not report["ok"] and report["final_head"] == base
    assert report["head"] != base
    assert git(repo, "worktree", "list", "--porcelain") == before


@pytest.mark.parametrize("ref,contains", [("missing-ref", []), ("main", ["0" * 40])])
def test_missing_objects_are_errors_before_worktree_creation(repo, ref, contains):
    before = git(repo, "worktree", "list", "--porcelain")
    with pytest.raises(ValueError):
        verifier.verify(repo, ref, contains, [command("raise AssertionError('must not run')")])
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_inherited_git_and_python_paths_do_not_redirect_checkout(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "bogus-index"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    result = verifier.verify(repo, "main", [], [command(
        "import os; assert 'PYTHONPATH' not in os.environ; assert 'GIT_DIR' not in os.environ")])
    assert result["ok"] and not (tmp_path / "bogus-index").exists()


@pytest.mark.parametrize("kwargs", [
    {"commands": []}, {"commands": [command("pass"), []]},
    {"contains": ["abc"]}, {"timeout": float("nan")},
    {"timeout": float("inf")}, {"timeout": 0}, {"ref": "--help"},
])
def test_invalid_input_has_no_worktree_side_effects(repo, kwargs):
    before = git(repo, "worktree", "list", "--porcelain")
    args = dict(root=repo, ref="main", contains=[], commands=[command("pass")])
    args.update(kwargs)
    with pytest.raises(ValueError):
        verifier.verify(**args)
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_cli_stdlib_json_receipt_and_failure_exit(repo):
    script = Path(verifier.__file__)
    for code, expected in (("print('ok')", 0), ("raise SystemExit(3)", 1)):
        done = subprocess.run([sys.executable, "-S", str(script), str(repo), "--ref", "main",
                               "--command", json.dumps(command(code))],
                              capture_output=True, text=True, check=False)
        assert done.returncode == expected, done.stderr
        assert json.loads(done.stdout)["ok"] == (expected == 0)
    done = subprocess.run([sys.executable, "-S", str(script), str(repo),
                           "--command", "[]"], capture_output=True, text=True)
    assert done.returncode == 2 and not done.stdout

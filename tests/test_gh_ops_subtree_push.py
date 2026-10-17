"""gh_ops subtree-push: verified split SHA to a NEW branch only (local bare repos, offline).

Cases mirror gh_identity#77 (tests/test_subtree_sync_run.py, test_subtree_push_guards.py,
test_subtree_stale_split.py at d43ceb5) so the two implementations cannot drift.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import gh_ops as g

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "gh_ops.py"
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
           GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
needs_subtree = pytest.mark.skipif(
    subprocess.run(["git", "subtree", "-h"], capture_output=True).returncode == 1,
    reason="git subtree not installed")


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch):
    for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, ENV[key])


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
                          env=ENV).stdout.strip()


def _commit(root, files, msg):
    for name, text in files.items():
        p = Path(root) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)
    return _git(root, "rev-parse", "HEAD")


def _refs(root):
    return _git(root, "for-each-ref", "--format=%(refname) %(objectname)")


def _git_dir(root):
    return Path(_git(root, "rev-parse", "--absolute-git-dir"))


class Spy:
    """Wraps subprocess.run; records git argv; optional per-call override."""

    def __init__(self, override=None):
        self.calls: list = []
        self.override = override

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.override:
            replaced = self.override(argv, kwargs)
            if replaced is not None:
                return replaced
        return subprocess.run(argv, **kwargs)

    def pushes(self):
        return [c for c in self.calls if c[:2] == ["git", "push"] or c[:3] == ["git", "subtree", "push"]]


def _fake(argv, stdout, code=0):
    return subprocess.CompletedProcess(argv, code, stdout, "")


@pytest.fixture
def world(tmp_path):
    mono = tmp_path / "mono"
    mono.mkdir()
    _git(mono, "init", "-q", "-b", "main")
    _commit(mono, {"sub/a.py": "a = 1\n", "top.py": "x = 1\n"}, "one")
    _commit(mono, {"sub/b.py": "b = 2\n"}, "two")
    child = tmp_path / "child.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "trunk", str(child))
    return {"mono": mono, "child": child, "tmp": tmp_path}


def _split(mono, prefix="sub"):
    return g.subtree_split_verified(prefix, cwd=str(mono))["sha"]


def _seed_child(world):
    split = _split(world["mono"])
    _git(world["mono"], "push", "-q", str(world["child"]), f"{split}:refs/heads/trunk")
    return split


# --- input validation (mirrors #77 BAD) --------------------------------------------------

BAD = [
    dict(prefix="--rejoin", remote="/x", branch="b"),
    dict(prefix="../up", remote="/x", branch="b"),
    dict(prefix="sub", remote="https://user:tok@github.com/o/r", branch="b"),
    dict(prefix="sub", remote="ssh://github.com/o/r", branch="b"),
    dict(prefix="sub", remote="relative", branch="b"),
    dict(prefix="sub", remote="/x", branch="--force"),
    dict(prefix="sub", remote="/x", branch="a..b"),
    dict(prefix="sub", remote="/x", branch="x.lock"),
    dict(prefix="sub", remote="/x", branch="main"),
    dict(prefix="sub", remote="/x", branch="master"),
    dict(prefix="sub", remote=None, branch="b"),
    dict(prefix="missing", remote="/x", branch="b"),
    dict(prefix="sub", remote="/x", branch="b", source="--all"),
    dict(prefix="sub", remote="/x", branch="b", compare_ref="+trunk"),
]


@pytest.mark.parametrize("kwargs", BAD)
def test_bad_input_rejected_before_subtree_check(world, monkeypatch, kwargs):
    monkeypatch.setattr(g, "_subtree_available", lambda *a, **k: pytest.fail("availability checked first"))
    with pytest.raises(ValueError):
        g.subtree_push(cwd=str(world["mono"]), **kwargs)


def test_write_must_be_bool(world):
    with pytest.raises(ValueError):
        g.subtree_push("sub", str(world["child"]), "sync/x", write="yes", cwd=str(world["mono"]))


def test_missing_subtree_is_clear_error(world, monkeypatch):
    monkeypatch.setattr(g, "_subtree_available", lambda *a, **k: False)
    with pytest.raises(g.SubtreePushError) as e:
        g.subtree_push("sub", str(world["child"]), "feature", cwd=str(world["mono"]))
    assert e.value.code == "subtree_unavailable"


# --- plan / write -------------------------------------------------------------------------

@needs_subtree
def test_push_plan_changes_nothing(world):
    _seed_child(world)
    before_mono, before_child = _refs(world["mono"]), _refs(world["child"])
    spy = Spy()
    out = g.subtree_push("sub", str(world["child"]), "sync/one", cwd=str(world["mono"]), run=spy)
    assert out["state"] == "planned" and out["write"] is False and out["dry_run"]
    assert out["command"] == ["git", "push", "--force-with-lease=refs/heads/sync/one:", "--", str(world["child"]),
                              f"{out['split_sha']}:refs/heads/sync/one"]
    assert out["provenance"]["trailer"] == f"Subtree-Split: {out['source']} {out['split_sha']} sub"
    assert not spy.pushes()
    assert _refs(world["mono"]) == before_mono and _refs(world["child"]) == before_child
    assert not (_git_dir(world["mono"]) / "subtree-cache").exists()
    assert not (_git_dir(world["mono"]) / "FETCH_HEAD").exists()


@needs_subtree
def test_push_write_creates_new_branch_and_verifies(world):
    _seed_child(world)
    before_mono = _refs(world["mono"])
    spy = Spy()
    out = g.subtree_push("sub", str(world["child"]), "sync/one", write=True, cwd=str(world["mono"]), run=spy)
    assert out["state"] == "pushed" and out["verified"]["ok"]
    assert _git(world["child"], "rev-parse", "refs/heads/sync/one") == out["split_sha"]
    assert _git(world["child"], "rev-parse", "refs/heads/sync/one^{tree}") == _git(world["mono"], "rev-parse",
                                                                                  "HEAD:sub")
    (push,) = spy.pushes()
    assert push == ["git", "push", "--force-with-lease=refs/heads/sync/one:", "--", str(world["child"]),
                    f"{out['split_sha']}:refs/heads/sync/one"]
    # The only force-family option is the empty lease (branch must not exist); nothing else.
    assert not any(a in ("-f", "--force", "--force-with-lease", "--mirror", "--force-if-includes")
                   or a.startswith("+") or (a.startswith("--force") and a != "--force-with-lease=refs/heads/sync/one:")
                   for a in push)
    assert _refs(world["mono"]) == before_mono
    assert not (_git_dir(world["mono"]) / "subtree-cache").exists()
    with pytest.raises(ValueError, match="refs/heads/sync/one already exists"):
        g.subtree_push("sub", str(world["child"]), "sync/one", cwd=str(world["mono"]))


@needs_subtree
def test_push_refuses_remote_default_branch(world):
    _seed_child(world)
    with pytest.raises(ValueError, match="default branch trunk"):
        g.subtree_push("sub", str(world["child"]), "trunk", write=True, cwd=str(world["mono"]))


@needs_subtree
def test_push_fast_forward_of_child_default_is_allowed(world):
    seeded = _seed_child(world)
    _commit(world["mono"], {"sub/c.py": "c = 3\n"}, "three")
    out = g.subtree_push("sub", str(world["child"]), "sync/ff", write=True, cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "trunk", "compare_sha": seeded, "child_only_paths": []}
    assert _git(world["child"], "merge-base", "--is-ancestor", seeded, "refs/heads/sync/ff") == ""


# --- guards (mirrors test_subtree_push_guards.py) -----------------------------------------

def _child_ahead(world, files):
    _seed_child(world)
    work = world["tmp"] / "work"
    _git(world["tmp"], "clone", "-q", str(world["child"]), str(work))
    _git(work, "checkout", "-q", "trunk")
    _commit(work, files, "child ahead")
    _git(work, "push", "-q", "origin", "HEAD:trunk")
    return work


@needs_subtree
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("compare_ref", [None, "trunk"])
def test_push_blocked_when_child_trunk_not_ancestor(world, write, compare_ref):
    _child_ahead(world, {"ahead-on-child.py": "x\n"})
    before = _readonly_state(world)
    spy = Spy()
    with pytest.raises(ValueError, match="remote ref trunk is not an ancestor"):
        g.subtree_push("sub", str(world["child"]), "sync/new", compare_ref=compare_ref, write=write,
                       cwd=str(world["mono"]), run=spy)
    assert not spy.pushes()
    assert _readonly_state(world) == before


def test_push_guard_lists_child_only_paths(world, monkeypatch):
    split = "a" * 40
    monkeypatch.setattr(g, "_subtree_is_ancestor", lambda *a, **k: True)
    monkeypatch.setattr(g, "_subtree_oid", lambda cwd, spec, **k: "split-tree" if spec.startswith(split) else "child-tree")
    monkeypatch.setattr(g, "_subtree_tree_paths",
                        lambda cwd, t, **k: {"a.py"} if t == "split-tree" else {"a.py", "only-on-child.txt"})
    compare = {"ref": "trunk", "sha": "1" * 40}
    with pytest.raises(ValueError, match=r"only-on-child\.txt \(remote ref trunk\)"):
        g._subtree_push_guards(str(world["mono"]), split, compare, run=subprocess.run, timeout=30)
    out = g._subtree_push_guards(str(world["mono"]), split, compare, child_only_paths=("only-on-*",),
                                 run=subprocess.run, timeout=30)
    assert out["child_only_paths"] == ["only-on-child.txt"]


@needs_subtree
def test_child_only_paths_refused_by_default_allowed_by_glob(world):
    """The child tip has a path the (descendant) split lacks: refused unless a glob allows it."""
    seeded = _seed_child(world)
    _git(world["mono"], "rm", "-q", "sub/b.py")
    _git(world["mono"], "commit", "-q", "-m", "drop b")
    spy = Spy()
    with pytest.raises(ValueError, match=r"child-only paths not present in split: b\.py \(remote ref trunk\)"):
        g.subtree_push("sub", str(world["child"]), "sync/p", write=True, cwd=str(world["mono"]), run=spy)
    assert not spy.pushes()
    out = g.subtree_push("sub", str(world["child"]), "sync/p", child_only_paths=("b.*",), write=True,
                         cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "trunk", "compare_sha": seeded, "child_only_paths": ["b.py"]}


# --- stale split (mirrors test_subtree_stale_split.py) ------------------------------------

@pytest.fixture
def stale_mono(tmp_path):
    root = tmp_path / "mono"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, {"sub/a.py": "v1\n", "top.py": "t\n"}, "one")
    stale = g.subtree_split_verified("sub", cwd=str(root))["sha"]
    _commit(root, {"sub/a.py": "v2\n"}, "two")
    child = tmp_path / "child.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "trunk", str(child))
    return root, stale, child


def _stale_split_override(stale):
    def override(argv, kwargs):
        if argv[1:3] == ["subtree", "split"]:
            return _fake(argv, stale + "\n")
        if argv[1:3] == ["subtree", "push"]:
            raise AssertionError("git subtree push must not run")
        return None
    return override


@needs_subtree
def test_stale_subtree_split_corrected(stale_mono):
    root, stale, _ = stale_mono
    out = g.subtree_split_verified("sub", cwd=str(root), run=Spy(_stale_split_override(stale)))
    assert out["sha"] != stale and out["method"] == "filter-branch" and out["warning"] == "subtree_split_stale"
    assert out["trees_match"]
    assert _git(root, "rev-parse", out["sha"] + "^{tree}") == _git(root, "rev-parse", "HEAD:sub")
    assert not (_git_dir(root) / "FETCH_HEAD").exists()


@needs_subtree
def test_tree_check_mutation_breaks_stale_detection(stale_mono, monkeypatch):
    """Mutation: with tree validation disabled the stale split is wrongly accepted."""
    root, stale, _ = stale_mono
    monkeypatch.setattr(g, "_subtree_split_matches_prefix_tree",
                        lambda *a, **k: {"prefix_tree": "a", "split_tree": "a", "trees_match": True})
    out = g.subtree_split_verified("sub", cwd=str(root), run=Spy(_stale_split_override(stale)))
    assert out["sha"] == stale


@needs_subtree
def test_stale_split_without_fallback_fails_closed(stale_mono, monkeypatch):
    root, stale, child = stale_mono
    _git(root, "push", "-q", str(child), f"{stale}:refs/heads/trunk")
    before_child = _refs(child)
    monkeypatch.setattr(g, "_subtree_split_filter_branch", lambda *a, **k: None)
    spy = Spy(_stale_split_override(stale))
    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push("sub", str(child), "sync/stale", write=True, cwd=str(root), run=spy)
    assert err.value.code == "stale_subtree_split"
    assert not spy.pushes() and _refs(child) == before_child


@needs_subtree
def test_fallback_result_is_revalidated(stale_mono, monkeypatch):
    """A fallback that returns a wrong-tree commit is still rejected by the final tree check."""
    root, stale, _ = stale_mono
    monkeypatch.setattr(g, "_subtree_split_filter_branch", lambda *a, **k: stale)
    out = g.subtree_split_verified("sub", cwd=str(root), run=Spy(_stale_split_override(stale)))
    assert out["sha"] is None and out["reason"] == "stale_subtree_split" and out["method"] == "filter-branch"


@needs_subtree
def test_push_write_refuses_stale_split(world, monkeypatch):
    split = _seed_child(world)
    mono, child = world["mono"], world["child"]
    monkeypatch.setattr(g, "subtree_split_verified", lambda prefix, **kw: {
        "sha": split, "reason": None, "method": "subtree", "warning": None})
    monkeypatch.setattr(g, "_subtree_split_matches_prefix_tree",
                        lambda *a, **k: {"prefix_tree": "a" * 40, "split_tree": "b" * 40, "trees_match": False})
    spy = Spy()
    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push("sub", str(child), "sync/stale-test", write=True, cwd=str(mono), run=spy)
    assert err.value.code == "stale_subtree_split"
    assert not spy.pushes()


@needs_subtree
def test_push_uses_validated_split_not_subtree_push(stale_mono):
    root, stale, child = stale_mono
    _git(root, "push", "-q", str(child), f"{stale}:refs/heads/trunk")
    good = g.subtree_split_verified("sub", cwd=str(root))["sha"]
    spy = Spy(_stale_split_override(stale))
    out = g.subtree_push("sub", str(child), "sync/x", write=True, cwd=str(root), run=spy)
    assert out["verified"]["ok"] and out["split_sha"] == good != stale
    assert _git(child, "rev-parse", "refs/heads/sync/x") == good
    assert spy.pushes() == [["git", "push", "--force-with-lease=refs/heads/sync/x:", "--", str(child),
                             f"{good}:refs/heads/sync/x"]]


@needs_subtree
def test_push_revalidates_after_planning_and_never_writes_on_mismatch(world, monkeypatch):
    """Planning passes; only the gate immediately before push sees the mismatch."""
    _seed_child(world)
    mono, child = world["mono"], world["child"]
    before_child = _refs(child)
    real_check, real_guard = g._subtree_split_matches_prefix_tree, g._subtree_push_guards
    planning_done, checks = False, []

    def guard(*a, **k):
        nonlocal planning_done
        result = real_guard(*a, **k)
        planning_done = True
        return result

    def check(*a, **k):
        checks.append(planning_done)
        result = real_check(*a, **k)
        return {**result, "trees_match": False} if planning_done else result

    monkeypatch.setattr(g, "_subtree_push_guards", guard)
    monkeypatch.setattr(g, "_subtree_split_matches_prefix_tree", check)
    spy = Spy()
    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push("sub", str(child), "sync/revalidate", write=True, cwd=str(mono), run=spy)
    assert err.value.code == "stale_subtree_split"
    assert False in checks and checks[-1] is True
    assert not spy.pushes() and _refs(child) == before_child


@needs_subtree
def test_push_tree_gate_called_once_per_write(world, monkeypatch):
    _seed_child(world)
    calls = []
    real = g._subtree_push_split_tree_gate

    def gate(*a, **k):
        calls.append(1)
        if len(calls) > 1:
            raise g.SubtreePushError("stale_subtree_split")
        return real(*a, **k)

    monkeypatch.setattr(g, "_subtree_push_split_tree_gate", gate)
    g.subtree_push("sub", str(world["child"]), "sync/once", write=True, cwd=str(world["mono"]))
    assert len(calls) == 1


@needs_subtree
def test_post_push_mismatch_is_reported(world):
    _seed_child(world)
    mono, child = world["mono"], world["child"]
    state = {"pushed": False}

    def override(argv, kwargs):
        if argv[:2] == ["git", "push"]:
            state["pushed"] = True
            return None
        if state["pushed"] and argv[1:2] == ["ls-remote"]:
            return _fake(argv, "f" * 40 + "\trefs/heads/sync/v\n")
        return None

    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push("sub", str(child), "sync/v", write=True, cwd=str(mono), run=Spy(override))
    assert err.value.code == "post_write_verification_failed" and "refs/heads/sync/v" in str(err.value)


@needs_subtree
def test_branch_created_between_plan_and_push_is_not_fast_forwarded(world):
    """Race: someone creates the target branch at an ancestor of the split after the
    ls-remote check. A plain push would fast-forward it; the empty lease rejects it."""
    mono, child = world["mono"], world["child"]
    seeded = _seed_child(world)
    _commit(mono, {"sub/c.py": "c = 3\n"}, "three")

    def override(argv, kwargs):
        if argv[:2] == ["git", "push"]:
            _git(child, "update-ref", "refs/heads/sync/race", seeded)
        return None

    spy = Spy(override)
    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push("sub", str(child), "sync/race", write=True, cwd=str(mono), run=spy, sleep=lambda s: None)
    assert err.value.code == "subtree_push_failed" and "refs/heads/sync/race" in str(err.value)
    assert len(spy.pushes()) == 1  # rejected, not a network error: no retry
    assert _git(child, "rev-parse", "refs/heads/sync/race") == seeded


@needs_subtree
def test_child_only_path_added_on_child_real_repos(tmp_path):
    """Non-mocked: child adds LICENSE, monorepo pulls it (subtree add/pull) then drops it."""
    child = tmp_path / "child.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "trunk", str(child))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(child), str(work))
    _git(work, "checkout", "-q", "-b", "trunk")
    _commit(work, {"a.py": "a = 1\n"}, "child one")
    _git(work, "push", "-q", "origin", "HEAD:trunk")
    mono = tmp_path / "mono"
    mono.mkdir()
    _git(mono, "init", "-q", "-b", "main")
    _commit(mono, {"top.py": "x = 1\n"}, "mono one")
    _git(mono, "subtree", "add", "-q", "--prefix=sub", str(child), "trunk")
    _commit(work, {"LICENSE": "x\n"}, "child adds LICENSE")
    _git(work, "push", "-q", "origin", "HEAD:trunk")
    tip = _git(work, "rev-parse", "HEAD")
    _git(mono, "subtree", "pull", "-q", "--prefix=sub", "-m", "take child", str(child), "trunk")
    _git(mono, "rm", "-q", "sub/LICENSE")
    _git(mono, "commit", "-q", "-m", "monorepo does not ship LICENSE")
    spy = Spy()
    with pytest.raises(ValueError, match=r"child-only paths not present in split: LICENSE \(remote ref trunk\)"):
        g.subtree_push("sub", str(child), "sync/lic", write=True, cwd=str(mono), run=spy)
    assert not spy.pushes() and _git(child, "for-each-ref", "--format=%(refname)") == "refs/heads/trunk"
    out = g.subtree_push("sub", str(child), "sync/lic", child_only_paths=("LICENSE",), write=True, cwd=str(mono))
    assert out["push_guard"] == {"compare_ref": "trunk", "compare_sha": tip, "child_only_paths": ["LICENSE"]}
    assert _git(child, "rev-parse", "refs/heads/sync/lic") == out["split_sha"]


@needs_subtree
def test_remote_unavailable_fails_closed(world, tmp_path):
    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push("sub", str(tmp_path / "nope.git"), "sync/x", cwd=str(world["mono"]))
    assert err.value.code == "git_remote_unavailable"


@needs_subtree
def test_push_retries_only_network_errors(world):
    _seed_child(world)
    attempts = []

    def override(argv, kwargs):
        if argv[:2] == ["git", "push"]:
            attempts.append(argv)
            if len(attempts) == 1:
                return subprocess.CompletedProcess(argv, 128, "", "fatal: unable to access: Could not resolve host")
        return None

    sleeps = []
    out = g.subtree_push("sub", str(world["child"]), "sync/r", write=True, cwd=str(world["mono"]),
                         run=Spy(override), sleep=sleeps.append)
    assert out["verified"]["ok"] and len(attempts) == 2 and sleeps == [2.0]


# --- squash join: fetch child tip first, then re-anchor (3bot / PythDRagoras) ---------------

def _squash_world(tmp_path):
    child = tmp_path / "child.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(child))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(child), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _commit(work, {"a.py": "a = 1\n"}, "child one")
    _git(work, "push", "-q", "origin", "HEAD:main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _commit(seed, {"top.py": "x = 1\n"}, "mono one")
    _git(seed, "subtree", "add", "-q", "--prefix=sub", "--squash", str(child), "main")
    # file:// clone so the child's objects are NOT shared with the monorepo checkout.
    mono = tmp_path / "mono"
    _git(tmp_path, "clone", "-q", "file://" + str(seed), str(mono))
    _commit(mono, {"sub/b.py": "b = 2\n"}, "mono edits child")
    return {"mono": mono, "child": child, "work": work}


@needs_subtree
def test_squash_join_split_needs_child_tip_fetched_object_only(tmp_path, monkeypatch):
    w = _squash_world(tmp_path)
    mono, child = w["mono"], w["child"]
    tip = _git(child, "rev-parse", "main")
    assert subprocess.run(["git", "-C", str(mono), "cat-file", "-e", tip], env=ENV).returncode != 0
    before = _refs(mono)
    real = g._subtree_fetch_compare_tip
    # Mutation: skipping the pre-split fetch reproduces "could not rev-parse split hash".
    monkeypatch.setattr(g, "_subtree_fetch_compare_tip", lambda *a, **k: False)
    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push_plan("sub", str(child), "sync/s", cwd=str(mono))
    assert err.value.code == "subtree_split_failed" and "split hash" in str(err.value)
    monkeypatch.setattr(g, "_subtree_fetch_compare_tip", real)
    out = g.subtree_push_plan("sub", str(child), "sync/s", cwd=str(mono))
    assert out["compare_tip_fetched"] is True
    assert out["push_guard"]["compare_ref"] == "main" and out["push_guard"]["compare_sha"] == tip
    assert _refs(mono) == before
    assert not (_git_dir(mono) / "FETCH_HEAD").exists()


@needs_subtree
def test_squash_reanchor_flow_push_merge_pull_then_fast_forward(tmp_path):
    """push -> merge in child -> `git subtree pull --squash` -> next push is a fast-forward."""
    w = _squash_world(tmp_path)
    mono, child, work = w["mono"], w["child"], w["work"]
    first = g.subtree_push("sub", str(child), "sync/one", write=True, cwd=str(mono))
    _git(work, "fetch", "-q", "origin")
    _git(work, "merge", "-q", "--ff-only", "origin/sync/one")
    _git(work, "push", "-q", "origin", "HEAD:main")
    assert _git(child, "rev-parse", "main") == first["split_sha"]
    _git(mono, "subtree", "pull", "-q", "--prefix=sub", "--squash", "-m", "re-anchor", str(child), "main")
    _commit(mono, {"sub/c.py": "c = 3\n"}, "next edit")
    second = g.subtree_push("sub", str(child), "sync/two", write=True, cwd=str(mono))
    assert second["push_guard"]["compare_sha"] == first["split_sha"]
    assert _git(child, "merge-base", "--is-ancestor", first["split_sha"], "refs/heads/sync/two") == ""


# --- shared push loop + CLI -----------------------------------------------------------------

def test_push_with_retry_default_args_unchanged():
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert g.git_commit_push_with_retry(run=run, sleep=lambda s: None) == (True, "")
    assert seen == [(["git", "push", "origin", "HEAD"], {"cwd": ".", "capture_output": True, "text": True})]
    seen.clear()
    g.git_commit_push_with_retry(run=run, sleep=lambda s: None, push_args=("--", "/r", "a:refs/heads/b"))
    assert seen[0][0] == ["git", "push", "--", "/r", "a:refs/heads/b"]


@needs_subtree
def test_cli_plan_prints_command(world):
    _seed_child(world)
    before_child = _refs(world["child"])
    r = subprocess.run([sys.executable, "-S", str(SCRIPT), "--json", "subtree-push", "--root", str(world["mono"]),
                        "--prefix", "sub", "--remote", str(world["child"]), "--branch", "sync/x"],
                       capture_output=True, text=True, env=ENV)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["state"] == "planned" and out["command"][:4] == ["git", "push", "--force-with-lease=refs/heads/sync/x:", "--"]
    assert _refs(world["child"]) == before_child
    r = subprocess.run([sys.executable, "-S", str(SCRIPT), "subtree-push", "--root", str(world["mono"]),
                        "--prefix", "sub", "--remote", str(world["child"]), "--branch", "main"],
                       capture_output=True, text=True, env=ENV)
    assert r.returncode == 2 and not r.stdout and "main/master" in r.stderr

@needs_subtree
@pytest.mark.parametrize("default", ["main", "trunk", "develop"])
@pytest.mark.parametrize("write", [False, True])
def test_explicit_missing_compare_ref_fails_closed(world, default, write):
    split = _split(world["mono"])
    _git(world["mono"], "push", "-q", str(world["child"]), f"{split}:refs/heads/{default}")
    _git(world["child"], "symbolic-ref", "HEAD", f"refs/heads/{default}")
    before = _refs(world["child"])
    spy = Spy()
    with pytest.raises(g.SubtreePushError, match="remote ref missing does not exist") as exc:
        g.subtree_push("sub", str(world["child"]), "sync/missing",
                       compare_ref="missing", write=write, cwd=str(world["mono"]), run=spy)
    assert exc.value.code == "compare_ref_not_found"
    assert not spy.pushes()
    assert _refs(world["child"]) == before

@needs_subtree
def test_explicit_existing_compare_ref_overrides_default(world):
    split = _seed_child(world)
    _git(world["child"], "update-ref", "refs/heads/custom", split)
    out = g.subtree_push_plan("sub", str(world["child"]), "sync/custom",
                              compare_ref="custom", cwd=str(world["mono"]))
    assert out["push_guard"]["compare_ref"] == "custom"
    assert out["push_guard"]["compare_sha"] == split


@needs_subtree
def test_explicit_compare_ref_does_not_reorder_behind_remote_default(world):
    seeded = _seed_child(world)
    _commit(world["mono"], {"sub/c.py": "c = 3\n"}, "three")
    newer = _split(world["mono"])
    _git(world["mono"], "push", "-q", str(world["child"]), f"{newer}:refs/heads/trunk")
    _git(world["child"], "update-ref", "refs/heads/custom", seeded)
    out = g.subtree_push_plan("sub", str(world["child"]), "sync/custom-only",
                              compare_ref="custom", cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "custom", "compare_sha": seeded, "child_only_paths": []}


@needs_subtree
def test_default_branch_lookup_when_not_in_fallback_candidates(world):
    split = _split(world["mono"])
    child = world["child"]
    _git(child, "update-ref", "-d", "refs/heads/trunk")
    _git(world["mono"], "push", "-q", str(child), f"{split}:refs/heads/develop")
    _git(child, "symbolic-ref", "HEAD", "refs/heads/develop")
    spy = Spy()
    out = g.subtree_push_plan("sub", str(child), "sync/develop-default", cwd=str(world["mono"]), run=spy)
    assert out["push_guard"] == {"compare_ref": "develop", "compare_sha": split, "child_only_paths": []}
    assert len([c for c in spy.calls if "ls-remote" in c]) == 2


@needs_subtree
def test_explicit_compare_ref_skips_default_branch_ls_remote(world):
    split = _seed_child(world)
    _git(world["child"], "update-ref", "refs/heads/custom", split)
    spy = Spy()
    g.subtree_push_plan("sub", str(world["child"]), "sync/skip-lookup",
                        compare_ref="custom", cwd=str(world["mono"]), run=spy)
    ls_remote = [c for c in spy.calls if "ls-remote" in c]
    assert len(ls_remote) == 1


@needs_subtree
def test_compare_fallback_to_master_when_default_unresolvable(world):
    split = _split(world["mono"])
    child = world["child"]
    _git(child, "update-ref", "-d", "refs/heads/trunk")
    _git(world["mono"], "push", "-q", str(child), f"{split}:refs/heads/master")
    _git(child, "symbolic-ref", "HEAD", "refs/heads/develop")
    out = g.subtree_push_plan("sub", str(child), "sync/master-fallback", cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "master", "compare_sha": split, "child_only_paths": []}


@needs_subtree
def test_compare_fallback_to_trunk_when_trunk_is_not_default(world):
    split = _split(world["mono"])
    child = world["child"]
    _git(child, "update-ref", "-d", "refs/heads/trunk")
    _git(child, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(world["mono"], "push", "-q", str(child), f"{split}:refs/heads/trunk")
    out = g.subtree_push_plan("sub", str(child), "sync/trunk-fallback", cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "trunk", "compare_sha": split, "child_only_paths": []}


def test_push_guard_without_compare_reports_exact_message(world):
    split = _split(world["mono"])
    with pytest.raises(g.SubtreePushError) as exc:
        g._subtree_push_guards(str(world["mono"]), split, None, run=subprocess.run, timeout=30)
    assert exc.value.code == "compare_ref_unavailable"
    assert str(exc.value) == "compare_ref_unavailable: remote comparison ref is required for ancestor validation"


@needs_subtree
def test_unresolvable_compare_reports_exact_message(world):
    spy = Spy()
    with pytest.raises(g.SubtreePushError) as exc:
        g.subtree_push_plan("sub", str(world["child"]), "sync/no-tip", cwd=str(world["mono"]), run=spy)
    assert exc.value.code == "compare_ref_unavailable"
    assert str(exc.value) == (
        "compare_ref_unavailable: could not resolve remote comparison ref; "
        "specify an existing --compare-ref"
    )


@needs_subtree
def test_subtree_push_render_includes_compare_line(world):
    _seed_child(world)
    out = g.subtree_push("sub", str(world["child"]), "sync/render", cwd=str(world["mono"]))
    guard = out["push_guard"]
    rendered = g._render("subtree-push", out)
    assert rendered.startswith("dry run subtree-push: would run git push")
    assert f" compare={guard['compare_ref']}@{guard['compare_sha'][:12]}" in rendered
    assert rendered.endswith(out["provenance"]["trailer"])


# --- comparison is mandatory; exercise actual Git failures, not canned git output --------

def _readonly_state(world):
    mono, child = world["mono"], world["child"]
    fetch_head = _git_dir(mono) / "FETCH_HEAD"
    return (_refs(mono), _refs(child), fetch_head.read_bytes() if fetch_head.exists() else None)


@needs_subtree
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("populated", [False, True])
def test_unresolvable_compare_fails_before_split(world, write, populated):
    if populated:
        split = _split(world["mono"])
        _git(world["mono"], "push", "-q", str(world["child"]), f"{split}:refs/heads/custom")
    # HEAD points to an unborn trunk; neither the default nor fallback refs exist.
    (_git_dir(world["mono"]) / "FETCH_HEAD").write_bytes(b"keep existing fetch state\n")
    before = _readonly_state(world)
    spy = Spy()
    with pytest.raises(g.SubtreePushError) as exc:
        g.subtree_push("sub", str(world["child"]), "sync/no-compare", write=write,
                       cwd=str(world["mono"]), run=spy)
    assert exc.value.code == "compare_ref_unavailable"
    assert not spy.pushes()
    assert not any(c[1:3] == ["subtree", "split"] or c[1:2] == ["fetch"] for c in spy.calls)
    assert _readonly_state(world) == before


@needs_subtree
def test_push_guard_without_comparison_fails_closed(world):
    split = _split(world["mono"])
    with pytest.raises(g.SubtreePushError) as exc:
        g._subtree_push_guards(str(world["mono"]), split, None, run=subprocess.run, timeout=30)
    assert exc.value.code == "compare_ref_unavailable"


@needs_subtree
@pytest.mark.parametrize("write", [False, True])
def test_develop_default_is_selected_and_checked(world, write):
    split = _split(world["mono"])
    _git(world["mono"], "push", "-q", str(world["child"]), f"{split}:refs/heads/develop")
    _git(world["child"], "symbolic-ref", "HEAD", "refs/heads/develop")
    _commit(world["mono"], {"sub/c.py": "c = 3\n"}, "advance split")
    before_mono = _refs(world["mono"])
    out = g.subtree_push("sub", str(world["child"]), "sync/develop", write=write, cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "develop", "compare_sha": split, "child_only_paths": []}
    assert out["state"] == ("pushed" if write else "planned")
    assert _refs(world["mono"]) == before_mono
    assert not (_git_dir(world["mono"]) / "FETCH_HEAD").exists()


@needs_subtree
def test_known_fallback_is_used_if_remote_head_is_unborn(world):
    split = _split(world["mono"])
    _git(world["mono"], "push", "-q", str(world["child"]), f"{split}:refs/heads/main")
    out = g.subtree_push_plan("sub", str(world["child"]), "sync/fallback", cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "main", "compare_sha": split, "child_only_paths": []}


@needs_subtree
@pytest.mark.parametrize("write", [False, True])
def test_actual_compare_fetch_failure_never_pushes_or_changes_refs(tmp_path, write):
    world = _squash_world(tmp_path)
    mono, child = world["mono"], world["child"]
    tip = _git(child, "rev-parse", "main")
    assert subprocess.run(["git", "-C", str(mono), "cat-file", "-e", tip],
                          env=ENV, capture_output=True).returncode != 0
    fetch_head = _git_dir(mono) / "FETCH_HEAD"
    fetch_head.write_bytes(b"preserve me\n")
    before = _readonly_state(world)
    hidden = tmp_path / "unavailable.git"

    def hide_before_fetch(argv, kwargs):
        if argv[1:2] == ["fetch"]:
            child.rename(hidden)  # git itself now reports a real transport/repository error
        return None

    spy = Spy(hide_before_fetch)
    try:
        with pytest.raises(g.SubtreePushError) as exc:
            g.subtree_push("sub", str(child), "sync/fetch-fails", write=write, cwd=str(mono), run=spy)
        assert exc.value.code == "git_failed" and "git fetch" in str(exc.value)
        assert not spy.pushes()
    finally:
        if hidden.exists():
            hidden.rename(child)
    assert _readonly_state(world) == before


@needs_subtree
@pytest.mark.parametrize("write", [False, True])
def test_actual_default_ref_lookup_failure_never_pushes(world, write):
    mono, child = world["mono"], world["child"]
    split = _split(mono)
    _git(mono, "push", "-q", str(child), f"{split}:refs/heads/develop")
    _git(child, "symbolic-ref", "HEAD", "refs/heads/develop")
    before = _readonly_state(world)
    hidden = world["tmp"] / "unavailable.git"
    reads = []

    def hide_before_second_read(argv, kwargs):
        if argv[1:2] == ["ls-remote"]:
            reads.append(argv)
            if len(reads) == 2:
                child.rename(hidden)
        return None

    spy = Spy(hide_before_second_read)
    try:
        with pytest.raises(g.SubtreePushError) as exc:
            g.subtree_push("sub", str(child), "sync/lookup-fails", write=write, cwd=str(mono), run=spy)
        assert exc.value.code == "git_remote_unavailable"
        assert len(reads) == 2 and not spy.pushes()
    finally:
        if hidden.exists():
            hidden.rename(child)
    assert _readonly_state(world) == before

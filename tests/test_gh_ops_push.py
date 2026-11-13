"""gh_ops git-side commands (commit-files, push, subtree-push, pr-retarget) against local bare repositories and in-memory APIs."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import gh_ops as g
from test_gh_ops import gh_ops


# ═══ · gh ops commit files ═══════════════════════════════════════════════════════════════════════════════
# commit-files: one multi-file commit through the Git Data API, planned first (in-memory API).

spec = importlib.util.spec_from_file_location("gh_ops", ROOT / "gh_ops.py")
gh_ops_gh_ops_commit_files = sys.modules.get("gh_ops") or importlib.util.module_from_spec(spec)
if "gh_ops" not in sys.modules:
    sys.modules["gh_ops"] = gh_ops_gh_ops_commit_files
    spec.loader.exec_module(gh_ops_gh_ops_commit_files)

REPO = "octo/demo"


class Remote:
    """A transport serving one repository's refs, commits, trees and blobs."""

    def __init__(self, files, default_branch="main"):
        self.default_branch = default_branch
        self.trees = {"t0": {path: gh_ops_gh_ops_commit_files._git_blob_sha(data) for path, data in files.items()}}
        self.commits = {"c0": "t0"}
        self.head = "c0" * 20
        self.commits[self.head] = "t0"
        self.calls = []
        self.refuse_patch = self.truncated = self.bad_blob = False

    def __call__(self, method, url, body, headers):
        path = urllib.parse.urlsplit(url).path
        self.calls.append((method, path))
        ok = lambda data, status=200: gh_ops_gh_ops_commit_files.Response(status, data, {})
        if path == f"/repos/{REPO}":
            return ok({"default_branch": self.default_branch})
        if "/git/ref/heads/" in path and method == "GET":
            return ok({"object": {"sha": self.head}})
        if "/git/commits/" in path and method == "GET":
            return ok({"tree": {"sha": self.commits[path.rsplit("/", 1)[1]]}})
        if "/git/trees/" in path and method == "GET":
            tree = self.trees[path.rsplit("/", 1)[1]]
            return ok({"truncated": self.truncated,
                       "tree": [{"path": p, "type": "blob", "mode": "100644", "sha": s} for p, s in tree.items()]})
        if path.endswith("/git/blobs"):
            data = base64.b64decode(body["content"])
            return ok({"sha": "0" * 40 if self.bad_blob else gh_ops_gh_ops_commit_files._git_blob_sha(data)}, 201)
        if path.endswith("/git/trees"):
            tree = dict(self.trees[body["base_tree"]])
            for element in body["tree"]:
                if element["sha"] is None:
                    tree.pop(element["path"], None)
                else:
                    tree[element["path"]] = element["sha"]
            name = f"t{len(self.trees)}"
            self.trees[name] = tree
            return ok({"sha": name}, 201)
        if path.endswith("/git/commits"):
            assert body["parents"] == [self.head]
            name = f"{len(self.commits):040x}"
            self.commits[name] = body["tree"]
            return ok({"sha": name}, 201)
        if method == "PATCH":
            assert body["force"] is False
            if self.refuse_patch:
                return ok({"message": "Update is not a fast forward"}, 422)
            self.head = body["sha"]
            return ok({"object": {"sha": body["sha"]}})
        raise AssertionError((method, path))

    def client(self):
        return gh_ops_gh_ops_commit_files.Client(token="t0ken", transport=self)


def test_plan_then_write_one_verified_commit():
    remote = Remote({"keep.txt": b"same", "old.txt": b"bye", "edit.txt": b"v1"})
    files = {"keep.txt": b"same", "edit.txt": b"v2", "dir/new.txt": b"hi"}
    plan = gh_ops_gh_ops_commit_files.commit_files(REPO, "feat", files, "msg", deletes=("old.txt", "never.txt"), client=remote.client())
    assert plan["ok"] and plan["dry_run"] and not plan["changed"]
    assert {i["path"]: i["change"] for i in plan["plan"]} == {
        "dir/new.txt": "new", "edit.txt": "changed", "keep.txt": "unchanged", "old.txt": "delete", "never.txt": "absent"}
    assert {m for m, _ in remote.calls} == {"GET"}
    done = gh_ops_gh_ops_commit_files.commit_files(REPO, "feat", files, "msg", deletes=("old.txt",), expect_head="C0" * 20,
                               write=True, client=remote.client())
    assert done["ok"] and done["verified"] and remote.head == done["commit_sha"]
    assert remote.trees[remote.commits[remote.head]] == {
        "keep.txt": gh_ops_gh_ops_commit_files._git_blob_sha(b"same"), "edit.txt": gh_ops_gh_ops_commit_files._git_blob_sha(b"v2"),
        "dir/new.txt": gh_ops_gh_ops_commit_files._git_blob_sha(b"hi")}
    assert sum(1 for m, p in remote.calls if p.endswith("/git/blobs")) == 2  # unchanged keep.txt not uploaded
    text = gh_ops_gh_ops_commit_files._render("commit-files", done)
    assert text.startswith("OK commit-files octo/demo@feat (applied) head c0c0c0c ->") and "changed   edit.txt" in text


def test_refusals_and_failures_never_move_the_branch():
    remote = Remote({"a.txt": b"x"})
    start = remote.head
    same = gh_ops_gh_ops_commit_files.commit_files(REPO, "b", {"a.txt": b"x"}, "m", write=True, client=remote.client())
    assert same["ok"] and same["reason"] == "unchanged"
    main = gh_ops_gh_ops_commit_files.commit_files(REPO, "main", {"a.txt": b"y"}, "m", write=True, client=remote.client())
    assert not main["ok"] and "default branch" in main["reason"]
    moved = gh_ops_gh_ops_commit_files.commit_files(REPO, "b", {"a.txt": b"y"}, "m", expect_head="f" * 40, write=True, client=remote.client())
    assert not moved["ok"] and "branch moved" in moved["reason"]
    # Same leading digits, different commit: an exact match is required.
    near = gh_ops_gh_ops_commit_files.commit_files(REPO, "b", {"a.txt": b"y"}, "m", expect_head="c0" * 19 + "c1", write=True,
                               client=remote.client())
    assert not near["ok"] and "branch moved" in near["reason"] and remote.head == start
    remote.refuse_patch = True
    refused = gh_ops_gh_ops_commit_files.commit_files(REPO, "b", {"a.txt": b"y"}, "m", write=True, client=remote.client())
    assert not refused["ok"] and "ref update refused" in refused["reason"] and remote.head == start
    remote.refuse_patch, remote.truncated = False, True
    unknown = gh_ops_gh_ops_commit_files.commit_files(REPO, "b", {"a.txt": b"y"}, "m", deletes=("z",), client=remote.client())
    assert {i["change"] for i in unknown["plan"]} == {"unknown"}
    remote.truncated, remote.bad_blob = False, True
    bad = gh_ops_gh_ops_commit_files.commit_files(REPO, "b", {"a.txt": b"y"}, "m", write=True, client=remote.client())
    assert not bad["ok"] and "blob SHA mismatch" in bad["reason"] and remote.head == start
    allowed = gh_ops_gh_ops_commit_files.commit_files(REPO, "main", {"a.txt": b"y"}, "m", allow_default_branch=True, client=remote.client())
    assert allowed["ok"] and allowed["dry_run"]


@pytest.mark.parametrize("kwargs", [
    {"branch": ""}, {"branch": "-x"}, {"branch": "a..b"}, {"message": " "}, {"files": {}},
    {"files": {"../x": b""}}, {"files": {"/x": b""}}, {"files": {".git/config": b""}}, {"files": {"a\\b": b""}},
    {"files": {"a": b""}, "deletes": ("a",)}, {"files": {"a": "text"}},
    {"files": {f"f{i}": b"" for i in range(101)}}, {"expect_head": "zz"}, {"expect_head": "c0c0c0c"}, {"expect_head": "c0" * 21},
])
def test_validation(kwargs):
    args = {"branch": "b", "files": {"a.txt": b"x"}, "message": "m", **kwargs}
    with pytest.raises(gh_ops_gh_ops_commit_files.GhOpsError):
        gh_ops_gh_ops_commit_files.commit_files(REPO, args.pop("branch"), args.pop("files"), args.pop("message"),
                            client=Remote({"a.txt": b"q"}).client(), **args)


def test_cli_reads_local_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "local.txt").write_bytes(b"new")
    (tmp_path / "other.bin").write_bytes(b"\0")
    monkeypatch.chdir(tmp_path)
    remote = Remote({"a.txt": b"x"})
    assert gh_ops_gh_ops_commit_files.main(["commit-files", REPO, "--branch", "b", "--file", "a.txt=other.bin", "--file", "local.txt",
                        "--message", "m"], client=remote.client()) == 0
    assert "new       local.txt (3 bytes" in capsys.readouterr().out
    assert gh_ops_gh_ops_commit_files.main(["--json", "commit-files", REPO, "--branch", "b", "--file", "local.txt", "--message", "m",
                        "--write"], client=remote.client()) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert gh_ops_gh_ops_commit_files.main(["commit-files", REPO, "--branch", "b", "--message", "m"], client=remote.client()) == 2


# ═══ · gh ops git push ═══════════════════════════════════════════════════════════════════════════════════
# Exercise standalone push against local bare repositories (no GitHub writes).

def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    work, remote = tmp_path / 'work', tmp_path / 'remote.git'
    work.mkdir()
    git(work, 'init', '-q', '-b', 'feature')
    git(work, 'config', 'user.name', 'Test')
    git(work, 'config', 'user.email', 'test@example.invalid')
    (work / 'a').write_text('one')
    git(work, 'add', 'a'); git(work, 'commit', '-qm', 'initial')
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    git(work, 'remote', 'add', 'origin', str(remote))
    return work, remote


def test_plan_then_push_existing_commit_and_verify(checkout):
    work, remote = checkout
    before = git(work, 'rev-parse', 'HEAD')
    planned = gh_ops.git_push(cwd=str(work))
    assert planned['status'] == 'planned' and not planned['pushed']
    assert git(remote, 'for-each-ref', '--format=%(refname)') == ''
    result = gh_ops.git_push(cwd=str(work), write=True)
    assert result['ok'] and result['status'] == 'verified'
    assert result['head_sha'] == before == git(remote, 'rev-parse', 'refs/heads/feature')
    assert git(work, 'rev-parse', 'HEAD') == before


def test_verify_uses_pushurl_not_fetchurl(checkout, tmp_path):
    work, original = checkout
    push = tmp_path / 'push.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(push)], check=True)
    git(work, 'remote', 'set-url', '--push', 'origin', str(push))
    result = gh_ops.git_push(cwd=str(work), write=True)
    assert result['ok'] and result['verified']
    assert git(original, 'for-each-ref', '--format=%(refname)') == ''
    assert git(push, 'rev-parse', 'refs/heads/feature') == result['head_sha']


def test_rejected_push_is_nonzero_and_does_not_force(checkout, capsys):
    work, remote = checkout
    assert gh_ops.git_push(cwd=str(work), write=True)['ok']
    original = git(work, 'rev-parse', 'HEAD')
    (work / 'a').write_text('two')
    git(work, 'commit', '-qam', 'new remote')
    newer = git(work, 'rev-parse', 'HEAD')
    git(work, 'push', '-q', 'origin', 'HEAD')
    git(work, 'reset', '--hard', original)
    assert gh_ops.main(['--json', 'git-push', '--root', str(work), '--write']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'mutation_uncertain'
    assert git(remote, 'rev-parse', 'refs/heads/feature') == newer


def test_detached_requires_explicit_branch(checkout):
    work, remote = checkout
    git(work, 'checkout', '--detach', '-q')
    with pytest.raises(gh_ops.GhOpsError, match='detached'):
        gh_ops.git_push(cwd=str(work), write=True)
    assert gh_ops.git_push(cwd=str(work), branch='explicit', write=True)['ok']


def test_multiple_push_destinations_refused_before_write(checkout, tmp_path):
    work, remote = checkout
    git(work, 'remote', 'set-url', '--add', '--push', 'origin', str(remote))
    git(work, 'remote', 'set-url', '--add', '--push', 'origin', str(tmp_path / 'other'))
    with pytest.raises(gh_ops.GhOpsError, match='exactly one'):
        gh_ops.git_push(cwd=str(work), write=True)
    assert git(remote, 'for-each-ref', '--format=%(refname)') == ''


def test_push_does_not_follow_tags_from_config(checkout):
    work, remote = checkout
    git(work, 'tag', '-a', 'v1', '-m', 'tag')
    git(work, 'config', 'push.followTags', 'true')
    assert gh_ops.git_push(cwd=str(work), write=True)['ok']
    assert git(remote, 'for-each-ref', '--format=%(refname)', 'refs/tags') == ''


@pytest.mark.parametrize('value', [0, -1, True, float('nan')])
def test_push_invalid_timeout(value):
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.git_push(timeout=value)


def test_successful_push_readback_failure_preserves_mutation(checkout, monkeypatch):
    work, remote = checkout
    original = gh_ops.gh_identity._git_run
    def run(root, args, **kwargs):
        if args[0] == 'ls-remote':
            raise gh_ops.gh_identity.GitInspectionError('timeout')
        return original(root, args, **kwargs)
    monkeypatch.setattr(gh_ops.gh_identity, '_git_run', run)
    result = gh_ops.git_push(cwd=str(work), write=True)
    assert result['status'] == 'verification_failed' and result['pushed'] is True
    assert result['mutation_status'] == 'succeeded'


# ═══ · gh ops subtree push ═══════════════════════════════════════════════════════════════════════════════
# gh_ops subtree-push: verified split SHA to a NEW branch only (local bare repos, offline).
#
# Cases mirror gh_identity#77 (tests/test_subtree_sync_run.py, test_subtree_push_guards.py,
# test_subtree_stale_split.py at d43ceb5) so the two implementations cannot drift.

SCRIPT = ROOT / "gh_ops.py"
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
           GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
needs_subtree = pytest.mark.skipif(
    subprocess.run(["git", "subtree", "-h"], capture_output=True).returncode == 1,
    reason="git subtree not installed")


@pytest.fixture
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


@pytest.mark.usefixtures("_isolated_git")
@pytest.mark.parametrize("kwargs", BAD)
def test_bad_input_rejected_before_subtree_check(world, monkeypatch, kwargs):
    monkeypatch.setattr(g, "_subtree_available", lambda *a, **k: pytest.fail("availability checked first"))
    with pytest.raises(ValueError):
        g.subtree_push(cwd=str(world["mono"]), **kwargs)


@pytest.mark.usefixtures("_isolated_git")
def test_write_must_be_bool(world):
    with pytest.raises(ValueError):
        g.subtree_push("sub", str(world["child"]), "sync/x", write="yes", cwd=str(world["mono"]))


@pytest.mark.usefixtures("_isolated_git")
def test_missing_subtree_is_clear_error(world, monkeypatch):
    monkeypatch.setattr(g, "_subtree_available", lambda *a, **k: False)
    with pytest.raises(g.SubtreePushError) as e:
        g.subtree_push("sub", str(world["child"]), "feature", cwd=str(world["mono"]))
    assert e.value.code == "subtree_unavailable"


# --- plan / write -------------------------------------------------------------------------

@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_push_refuses_remote_default_branch(world):
    _seed_child(world)
    with pytest.raises(ValueError, match="default branch trunk"):
        g.subtree_push("sub", str(world["child"]), "trunk", write=True, cwd=str(world["mono"]))


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_stale_subtree_split_corrected(stale_mono):
    root, stale, _ = stale_mono
    out = g.subtree_split_verified("sub", cwd=str(root), run=Spy(_stale_split_override(stale)))
    assert out["sha"] != stale and out["method"] == "filter-branch" and out["warning"] == "subtree_split_stale"
    assert out["trees_match"]
    assert _git(root, "rev-parse", out["sha"] + "^{tree}") == _git(root, "rev-parse", "HEAD:sub")
    assert not (_git_dir(root) / "FETCH_HEAD").exists()


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_tree_check_mutation_breaks_stale_detection(stale_mono, monkeypatch):
    """Mutation: with tree validation disabled the stale split is wrongly accepted."""
    root, stale, _ = stale_mono
    monkeypatch.setattr(g, "_subtree_split_matches_prefix_tree",
                        lambda *a, **k: {"prefix_tree": "a", "split_tree": "a", "trees_match": True})
    out = g.subtree_split_verified("sub", cwd=str(root), run=Spy(_stale_split_override(stale)))
    assert out["sha"] == stale


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_fallback_result_is_revalidated(stale_mono, monkeypatch):
    """A fallback that returns a wrong-tree commit is still rejected by the final tree check."""
    root, stale, _ = stale_mono
    monkeypatch.setattr(g, "_subtree_split_filter_branch", lambda *a, **k: stale)
    out = g.subtree_split_verified("sub", cwd=str(root), run=Spy(_stale_split_override(stale)))
    assert out["sha"] is None and out["reason"] == "stale_subtree_split" and out["method"] == "filter-branch"


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_remote_unavailable_fails_closed(world, tmp_path):
    with pytest.raises(g.SubtreePushError) as err:
        g.subtree_push("sub", str(tmp_path / "nope.git"), "sync/x", cwd=str(world["mono"]))
    assert err.value.code == "git_remote_unavailable"


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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

@pytest.mark.usefixtures("_isolated_git")
def test_push_with_retry_default_args_unchanged():
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert g.git_commit_push_with_retry(run=run, sleep=lambda s: None) == (True, "")
    (argv, kwargs), = seen
    env = kwargs.pop("env")
    assert (argv, kwargs) == (["git", "push", "origin", "HEAD"],
                              {"cwd": ".", "capture_output": True, "text": True, "timeout": 60.0,
                               "stdin": subprocess.DEVNULL})
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GCM_INTERACTIVE"] == "never"
    seen.clear()
    g.git_commit_push_with_retry(run=run, sleep=lambda s: None, push_args=("--", "/r", "a:refs/heads/b"))
    assert seen[0][0] == ["git", "push", "--", "/r", "a:refs/heads/b"]


@pytest.mark.usefixtures("_isolated_git")
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

@pytest.mark.usefixtures("_isolated_git")
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

@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_explicit_existing_compare_ref_overrides_default(world):
    split = _seed_child(world)
    _git(world["child"], "update-ref", "refs/heads/custom", split)
    out = g.subtree_push_plan("sub", str(world["child"]), "sync/custom",
                              compare_ref="custom", cwd=str(world["mono"]))
    assert out["push_guard"]["compare_ref"] == "custom"
    assert out["push_guard"]["compare_sha"] == split


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_explicit_compare_ref_skips_default_branch_ls_remote(world):
    split = _seed_child(world)
    _git(world["child"], "update-ref", "refs/heads/custom", split)
    spy = Spy()
    g.subtree_push_plan("sub", str(world["child"]), "sync/skip-lookup",
                        compare_ref="custom", cwd=str(world["mono"]), run=spy)
    ls_remote = [c for c in spy.calls if "ls-remote" in c]
    assert len(ls_remote) == 1


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_compare_fallback_to_master_when_default_unresolvable(world):
    split = _split(world["mono"])
    child = world["child"]
    _git(child, "update-ref", "-d", "refs/heads/trunk")
    _git(world["mono"], "push", "-q", str(child), f"{split}:refs/heads/master")
    _git(child, "symbolic-ref", "HEAD", "refs/heads/develop")
    out = g.subtree_push_plan("sub", str(child), "sync/master-fallback", cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "master", "compare_sha": split, "child_only_paths": []}


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_compare_fallback_to_trunk_when_trunk_is_not_default(world):
    split = _split(world["mono"])
    child = world["child"]
    _git(child, "update-ref", "-d", "refs/heads/trunk")
    _git(child, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(world["mono"], "push", "-q", str(child), f"{split}:refs/heads/trunk")
    out = g.subtree_push_plan("sub", str(child), "sync/trunk-fallback", cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "trunk", "compare_sha": split, "child_only_paths": []}


@pytest.mark.usefixtures("_isolated_git")
def test_push_guard_without_compare_reports_exact_message(world):
    split = _split(world["mono"])
    with pytest.raises(g.SubtreePushError) as exc:
        g._subtree_push_guards(str(world["mono"]), split, None, run=subprocess.run, timeout=30)
    assert exc.value.code == "compare_ref_unavailable"
    assert str(exc.value) == "compare_ref_unavailable: remote comparison ref is required for ancestor validation"


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_push_guard_without_comparison_fails_closed(world):
    split = _split(world["mono"])
    with pytest.raises(g.SubtreePushError) as exc:
        g._subtree_push_guards(str(world["mono"]), split, None, run=subprocess.run, timeout=30)
    assert exc.value.code == "compare_ref_unavailable"


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
@needs_subtree
def test_known_fallback_is_used_if_remote_head_is_unborn(world):
    split = _split(world["mono"])
    _git(world["mono"], "push", "-q", str(world["child"]), f"{split}:refs/heads/main")
    out = g.subtree_push_plan("sub", str(world["child"]), "sync/fallback", cwd=str(world["mono"]))
    assert out["push_guard"] == {"compare_ref": "main", "compare_sha": split, "child_only_paths": []}


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
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


@pytest.mark.usefixtures("_isolated_git")
def test_push_timeout_is_a_retried_network_error():
    """fetch-guard: a hung push is cut at the deadline and retried like a network error."""
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    sleeps = []
    assert g.git_commit_push_with_retry(run=run, sleep=sleeps.append, timeout=5) == (True, "ok")
    assert calls == [5, 5] and len(sleeps) == 1
    ok, output = g.git_commit_push_with_retry(
        run=lambda argv, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 5)),
        sleep=lambda s: None, delays=(), timeout=5)
    assert ok is False and "timed out after 5s" in output


@pytest.mark.usefixtures("_isolated_git")
def test_subtree_network_commands_never_prompt():
    seen = []

    def run(argv, **kwargs):
        seen.append((argv[1], kwargs.get("env")))
        return subprocess.CompletedProcess(argv, 0, "", "")

    g._subtree_git(".", ["ls-remote", "--", "origin"], run=run, timeout=9)
    g._subtree_git(".", ["rev-parse", "HEAD"], run=run, timeout=9)
    g._subtree_git(".", ["fetch", "-q", "origin"], run=run, timeout=9, env={"X": "1"})
    (_, remote_env), (_, local_env), (_, fetch_env) = seen
    assert remote_env["GIT_TERMINAL_PROMPT"] == "0" and local_env is None
    assert fetch_env["GIT_TERMINAL_PROMPT"] == "0" and fetch_env["X"] == "1"


@pytest.mark.usefixtures("_isolated_git")
def test_fetch_against_an_unreachable_remote_fails_fast(tmp_path):
    """Connection refusal is a transport failure, not proof of prompt suppression."""
    import time
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    start = time.monotonic()
    done = subprocess.run(["git", "-C", str(repo), "ls-remote", "https://127.0.0.1:9/x.git"],
                          capture_output=True, text=True, timeout=30, env=g._git_network_env(),
                          stdin=subprocess.DEVNULL)
    assert done.returncode != 0 and time.monotonic() - start < 30


# ═══ · gh ops pr retarget ════════════════════════════════════════════════════════════════════════════════
# pr-retarget in gh_ops: transport stub plus real git for empty commit messages.

gh_ops_gh_ops_pr_retarget = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules["gh_ops"] = gh_ops_gh_ops_pr_retarget
spec.loader.exec_module(gh_ops_gh_ops_pr_retarget)
NUMBER = 7
HEAD_SHA = "b" * 40
OLD_TIP = "c" * 40
MAIN_TIP = "d" * 40
TREE_SHA = "t" * 40


def reply(data=None, status=200):
    return gh_ops_gh_ops_pr_retarget.Response(status, data)


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
    return gh_ops_gh_ops_pr_retarget.Client(token="t0ken", transport=stub), stub


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
    assert gh_ops_gh_ops_pr_retarget._commit_message_subject("") == "(empty commit message)"
    assert gh_ops_gh_ops_pr_retarget._commit_message_subject("   \n\t\n") == "(empty commit message)"
    assert gh_ops_gh_ops_pr_retarget._commit_message_subject(None) == "(empty commit message)"
    assert gh_ops_gh_ops_pr_retarget._commit_message_subject("subject\n\nbody") == "subject"
    long_line = "x" * 120
    assert len(gh_ops_gh_ops_pr_retarget._commit_message_subject(long_line)) == 100
    assert gh_ops_gh_ops_pr_retarget._commit_message_subject(long_line) == "x" * 100


def test_validate_branch_name_strips_and_rejects():
    assert gh_ops_gh_ops_pr_retarget._validate_branch_name("  develop  ") == "develop"
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="refs/"):
        gh_ops_gh_ops_pr_retarget._validate_branch_name("refs/heads/main")
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="\\.\\."):
        gh_ops_gh_ops_pr_retarget._validate_branch_name("feat..parent")
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="invalid characters"):
        gh_ops_gh_ops_pr_retarget._validate_branch_name("feat parent")
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="\\.lock"):
        gh_ops_gh_ops_pr_retarget._validate_branch_name("release.lock")


def test_pr_retarget_rejects_non_string_base():
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="base is required"):
        gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, 123)  # type: ignore[arg-type]


def test_pr_retarget_old_base_ref_fetch_failure_is_tolerated_for_plan():
    routes = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = reply({"message": "missing"}, status=404)
    client, _ = client_for(routes)
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "planned"


def test_branch_tip_tree_match_rejects_empty_new_base_tree_with_nonempty_old():
    routes = {
        ("GET", f"/repos/{REPO}/git/ref/heads/main"): ref_route(MAIN_TIP),
        ("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp"): ref_route(OLD_TIP),
        ("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}"): commit_tree_route(""),
        ("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}"): commit_tree_route(TREE_SHA),
    }
    client, _ = client_for(routes)
    info = gh_ops_gh_ops_pr_retarget._branch_tip_tree_match(client, REPO, "main", "feat/p")
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
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_unmerged_base" and out["ok"] is False
    assert "unmerged_commit_tree_match" not in out
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_branch_tip_tree_match_returns_false_on_git_errors():
    client, _ = client_for({("GET", f"/repos/{REPO}/git/ref/heads/main"): reply({}, status=404)})
    assert gh_ops_gh_ops_pr_retarget._branch_tip_tree_match(client, REPO, "main", "feat/p")["matched"] is False


@pytest.mark.parametrize("name", ["-x", "feat/", "a~1", "a:b", "a^", "a?", "a*", "a[b", "a@{1", "/a", "a//b",
                                  ".a", "a\\b", "a.", "a/.b", "a.lock/b", "@", "a\tb", "a\x7fb"])
def test_validate_branch_name_rejects_git_invalid_names(name):
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError):
        gh_ops_gh_ops_pr_retarget._validate_branch_name(name)


def test_pr_retarget_plans_refuses_and_writes():
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[
        pr_payload(base_ref="feat/parent")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["ok"] is True and out["status"] == "planned" and out["dry_run"] is True
    assert out["old_base_unmerged_commits"] == 0 and "warning" not in out
    assert stub.calls[0]["method"] == "GET"

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["ok"] is True and out["status"] == "verified"
    patch_calls = [call for call in stub.calls if call["method"] == "PATCH"]
    assert patch_calls == [{"method": "PATCH", "path": f"/repos/{REPO}/pulls/{NUMBER}", "body": {"base": "main"}}]

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 3}))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["ok"] is False and out["status"] == "refused_unmerged_base"
    assert out["warning"] == "feat/parent has 3 commit(s) not in main; they would join the diff"
    assert all(call["method"] == "GET" for call in stub.calls)


def test_pr_retarget_tree_match_clears_unmerged_risk_after_squash_style_merge():
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 2}, tree_match=True,
                                             pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["unmerged_commit_tree_match"] is True
    assert "warning" not in out
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 2}, tree_match=True))
    out_write = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out_write["status"] == "verified"


def test_pr_retarget_compare_failure_requires_allow_compare_failure():
    client, stub = client_for(retarget_routes("gone", {}, compare_status=404, pr_reads=[
        pr_payload(base_ref="gone")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "not_found"
    assert out["warning"] == "could not compare gone with main"
    assert not any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="main"),
    ]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verified"
    assert out["old_base_tip_sha"] is None
    assert any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_verification_mismatch_ok_reason_dry_run_and_cli_exit():
    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
    ]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verification_mismatch"
    assert out["ok"] is False
    assert out["dry_run"] is False
    assert "after PATCH" in out["reason"]

    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True, pr_reads=[
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
        pr_payload(base_ref="gone"),
    ]))
    assert gh_ops_gh_ops_pr_retarget.main(["pr-retarget", REPO, str(NUMBER), "--write", "--allow-compare-failure"],
                         client=client) == 1


def test_pr_retarget_empty_compare_commit_messages_do_not_crash():
    compare = {"ahead_by": 2, "commits": [
        {"commit": {"message": ""}},
        {"commit": {"message": "   \n"}},
        {"commit": {"message": "real subject\n\nbody"}},
    ]}
    client, _ = client_for(retarget_routes("feat/parent", compare, pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["old_base_commit_subjects"] == ["(empty commit message)", "(empty commit message)", "real subject"]


def test_pr_retarget_not_open_merged_reason():
    client, _ = client_for({("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(
        pr_payload(state="closed", merged=True, base_ref="feat/x"))})
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "not_open" and out["ok"] is False and out["reason"] == "merged"


def test_pr_retarget_compare_non_404_error_is_not_labeled_not_found():
    client, _ = client_for(retarget_routes(
        "feat/p", {"ahead_by": 1}, compare_status=500, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["compare_error"] != "not_found"
    assert "HTTP 500" in out["compare_error"]


def test_pr_retarget_old_base_commit_subjects_limited_to_five():
    commits = [{"commit": {"message": f"subject-{index}"}} for index in range(6)]
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 6, "commits": commits},
                                         pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["old_base_commit_subjects"] == [f"subject-{index}" for index in range(5)]
    assert "subject-5" not in out["old_base_commit_subjects"]


def test_pr_retarget_render_unknown_old_base(capsys):
    routes = {("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(
        {"state": "open", "merged": False, "html_url": f"https://github.com/{REPO}/pull/{NUMBER}",
         "base": {}, "head": {"ref": "feat/x", "sha": HEAD_SHA}}),
        ("GET", f"/repos/{REPO}/compare/main..."): reply({"ahead_by": 0, "commits": []})}
    client, _ = client_for(routes)
    gh_ops_gh_ops_pr_retarget.main(["pr-retarget", REPO, str(NUMBER)], client=client)
    assert capsys.readouterr().out.startswith(f"planned {REPO}#{NUMBER} base (unknown) -> main")


def test_pr_retarget_includes_observed_at():
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
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
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
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
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr"
    assert "old base tip SHA changed" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_expect_head_sha_mismatch_refuses_write():
    before = pr_payload(base_ref="feat/parent")
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[before, before]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, expect_head_sha="a" * 40, client=client)
    assert out["status"] == "refused_stale_pr"
    assert "expect-head-sha" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_noops_and_validation():
    client, stub = client_for({("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(pr_payload())})
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "already_satisfied" and stub.calls == [{"method": "GET", "path": f"/repos/{REPO}/pulls/{NUMBER}", "body": None}]

    client, _ = client_for({("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(pr_payload(state="closed", base_ref="x"))})
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert out["status"] == "not_open" and out["ok"] is False

    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="base is required"):
        gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, " ")
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="positive"):
        gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, 0, "main")
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 0}, new_base="develop",
                                         pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "  develop  ", client=client)
    assert out["to"] == "develop"


def test_pr_retarget_cli_dry_run_and_write_exit_codes(capsys):
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 1}, pr_reads=[pr_payload(base_ref="feat/p")]))
    assert gh_ops_gh_ops_pr_retarget.main(["pr-retarget", REPO, str(NUMBER), "--base", "main"], client=client) == 0
    planned = capsys.readouterr().out
    assert planned.startswith(f"planned {REPO}#{NUMBER} base feat/p -> main")
    assert "feat/p has 1 commit(s) not in main; they would join the diff" in planned

    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 1}, pr_reads=[pr_payload(base_ref="feat/p")]))
    code = gh_ops_gh_ops_pr_retarget.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write"], client=client)
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
    assert gh_ops_gh_ops_pr_retarget._commit_message_subject(log.stdout) == "(empty commit message)"


def test_pr_retarget_expect_head_sha_matching_verifies_and_cli_passes_flag():
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, expect_head_sha=HEAD_SHA, client=client)
    assert out["status"] == "verified" and out["ok"] is True
    assert any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}))
    assert gh_ops_gh_ops_pr_retarget.main(["pr-retarget", REPO, str(NUMBER), "--write", "--expect-head-sha", HEAD_SHA],
                         client=client) == 0
    assert any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_expect_head_sha_invalid_exits_2_before_network(capsys):
    code = gh_ops_gh_ops_pr_retarget.main(["pr-retarget", REPO, str(NUMBER), "--expect-head-sha", "not-a-sha"])
    assert code == 2
    assert "SHA must be" in capsys.readouterr().err


def test_pr_retarget_allow_flags_are_not_interchangeable(capsys):
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "refused_unmerged_base" and out["ok"] is False

    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                           pr_reads=[pr_payload(base_ref="gone")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, allow_unmerged_commits=True, client=client)
    assert out["status"] == "refused_compare_failure" and out["ok"] is False

    client, stub = client_for(retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")]))
    assert gh_ops_gh_ops_pr_retarget.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write", "--allow-compare-failure"],
                         client=client) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused_unmerged_base"
    assert not any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                              pr_reads=[pr_payload(base_ref="gone")]))
    assert gh_ops_gh_ops_pr_retarget.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write", "--allow-unmerged-commits"],
                         client=client) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused_compare_failure"
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_refused_compare_failure_ok_false_and_cli_exit_one():
    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                           pr_reads=[pr_payload(base_ref="gone")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure" and out["ok"] is False
    client, _ = client_for(retarget_routes("gone", {}, compare_status=404, old_ref_missing=True,
                                           pr_reads=[pr_payload(base_ref="gone")]))
    assert gh_ops_gh_ops_pr_retarget.main(["pr-retarget", REPO, str(NUMBER), "--write"], client=client) == 1


def test_pr_retarget_missing_ahead_by_is_compare_failure():
    client, _ = client_for(retarget_routes("feat/p", {"commits": []}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "missing ahead_by in compare response"


def test_pr_retarget_empty_tree_shas_do_not_count_as_match():
    routes = retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route("")
    routes[("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}")] = commit_tree_route("")
    client, _ = client_for(routes)
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert "unmerged_commit_tree_match" not in out
    assert out["warning"] == "feat/p has 2 commit(s) not in main; they would join the diff"


def test_pr_retarget_stale_merged_before_patch():
    before = pr_payload(base_ref="feat/parent")
    merged = pr_payload(base_ref="feat/parent", state="closed", merged=True)
    client, stub = client_for(retarget_routes("feat/parent", {"ahead_by": 0}, pr_reads=[before, merged]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr" and out["reason"] == "merged"
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_old_tip_stale_matrix():
    sha = OLD_TIP
    assert gh_ops_gh_ops_pr_retarget._pr_retarget_old_tip_stale(sha, sha, allow_both_missing=False) == ""
    assert gh_ops_gh_ops_pr_retarget._pr_retarget_old_tip_stale(None, None, allow_both_missing=False) == (
        "old base tip could not be verified before PATCH")
    assert gh_ops_gh_ops_pr_retarget._pr_retarget_old_tip_stale(None, None, allow_both_missing=True) == ""
    assert "consistently" in gh_ops_gh_ops_pr_retarget._pr_retarget_old_tip_stale(None, sha, allow_both_missing=True)


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
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
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
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr"
    assert out["ok"] is False
    assert "old base tip could not be verified before PATCH" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_git_ref_tip_sha_raises_when_object_has_no_sha():
    client, _ = client_for({("GET", f"/repos/{REPO}/git/ref/heads/main"): reply({"object": {}})})
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="returned no SHA"):
        gh_ops_gh_ops_pr_retarget._git_ref_tip_sha(client, REPO, "main")


def test_pr_retarget_tip_stale_refusal_ok_false_and_cli_exit_one(capsys):
    routes = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/p"), pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = [
        reply({"message": "missing"}, status=404),
        ref_route(OLD_TIP),
    ]
    client, _ = client_for(routes)
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr" and out["ok"] is False
    routes_cli = retarget_routes("feat/p", {"ahead_by": 0}, pr_reads=[pr_payload(base_ref="feat/p"), pr_payload(base_ref="feat/p")])
    routes_cli[("GET", f"/repos/{REPO}/git/ref/heads/feat%2Fp")] = [
        reply({"message": "missing"}, status=404),
        ref_route(OLD_TIP),
    ]
    client, stub = client_for(routes_cli)
    assert gh_ops_gh_ops_pr_retarget.main(["--json", "pr-retarget", REPO, str(NUMBER), "--write"], client=client) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused_stale_pr"
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_negative_ahead_by_is_compare_failure():
    client, stub = client_for(retarget_routes("feat/p", {"ahead_by": -1}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "negative ahead_by in compare response"
    assert out["old_base_unmerged_commits"] is None
    assert not any(call["method"] == "PATCH" for call in stub.calls)

    client, stub = client_for(retarget_routes("feat/p", {"ahead_by": -1}, pr_reads=[
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="feat/p"),
        pr_payload(base_ref="main"),
    ]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, allow_compare_failure=True, client=client)
    assert out["status"] == "verified"
    assert any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_invalid_ahead_by_string_is_compare_failure():
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": "nope"}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "invalid ahead_by in compare response"


def test_pr_retarget_null_ahead_by_is_compare_failure():
    client, _ = client_for(retarget_routes("feat/p", {"ahead_by": None}, pr_reads=[pr_payload(base_ref="feat/p")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_compare_failure"
    assert out["compare_error"] == "null ahead_by in compare response"


def test_pr_retarget_tree_match_requires_nonempty_trees_on_each_side():
    routes = retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route("")
    routes[("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}")] = commit_tree_route(TREE_SHA)
    client, _ = client_for(routes)
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert "unmerged_commit_tree_match" not in out

    routes = retarget_routes("feat/p", {"ahead_by": 2}, pr_reads=[pr_payload(base_ref="feat/p")])
    routes[("GET", f"/repos/{REPO}/git/commits/{MAIN_TIP}")] = commit_tree_route(TREE_SHA)
    routes[("GET", f"/repos/{REPO}/git/commits/{OLD_TIP}")] = commit_tree_route("")
    client, _ = client_for(routes)
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
    assert "unmerged_commit_tree_match" not in out


def test_pr_retarget_unknown_old_base_with_ahead_by_still_refuses_unmerged():
    routes = {("GET", f"/repos/{REPO}/pulls/{NUMBER}"): reply(
        {"state": "open", "merged": False, "html_url": f"https://github.com/{REPO}/pull/{NUMBER}",
         "base": {}, "head": {"ref": "feat/x", "sha": HEAD_SHA}}),
        ("GET", f"/repos/{REPO}/compare/main..."): reply({"ahead_by": 2, "commits": []})}
    client, stub = client_for(routes)
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
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
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", write=True, client=client)
    assert out["status"] == "refused_stale_pr"
    assert "could not be verified consistently" in out["reason"]
    assert not any(call["method"] == "PATCH" for call in stub.calls)


def test_pr_retarget_tree_compare_records_tip_shas():
    client, _ = client_for(retarget_routes("feat/parent", {"ahead_by": 2}, tree_match=True,
                                             pr_reads=[pr_payload(base_ref="feat/parent")]))
    out = gh_ops_gh_ops_pr_retarget.pr_retarget(REPO, NUMBER, "main", client=client)
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
    code = gh_ops_gh_ops_pr_retarget.main(_matrix_argv(cell), client=client)
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
    code = gh_ops_gh_ops_pr_retarget.main(["--json", "pr-retarget", REPO, str(NUMBER), f"--base={name}", "--write"], client=client)
    assert code == 2
    assert message in capsys.readouterr().err
    assert stub.calls == []


def test_other_commands_do_not_take_pr_retarget_dispatch():
    """Cover the command-dispatch arms that are not pr-retarget (branch coverage)."""
    assert gh_ops_gh_ops_pr_retarget.main(["url", "compare", REPO, "main", "feature"]) == 0
    rendered = gh_ops_gh_ops_pr_retarget._render("pr-edit", {"ok": True, "dry_run": True, "reason": ""})
    assert rendered.startswith("OK pr-edit")
    warning = "feat/p has 1 commit(s) not in main; they would join the diff"
    warned = gh_ops_gh_ops_pr_retarget._render("pr-retarget", {
        "status": "planned", "repository": REPO, "number": NUMBER,
        "from": "feat/p", "to": "main", "warning": warning,
    })
    assert warned == f"planned {REPO}#{NUMBER} base feat/p -> main ({warning})"
    assert gh_ops_gh_ops_pr_retarget._render("pr-retarget", {
        "status": "planned", "repository": REPO, "number": NUMBER,
        "from": None, "to": "main",
    }) == f"planned {REPO}#{NUMBER} base (unknown) -> main"


def test_validate_branch_segment_rejects_empty_segment():
    with pytest.raises(gh_ops_gh_ops_pr_retarget.GhOpsError, match="must not contain empty path segments"):
        gh_ops_gh_ops_pr_retarget._validate_branch_segment("", field="base")

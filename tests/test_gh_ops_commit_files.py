"""commit-files: one multi-file commit through the Git Data API, planned first (in-memory API)."""
import base64
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gh_ops", ROOT / "gh_ops.py")
gh_ops = sys.modules.get("gh_ops") or importlib.util.module_from_spec(spec)
if "gh_ops" not in sys.modules:
    sys.modules["gh_ops"] = gh_ops
    spec.loader.exec_module(gh_ops)

REPO = "octo/demo"


class Remote:
    """A transport serving one repository's refs, commits, trees and blobs."""

    def __init__(self, files, default_branch="main"):
        self.default_branch = default_branch
        self.trees = {"t0": {path: gh_ops._git_blob_sha(data) for path, data in files.items()}}
        self.commits = {"c0": "t0"}
        self.head = "c0" * 20
        self.commits[self.head] = "t0"
        self.calls = []
        self.refuse_patch = self.truncated = self.bad_blob = False

    def __call__(self, method, url, body, headers):
        path = urllib.parse.urlsplit(url).path
        self.calls.append((method, path))
        ok = lambda data, status=200: gh_ops.Response(status, data, {})
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
            return ok({"sha": "0" * 40 if self.bad_blob else gh_ops._git_blob_sha(data)}, 201)
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
        return gh_ops.Client(token="t0ken", transport=self)


def test_plan_then_write_one_verified_commit():
    remote = Remote({"keep.txt": b"same", "old.txt": b"bye", "edit.txt": b"v1"})
    files = {"keep.txt": b"same", "edit.txt": b"v2", "dir/new.txt": b"hi"}
    plan = gh_ops.commit_files(REPO, "feat", files, "msg", deletes=("old.txt", "never.txt"), client=remote.client())
    assert plan["ok"] and plan["dry_run"] and not plan["changed"]
    assert {i["path"]: i["change"] for i in plan["plan"]} == {
        "dir/new.txt": "new", "edit.txt": "changed", "keep.txt": "unchanged", "old.txt": "delete", "never.txt": "absent"}
    assert {m for m, _ in remote.calls} == {"GET"}
    done = gh_ops.commit_files(REPO, "feat", files, "msg", deletes=("old.txt",), expect_head="c0c0c0",
                               write=True, client=remote.client())
    assert done["ok"] and done["verified"] and remote.head == done["commit_sha"]
    assert remote.trees[remote.commits[remote.head]] == {
        "keep.txt": gh_ops._git_blob_sha(b"same"), "edit.txt": gh_ops._git_blob_sha(b"v2"),
        "dir/new.txt": gh_ops._git_blob_sha(b"hi")}
    assert sum(1 for m, p in remote.calls if p.endswith("/git/blobs")) == 2  # unchanged keep.txt not uploaded
    text = gh_ops._render("commit-files", done)
    assert text.startswith("OK commit-files octo/demo@feat (applied) head c0c0c0c ->") and "changed   edit.txt" in text


def test_refusals_and_failures_never_move_the_branch():
    remote = Remote({"a.txt": b"x"})
    start = remote.head
    same = gh_ops.commit_files(REPO, "b", {"a.txt": b"x"}, "m", write=True, client=remote.client())
    assert same["ok"] and same["reason"] == "unchanged"
    main = gh_ops.commit_files(REPO, "main", {"a.txt": b"y"}, "m", write=True, client=remote.client())
    assert not main["ok"] and "default branch" in main["reason"]
    moved = gh_ops.commit_files(REPO, "b", {"a.txt": b"y"}, "m", expect_head="ffff", write=True, client=remote.client())
    assert not moved["ok"] and "branch moved" in moved["reason"]
    remote.refuse_patch = True
    refused = gh_ops.commit_files(REPO, "b", {"a.txt": b"y"}, "m", write=True, client=remote.client())
    assert not refused["ok"] and "ref update refused" in refused["reason"] and remote.head == start
    remote.refuse_patch, remote.truncated = False, True
    unknown = gh_ops.commit_files(REPO, "b", {"a.txt": b"y"}, "m", deletes=("z",), client=remote.client())
    assert {i["change"] for i in unknown["plan"]} == {"unknown"}
    remote.truncated, remote.bad_blob = False, True
    bad = gh_ops.commit_files(REPO, "b", {"a.txt": b"y"}, "m", write=True, client=remote.client())
    assert not bad["ok"] and "blob SHA mismatch" in bad["reason"] and remote.head == start
    allowed = gh_ops.commit_files(REPO, "main", {"a.txt": b"y"}, "m", allow_default_branch=True, client=remote.client())
    assert allowed["ok"] and allowed["dry_run"]


@pytest.mark.parametrize("kwargs", [
    {"branch": ""}, {"branch": "-x"}, {"branch": "a..b"}, {"message": " "}, {"files": {}},
    {"files": {"../x": b""}}, {"files": {"/x": b""}}, {"files": {".git/config": b""}}, {"files": {"a\\b": b""}},
    {"files": {"a": b""}, "deletes": ("a",)}, {"files": {"a": "text"}},
    {"files": {f"f{i}": b"" for i in range(101)}}, {"expect_head": "zz"},
])
def test_validation(kwargs):
    args = {"branch": "b", "files": {"a.txt": b"x"}, "message": "m", **kwargs}
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.commit_files(REPO, args.pop("branch"), args.pop("files"), args.pop("message"),
                            client=Remote({"a.txt": b"q"}).client(), **args)


def test_cli_reads_local_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "local.txt").write_bytes(b"new")
    (tmp_path / "other.bin").write_bytes(b"\0")
    monkeypatch.chdir(tmp_path)
    remote = Remote({"a.txt": b"x"})
    assert gh_ops.main(["commit-files", REPO, "--branch", "b", "--file", "a.txt=other.bin", "--file", "local.txt",
                        "--message", "m"], client=remote.client()) == 0
    assert "new       local.txt (3 bytes" in capsys.readouterr().out
    assert gh_ops.main(["--json", "commit-files", REPO, "--branch", "b", "--file", "local.txt", "--message", "m",
                        "--write"], client=remote.client()) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert gh_ops.main(["commit-files", REPO, "--branch", "b", "--message", "m"], client=remote.client()) == 2

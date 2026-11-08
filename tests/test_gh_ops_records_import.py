"""records-import: merge a dashboard export into site/records.jsonl, locally or as one commit (+PR)."""
import base64
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gh_ops_commit_files import Remote, gh_ops, REPO  # noqa: E402

EXPORT = {"schema": "web-ui-drafts/1", "exported": "2026-11-08T04:00:00Z",
          "drafts": {"Demo#4:state": "done", "Demo#4:notes": "", "Demo#5:state": "doing", "Demo#5:notes": "rename only"}}
CURRENT = '{"id": "Demo#4", "state": "doing", "notes": "merged first", "next": "watch CI"}\n{"id": "Demo#2", "state": "waiting"}\n'


def test_merge_keeps_order_and_unrelated_fields_and_removes_empty_fields():
    merged = gh_ops.merge_records(CURRENT, gh_ops.drafts_from_export(EXPORT))
    assert (merged["added"], merged["updated"], merged["removed"], merged["unchanged"]) == (["Demo#5"], ["Demo#4"], [], [])
    rows = [json.loads(line) for line in merged["text"].splitlines()]
    assert rows == [{"id": "Demo#4", "next": "watch CI", "state": "done"}, {"id": "Demo#2", "state": "waiting"},
                    {"id": "Demo#5", "notes": "rename only", "state": "doing"}]
    again = gh_ops.merge_records(merged["text"], gh_ops.drafts_from_export(EXPORT))
    assert again["unchanged"] == ["Demo#4", "Demo#5"] and again["text"] == merged["text"]
    gone = gh_ops.merge_records(merged["text"], {"Demo#2": {"state": ""}})
    assert gone["removed"] == ["Demo#2"] and "Demo#2" not in gone["text"]


def test_export_validation():
    assert gh_ops.drafts_from_export({"Demo#i9:notes": "bare mapping"}) == {"Demo#i9": {"notes": "bare mapping"}}
    for bad in ({"drafts": {"x:foo": "1"}}, {"drafts": {"Demo#1:state": "nope"}}, {"drafts": {"Demo#1:notes": 3}}, [], {"drafts": {"no-number:state": ""}}):
        with pytest.raises(gh_ops.GhOpsError):
            gh_ops.drafts_from_export(bad)
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.records_from_jsonl('{"state": "done"}\n')


def test_local_import_plans_then_writes(tmp_path):
    export = tmp_path / "records.json"
    export.write_text(json.dumps(EXPORT), encoding="utf-8")
    records = tmp_path / "site" / "records.jsonl"
    plan = gh_ops.records_import(str(export), records_path=str(records))
    assert plan["ok"] and plan["dry_run"] and plan["added"] == ["Demo#4", "Demo#5"] and not records.exists()
    done = gh_ops.records_import(str(export), records_path=str(records), write=True)
    assert done["written"] and json.loads(records.read_text().splitlines()[1]) == {"id": "Demo#5", "notes": "rename only", "state": "doing"}
    text = gh_ops._render("records-import", done)
    assert text.startswith("OK records-import") and "added     Demo#4, Demo#5" in text


class RecordsRemote(Remote):
    """The commit-files stub plus repository contents, ref creation and pulls."""

    def __init__(self, files, *, branch_exists=False):
        super().__init__(files)
        self.branch_exists = branch_exists
        self.pulls = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        ok = lambda data, status=200: gh_ops.Response(status, data, {})
        if "/contents/" in path and method == "GET":
            self.calls.append((method, path))
            name = urllib.parse.unquote(path.split("/contents/", 1)[1])
            tree = self.trees[self.commits[self.head]]
            if name not in tree:
                return ok({"message": "Not Found"}, 404)
            data = self.blobs[name]
            return ok({"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if "/git/ref/heads/" in path and method == "GET" and not self.branch_exists and path.endswith("/audit%2Frecords"):
            self.calls.append((method, path))
            return ok({"message": "Not Found"}, 404)
        if path.endswith("/git/refs") and method == "POST":
            self.calls.append((method, path))
            assert body == {"ref": "refs/heads/audit/records", "sha": self.head}
            self.branch_exists = True
            return ok({"ref": body["ref"]}, 201)
        if path.endswith("/pulls") and method == "GET":
            self.calls.append((method, path))
            return ok(self.pulls)
        if path.endswith("/pulls") and method == "POST":
            self.calls.append((method, path))
            self.pulls.append({"number": 7, "head": {"ref": body["head"]}})
            return ok({"number": 7}, 201)
        return super().__call__(method, url, body, headers)


def test_remote_import_creates_branch_commits_once_and_opens_a_pr(tmp_path):
    export = tmp_path / "records.json"
    export.write_text(json.dumps(EXPORT), encoding="utf-8")
    remote = RecordsRemote({"site/records.jsonl": CURRENT.encode()})
    remote.blobs = {"site/records.jsonl": CURRENT.encode()}
    plan = gh_ops.records_import(str(export), repo=REPO, branch="audit/records", open_pr=True, client=remote.client())
    assert plan["ok"] and plan["dry_run"] and plan["source_ref"] == "main" and plan["plan"] == {"create_branch": True, "open_pr": True}
    assert {m for m, _ in remote.calls} == {"GET"}
    done = gh_ops.records_import(str(export), repo=REPO, branch="audit/records", open_pr=True, write=True, client=remote.client())
    assert done["ok"] and done["created_branch"] and done["commit"]["ok"] and done["pr"] == 7
    committed = remote.trees[remote.commits[remote.head]]["site/records.jsonl"]
    expected = gh_ops.merge_records(CURRENT, gh_ops.drafts_from_export(EXPORT))["text"].encode()
    assert committed == gh_ops._git_blob_sha(expected)
    assert sum(1 for m, p in remote.calls if m == "POST" and p.endswith("/pulls")) == 1
    text = gh_ops._render("records-import", done)
    assert "-> octo/demo@audit/records (applied)" in text and "pr        #7" in text


def test_remote_import_with_nothing_to_change_writes_nothing(tmp_path):
    export = tmp_path / "records.json"
    export.write_text(json.dumps({"drafts": {"Demo#4:state": "doing"}}), encoding="utf-8")
    remote = RecordsRemote({"site/records.jsonl": CURRENT.encode()}, branch_exists=True)
    remote.blobs = {"site/records.jsonl": CURRENT.encode()}
    done = gh_ops.records_import(str(export), repo=REPO, branch="audit/records", write=True, client=remote.client())
    assert done["ok"] and done["reason"] == "records already up to date" and done["source_ref"] == "audit/records"
    assert {m for m, _ in remote.calls} == {"GET"}
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.records_import(str(export), repo=REPO, client=remote.client())

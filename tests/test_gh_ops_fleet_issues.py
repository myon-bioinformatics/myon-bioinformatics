"""fleet-issues: one Issue per remaining fact of a fleet report, matched by key, numbers kept, dry-run first."""
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_gh_ops_commit_files import gh_ops, REPO  # noqa: E402

REPORT = {
    "schema": "vendor-fleet-report/1", "generated_at": "2026-11-09T04:00:00+00:00", "parent_revision": "5d6f424f" + "0" * 32,
    "remaining": [
        {"severity": "updated", "key": "updated|vendor|xprobe:xprobe.py|blob →9d9071f", "no": "FLEET-1", "what": "📥 xprobe:xprobe.py",
         "detail": "blob 7595fe5→9d9071f → 2× BrowserTestKit (tests/vendor/xprobe/xprobe.py), Err-o-RoadRunner (vendor/xprobe.py)",
         "repositories": ["BrowserTestKit", "Err-o-RoadRunner"], "rows": 2},
        {"severity": "failure", "key": "failure|vendor|PySaiyanWeBoo:|source path removed", "no": "FLEET-2", "what": "📥 PySaiyanWeBoo:",
         "detail": "source path removed at dabd14b → 3× Ironmate (a.py ← x.py, b.py ← y.py), github.io (c.css ← z.css)",
         "repositories": ["Ironmate", "github.io"], "rows": 3},
        {"severity": "insufficient", "key": "target:BrowserTestKit", "no": "FLEET-3", "what": "BrowserTestKit",
         "detail": "registry ≠ vendor.lock.json: missing 4, unexpected 0", "repositories": ["BrowserTestKit"]},
    ],
}


class Issues:
    """A transport serving /issues (list, create, patch, comments) and /labels for one repository."""

    def __init__(self, issues=(), labels=("bug", "enhancement")):
        self.issues = {issue["number"]: dict(issue) for issue in issues}
        self.labels = list(labels)
        self.calls = []
        self.comments = []

    def __call__(self, method, url, body, headers):
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        self.calls.append((method, path))
        ok = lambda data, status=200: gh_ops.Response(status, data, {})
        if path == f"/repos/{REPO}/labels":
            return ok([{"name": name} for name in self.labels])
        if path == f"/repos/{REPO}/issues" and method == "GET":
            return ok([dict(i, labels=[{"name": n} for n in i.get("label_names", [])]) for i in self.issues.values()])
        if path == f"/repos/{REPO}/issues" and method == "POST":
            number = max([100, *self.issues]) + 1
            self.issues[number] = {"number": number, "state": "open", "title": body["title"], "body": body["body"],
                                   "label_names": body.get("labels", []), "html_url": f"https://github.com/{REPO}/issues/{number}"}
            return ok(self.issues[number], 201)
        if path.startswith(f"/repos/{REPO}/issues/") and path.endswith("/comments") and method == "POST":
            self.comments.append((int(path.split("/")[-2]), body["body"]))
            return ok({"id": len(self.comments)}, 201)
        if path.startswith(f"/repos/{REPO}/issues/") and method == "PATCH":
            issue = self.issues[int(path.rsplit("/", 1)[1])]
            for field in ("title", "body", "state"):
                if field in body:
                    issue[field] = body[field]
            if "labels" in body:
                issue["label_names"] = body["labels"]
            return ok(issue)
        raise AssertionError((method, path))

    def client(self):
        return gh_ops.Client(token="t0ken", transport=self)


def report_file(tmp_path, report=REPORT):
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return str(path)


def issue(number, key, no, *, state="open", title=None):
    return {"number": number, "state": state, "title": title or f"{no} 📥 old title", "body": f"{gh_ops.FLEET_MARKER}{key} -->\n\nold",
            "html_url": f"https://github.com/{REPO}/issues/{number}", "label_names": []}


def test_plan_creates_nothing_and_shows_every_action(tmp_path):
    remote = Issues()
    plan = gh_ops.fleet_issues(report_file(tmp_path), REPO, client=remote.client())
    assert plan["ok"] and plan["dry_run"] and plan["actions"] == {"create": 3, "update": 0, "reopen": 0, "unchanged": 0, "close": 0}
    assert all(call[0] == "GET" for call in remote.calls) and plan["writes"] == 0
    rows = {row["key"]: row for row in plan["rows"]}
    assert rows[REPORT["remaining"][0]["key"]]["title"] == "FLEET-1 📥 xprobe:xprobe.py · blob 7595fe5→9d9071f (2×)"
    assert rows[REPORT["remaining"][0]["key"]]["labels"] == ["enhancement"]
    assert rows[REPORT["remaining"][1]["key"]]["labels"] == ["bug"]
    text = gh_ops._render("fleet-issues", plan)
    assert text.startswith("OK fleet-issues plan") and "🆕 #? FLEET-2 📥 PySaiyanWeBoo: · source path removed at dabd14b (3×) [bug]" in text
    long = dict(REPORT["remaining"][1], detail="source path removed at dabd14b; the pinned commit still has it (re-point) → 1× Ironmate (a.py)")
    assert gh_ops.fleet_issue_text(long, REPORT)[0] == "FLEET-2 📥 PySaiyanWeBoo: · source path removed at dabd14b (3×)"
    assert "re-point" in gh_ops.fleet_issue_text(long, REPORT)[1]


def test_write_creates_updates_reopens_keeps_numbers_and_verifies(tmp_path):
    existing = [issue(7, REPORT["remaining"][0]["key"], "FLEET-9"),               # same fact, numbered 9 by an earlier run
                issue(8, REPORT["remaining"][1]["key"], "FLEET-4", state="closed")]  # came back after being closed
    remote = Issues(existing, labels=("bug", "enhancement", "fleet"))
    done = gh_ops.fleet_issues(report_file(tmp_path), REPO, write=True, client=remote.client())
    assert done["ok"] and not done["dry_run"]
    assert done["actions"] == {"create": 1, "update": 1, "reopen": 1, "unchanged": 0, "close": 0}
    by_key = {row["key"]: row for row in done["rows"]}
    first = by_key[REPORT["remaining"][0]["key"]]
    assert (first["no"], first["number"], first["action"]) == ("FLEET-9", 7, "update")  # the Issue's number wins
    assert remote.issues[7]["title"].startswith("FLEET-9 📥 xprobe:xprobe.py")
    second = by_key[REPORT["remaining"][1]["key"]]
    assert (second["no"], second["action"], remote.issues[8]["state"]) == ("FLEET-4", "reopen", "open")
    third = by_key["target:BrowserTestKit"]
    assert third["action"] == "create" and third["no"] == "FLEET-3" and third["number"] == 101
    assert remote.issues[101]["label_names"] == ["bug", "fleet"]
    assert all(row["verified"] for row in done["rows"])
    body = remote.issues[7]["body"]
    assert body.startswith(gh_ops.FLEET_MARKER + REPORT["remaining"][0]["key"] + " -->")
    assert "- BrowserTestKit (tests/vendor/xprobe/xprobe.py)" in body and "- Err-o-RoadRunner (vendor/xprobe.py)" in body
    assert "- Ironmate (a.py ← x.py, b.py ← y.py)" in remote.issues[8]["body"]  # commas inside parentheses stay together
    again = gh_ops.fleet_issues(report_file(tmp_path), REPO, write=True, client=remote.client())
    assert again["actions"]["unchanged"] == 3 and again["writes"] == 0


def test_new_facts_avoid_numbers_any_issue_holds(tmp_path):
    remote = Issues([issue(5, "other|fact", "FLEET-3")])  # FLEET-3 is taken by a fact not in this report
    plan = gh_ops.fleet_issues(report_file(tmp_path), REPO, client=remote.client())
    numbers = {row["key"]: row["no"] for row in plan["rows"]}
    assert numbers["target:BrowserTestKit"] == "FLEET-4"
    assert len(set(numbers.values())) == 3


def test_close_resolved_only_with_the_flag_and_only_open_fleet_issues(tmp_path):
    remote = Issues([issue(5, "gone|fact", "FLEET-3"), issue(6, "gone|closed", "FLEET-8", state="closed"),
                     {"number": 9, "state": "open", "title": "unrelated issue", "body": "no marker", "html_url": "", "label_names": []}])
    plain = gh_ops.fleet_issues(report_file(tmp_path), REPO, client=remote.client())
    assert plain["actions"]["close"] == 0
    plan = gh_ops.fleet_issues(report_file(tmp_path), REPO, close_resolved=True, client=remote.client())
    assert [row["number"] for row in plan["rows"] if row["action"] == "close"] == [5]
    done = gh_ops.fleet_issues(report_file(tmp_path), REPO, close_resolved=True, write=True, client=remote.client())
    assert done["ok"] and remote.issues[5]["state"] == "closed" and remote.issues[9]["state"] == "open"
    assert remote.comments and remote.comments[0][0] == 5 and "閉じます" in remote.comments[0][1]


def test_severity_filter_and_bad_report(tmp_path):
    remote = Issues()
    plan = gh_ops.fleet_issues(report_file(tmp_path), REPO, severities=("failure",), client=remote.client())
    assert [row["severity"] for row in plan["rows"]] == ["failure"]
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.fleet_issues(str(bad), REPO, client=remote.client())


def test_cli_plan_and_real_report_shape(tmp_path, capsys):
    remote = Issues()
    assert gh_ops.main(["fleet-issues", report_file(tmp_path), "--repo", REPO], client=remote.client()) == 0
    assert "fleet-issues plan" in capsys.readouterr().out
    # The real producer's remaining items file without any adaptation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import vendor_fleet_plan as fleet
    result = {"graph": {"cycles": [["a:x", "b:y", "a:x"]], "findings": [{"repository": "r", "kind": "two-sources", "subject": "r:v.py",
                                                                        "detail": "v.py is copied from a:x and b:y"}]},
              "rows": [{"id": "vendor:r:v.py<-a:x", "kind": "vendor", "status": "updated", "reason": "blob 1→2", "source": "a:x",
                        "target": "r:v.py", "repository": "r"}],
              "targets": {"repositories": [{"repository": "s", "category": "insufficient", "reasons": ["lock outside every hub"]}]},
              "plan": {"steps": []}}
    items = fleet.remaining(result)
    real = {"schema": "vendor-fleet-report/1", "generated_at": "2026-11-09T05:00:00+00:00", "remaining": items}
    plan = gh_ops.fleet_issues(report_file(tmp_path, real), REPO, client=remote.client())
    assert plan["actions"]["create"] == 4
    titles = [row["title"] for row in plan["rows"]]
    assert any(t.startswith("FLEET-1 execution cycle") for t in titles)
    assert any("📥 a:x · blob 1→2" in t for t in titles)

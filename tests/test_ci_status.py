import hashlib, io, json, unittest
from contextlib import redirect_stderr, redirect_stdout; from datetime import datetime, timezone; from pathlib import Path; from unittest.mock import patch
import ci_status as ci

class StatusTests(unittest.TestCase):
    def test_vendor_lock_provenance(self):
        root = Path(ci.__file__).resolve().parent
        lock = json.loads((root / "vendor.lock.json").read_text(encoding="utf-8"))
        for item in lock["files"]:
            actual = (root / item["destination"]).read_bytes()
            self.assertEqual(hashlib.sha256(actual).hexdigest(), item["sha256"])
        self.assertIsNotNone(ci.ghi)

    def test_repository_normalization(self):
        self.assertEqual(ci.repository("Ironmate"), "myon-bioinformatics/Ironmate")
        for name in ("../../x", "..", "a/..", "./x", "a/."):
            with self.assertRaises(ValueError):
                ci.repository(name)

    def test_pr_progress(self):
        def fake(path):
            if "/pulls/7" in path:
                return {"head": {"sha": "a" * 40}}
            return {"total_count": 2, "check_runs": [
                {"id": 1, "name": "pytest", "status": "completed", "conclusion": "success"},
                {"id": 2, "name": "windows", "status": "in_progress", "conclusion": None}]}
        checks = [{"id": 1, "name": "pytest", "status": "completed", "conclusion": "success", "url": None},
                  {"id": 2, "name": "windows", "status": "in_progress", "conclusion": None, "url": None}]
        with patch.object(ci, "get", side_effect=fake), patch.object(ci.ghi, "checks_for_sha", return_value={"complete": True, "checks": checks}):
            item = ci.observe("Ironmate", 7)
        self.assertEqual((item["state"], item["completed"], item["total"]), ("RUNNING", 1, 2))

    def test_failed_check(self):
        def fake(path):
            if path.endswith("/repos/myon-bioinformatics/Ironmate"):
                return {"default_branch": "main"}
            if "/git/ref/heads/main" in path:
                return {"object": {"sha": "b" * 40}}
            return {"total_count": 1, "check_runs": [
                {"id": 3, "name": "test", "status": "completed", "conclusion": "failure"}]}
        checks = [{"id": 3, "name": "test", "status": "completed", "conclusion": "failure", "url": None}]
        with patch.object(ci, "get", side_effect=fake), patch.object(ci.ghi, "checks_for_sha", return_value={"complete": True, "checks": checks}):
            self.assertEqual(ci.observe("Ironmate")["state"], "FAILED")

    def test_non_green_conclusions(self):
        for conclusion in ("cancelled", "stale", "skipped", "neutral", None):
            def fake(path):
                if "/pulls/7" in path:
                    return {"head": {"sha": "a" * 40}}
                return {"total_count": 1, "check_runs": [
                    {"id": 1, "name": "test", "status": "completed", "conclusion": conclusion}]}
            checks = [{"id": 1, "name": "test", "status": "completed", "conclusion": conclusion, "url": None}]
            with patch.object(ci, "get", side_effect=fake), patch.object(ci.ghi, "checks_for_sha", return_value={"complete": True, "checks": checks}):
                self.assertEqual(ci.observe("Ironmate", 7)["state"], "INCOMPLETE")

    def test_default_branch_is_encoded_as_one_url_component(self):
        for branch, encoded in (("開発", "%E9%96%8B%E7%99%BA"),
                                ("release/#next", "release%2F%23next")):
            with self.subTest(branch=branch):
                base = "/repos/myon-bioinformatics/Ironmate"
                expected = base + "/git/ref/heads/" + encoded
                def fake(path):
                    if path == base:
                        return {"default_branch": branch}
                    self.assertEqual(path, expected)
                    return {"object": {"sha": "b" * 40}}
                with patch.object(ci, "get", side_effect=fake) as get, patch.object(
                    ci.ghi, "checks_for_sha", return_value={"complete": True, "checks": []}
                ) as checks:
                    item = ci.observe("Ironmate")
                self.assertEqual(get.call_count, 2)
                checks.assert_called_once_with("myon-bioinformatics/Ironmate", "b" * 40)
                self.assertEqual(item["target"], branch)

    def test_json_cli(self):
        with patch.object(ci, "observe", return_value={"repository": "o/r", "checks": []}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(ci.main(["Ironmate", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["schema"], "ci-observation/1")

    def test_cli_observation_timestamp_is_shared_by_text_and_json(self):
        observed = datetime(2026, 10, 20, 23, 0, 1, 123456, tzinfo=timezone.utc)
        one = {"repository": "o/one", "target": "main", "head_sha": "a" * 40,
               "state": "COMPLETE", "completed": 0, "total": 0, "checks": []}
        two = dict(one, repository="o/two", state="RUNNING")
        error = RuntimeError("not included in output")
        for observations, names in [([one], "one"), ([one, two], "one,two"),
                                    ([error], "one"), ([one, error], "one,two"), ([], "")]:
            for json_mode in (False, True):
                for check_mode in (False, True):
                    with self.subTest(names=names, json=json_mode, check=check_mode):
                        rows = [row for row in observations if isinstance(row, dict)]
                        errors = [{"repository": name, "error": "RuntimeError"}
                                  for name, row in zip(names.split(","), observations)
                                  if isinstance(row, RuntimeError)]
                        argv = ["--repos", names]
                        if json_mode:
                            argv.append("--json")
                        if check_mode:
                            argv.append("--ci-check")
                        out = io.StringIO()
                        with patch.object(ci, "observe", side_effect=observations), patch.object(
                            ci.datetime, "datetime"
                        ) as clock, redirect_stdout(out):
                            clock.now.return_value = observed
                            code = ci.main(argv)
                        clock.now.assert_called_once_with(timezone.utc)
                        self.assertEqual(code, 2 if errors else 1 if check_mode and two in rows else 0)
                        if json_mode:
                            self.assertEqual(json.loads(out.getvalue()), {
                                "schema": "ci-observation/1", "observed_at": observed.isoformat(),
                                "repositories": rows, "errors": errors,
                            })
                        else:
                            lines = out.getvalue().splitlines()
                            self.assertEqual(lines[0], "Observed at: " + observed.isoformat())
                            self.assertEqual(out.getvalue().count("Observed at:"), 1)
                            self.assertEqual(len(lines), 1 + len(rows) + len(errors))
                            for row in rows:
                                self.assertIn(row["repository"] + "  main  " + row["state"]
                                              + "  0/0  aaaaaaaa", lines)
                            for failure in errors:
                                self.assertIn(failure["repository"] + "  UNKNOWN (RuntimeError)", lines)

    def test_ci_check_cli_flag(self):
        for state, expected_code in [
            ("COMPLETE", 0),
            ("NO CHECKS", 0),
            ("FAILED", 1),
            ("RUNNING", 1),
            ("INCOMPLETE", 1),
        ]:
            with self.subTest(state=state):
                mock_obs = {
                    "repository": "o/r",
                    "target": "main",
                    "head_sha": "0" * 40,
                    "state": state,
                    "completed": 0,
                    "total": 0,
                    "checks": [],
                }
                with patch.object(ci, "observe", return_value=mock_obs):
                    out = io.StringIO()
                    with redirect_stdout(out):
                        code = ci.main(["Ironmate", "--ci-check"])
                    self.assertEqual(code, expected_code)

    def test_check_contract_across_repositories_and_output_modes(self):
        def observation(name, state):
            return {"repository": name, "target": "main", "head_sha": "0" * 40,
                    "state": state, "completed": 0, "total": 0, "checks": []}

        for json_mode in (False, True):
            for check_mode in (False, True):
                for states, expected in [
                    (("COMPLETE", "NO CHECKS"), 0),
                    (("COMPLETE", "FAILED"), 1 if check_mode else 0),
                    (("FAILED", "ERROR"), 2),
                    (("ERROR", "COMPLETE"), 2),
                ]:
                    with self.subTest(json=json_mode, check=check_mode, states=states):
                        def observe(name, pr):
                            state = dict(zip(("one", "two"), states))[name]
                            if state == "ERROR":
                                raise RuntimeError("API unavailable")
                            return observation(name, state)

                        argv = ["--repos", "one,two"]
                        if json_mode:
                            argv.append("--json")
                        if check_mode:
                            argv.append("--ci-check")
                        out = io.StringIO()
                        with patch.object(ci, "observe", side_effect=observe), redirect_stdout(out):
                            self.assertEqual(ci.main(argv), expected)
                        if json_mode:
                            payload = json.loads(out.getvalue())
                            self.assertEqual(payload["schema"], "ci-observation/1")
                            self.assertEqual(len(payload["errors"]), states.count("ERROR"))

    def test_plain_check_is_not_an_alias(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            ci.main(["Ironmate", "--check"])
        self.assertEqual(raised.exception.code, 2)

if __name__ == "__main__":
    unittest.main()


PULLS = {"pull_requests": [
    {"number": 5, "title": "stack top", "state": "open", "draft": False, "head_sha": "a" * 40, "head_ref": "feat/top",
     "base_sha": "b" * 40, "base_ref": "feat/base", "updated_at": "2026-11-08T00:00:00Z", "url": "https://github.com/myon-bioinformatics/Demo/pull/5"},
    {"number": 4, "title": "base <b>", "state": "open", "draft": True, "head_sha": "c" * 40, "head_ref": "feat/base",
     "base_sha": "d" * 40, "base_ref": "main", "updated_at": "2026-11-08T00:00:00Z", "url": "https://github.com/myon-bioinformatics/Demo/pull/4"}],
    "truncated": False}
ISSUES = {"issues": [{"number": 9, "title": "an issue", "url": "https://github.com/myon-bioinformatics/Demo/issues/9",
                      "labels": ["bug"], "updated_at": "2026-11-07T00:00:00Z"}]}
CHECKS = {"complete": True, "checks": [{"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "url": None},
                                       {"id": 2, "name": "copilot-pull-request-reviewer", "status": "completed", "conclusion": "failure", "url": None}]}


class AuditTests(unittest.TestCase):
    def snapshot(self, **kwargs):
        def fake_get(path):
            return {"mergeable": "/pulls/5" not in path, "mergeable_state": "clean"}
        with patch.object(ci.ghi, "pull_requests", return_value=PULLS), patch.object(ci.ghi, "issues", return_value=ISSUES), \
                patch.object(ci, "get", side_effect=fake_get), patch.object(ci.ghi, "checks_for_sha", return_value=CHECKS), \
                patch.object(ci, "conventions_repository", return_value=([], False)):
            return ci.audit(["Demo"], **kwargs)

    def test_audit_links_stacked_prs_conflicts_and_ignores_reviewer_checks(self):
        snap = self.snapshot()
        by_id = {r["id"]: r for r in snap["records"]}
        self.assertEqual(sorted(by_id), ["Demo#4", "Demo#5", "Demo#i9"])
        self.assertEqual(by_id["Demo#5"]["depends_on"], ["Demo#4"])
        self.assertTrue(by_id["Demo#5"]["conflict"])
        self.assertEqual(by_id["Demo#4"]["depends_on"], [])
        self.assertEqual(by_id["Demo#5"]["ci"], "COMPLETE", "the reviewer check run does not count as CI")
        self.assertEqual(by_id["Demo#i9"]["ci"], "-")
        self.assertEqual(snap["summary"], {"repositories": 1, "pull_requests": 2, "issues": 1, "ci_failed": 0,
                                           "conflicts": 1, "stacked": 1, "done": 0, "conventions": 0})
        self.assertEqual(snap["ignored_checks"], ["copilot-pull-request-reviewer"])

    def test_fleet_report_is_one_more_view_of_the_same_rows(self):
        fleet = {"schema": "vendor-fleet-report/1", "generated_at": "2026-11-09T00:00:00+00:00", "parent_revision": "a" * 40,
                 "counts": {"success": 0, "failure": 1, "updated": 1, "skipped": 1, "blocked": 0, "unavailable": 0},
                 "targets": {"counts": {"target": 2, "excluded": 1, "insufficient": 0}, "repositories": []},
                 "graph": {"kinds": {"vendor": 2}, "mutual": [{"repositories": ["a", "b"], "kind": "code"}], "cycles": []},
                 "plan": {"steps": [{"id": "vendor:app:vendor.lock.json:tool", "wave": 1, "auto": True, "reasons": ["vendor/tool.py: blob 1→2"],
                                     "argv": ["python", "-S", "<tools>/vendor_sync.py", "update", "--only", "vendor/tool.py"]}], "waves": [["x"]]},
                 "rows": [{"id": "vendor:app:vendor/tool.py<-tool:tool.py", "kind": "vendor", "status": "updated", "reason": "blob 1→2 <b>",
                           "revision": "b" * 40, "checked_at": "2026-11-09T00:00:00+00:00"},
                          {"id": "same", "kind": "vendor", "status": "skipped", "reason": "same content", "revision": None}],
                 "stale": [{"id": "test:app:tests", "status": "success", "revision": "c" * 40, "current_revision": "d" * 40,
                            "checked_at": "2026-11-08T00:00:00+00:00"}],
                 "remaining": [{"severity": "failure", "what": "test:hub:tests", "detail": "1 failures"}],
                 "diff": {"previous_at": "2026-11-08T00:00:00+00:00", "changed": [], "new": ["x"], "gone": []}}
        snap = self.snapshot()
        snap["fleet"] = fleet
        html = ci.render_audit_html(snap)
        self.assertIn('data-ui-view="fleet"', html)
        self.assertIn("🔗 依存・更新", html)
        self.assertIn("vendor:app:vendor.lock.json:tool", html)
        self.assertIn("&lt;b&gt;", html)
        self.assertIn("古い結果", html)
        self.assertNotIn(">same<", html)  # same-content plan rows are counted, not listed
        snap["fleet"] = {"schema": "vendor-fleet-report/1", "status": "unavailable", "error": "OSError"}
        html = ci.render_audit_html(snap)
        self.assertIn("取得できませんでした", html)
        self.assertNotIn("table-fleet-rows", html)
        self.assertNotIn('data-ui-view="fleet"', ci.render_audit_html(self.snapshot()))

    def test_records_file_and_previous_snapshot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            records = Path(directory) / "records.jsonl"
            records.write_text(json.dumps({"id": "Demo#4", "state": "done", "notes": "merged first"}) + "\n", encoding="utf-8")
            previous = {"records": [{"id": "Demo#4", "head_sha": "c" * 40, "ci": "FAILED"}, {"id": "Demo#1", "head_sha": "x", "ci": "COMPLETE"}]}
            snap = self.snapshot(records_path=records, previous=previous)
            by_id = {r["id"]: r for r in snap["records"]}
            self.assertEqual((by_id["Demo#4"]["state"], by_id["Demo#4"]["notes"]), ("done", "merged first"))
            self.assertEqual(snap["summary"]["done"], 1)
            self.assertEqual(snap["changes"], {"new": ["Demo#5", "Demo#i9"], "gone": ["Demo#1"], "changed": ["Demo#4"]})
            written = ci.write_audit_site(snap, Path(directory) / "dist")
            self.assertEqual(written, ["data.json", "index.html", "js/navigation.js", "js/records.js"])
            html = (Path(directory) / "dist" / "index.html").read_text(encoding="utf-8")
            self.assertIn("&lt;b&gt;", html)
            self.assertNotIn("base <b>", html)
            for view in ("queue", "compare", "weekly", "done"):
                self.assertIn(f'data-ui-view="{view}"', html)
            self.assertIn('data-ui-draft="Demo#5:notes"', html)
            self.assertIn('data-ui-records', html)
            self.assertIn("Meiryo", html)
            self.assertIn('src="./js/records.js"', html)
            self.assertNotIn("api.github.com", html, "the page never fetches: the producer did")
            data = json.loads((Path(directory) / "dist" / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "parent-audit/1")

    def test_audit_reports_repository_errors_without_stopping(self):
        with patch.object(ci.ghi, "pull_requests", side_effect=RuntimeError("boom")):
            snap = ci.audit(["Demo"])
        self.assertEqual(snap["records"], [])
        self.assertEqual(snap["errors"][0]["repository"], "Demo")
        self.assertEqual(snap["errors"][0]["error"], "RuntimeError")

    def test_cli_audit_writes_site(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(ci, "audit", return_value={"schema": "parent-audit/1", "generated_at": "t", "owner": ci.OWNER, "repositories": [],
                                                         "ignored_checks": [], "records": [], "summary": {"repositories": 0, "pull_requests": 0, "issues": 0, "ci_failed": 0, "conflicts": 0, "stacked": 0, "done": 0},
                                                         "errors": [], "truncated": [], "changes": {"new": [], "gone": [], "changed": []}}) as fake:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = ci.main(["--audit", "--repos", "Demo", "--out", directory, "--ignore-checks", "x,y"])
            self.assertEqual(code, 0)
            self.assertEqual(fake.call_args.kwargs["ignored_checks"], ("x", "y"))
            self.assertTrue((Path(directory) / "index.html").is_file())
            self.assertEqual(json.loads(out.getvalue())["written"][0], "data.json")


CI_TEXT = """name: Tests
on:
  pull_request:
jobs:
  changes:
    uses: ./.github/workflows/reusable-change-detection.yml
  resolve-aoi:
    runs-on: ubuntu-latest
  vendor-dispatch:
    name: vendor lock dispatch
    runs-on: ubuntu-latest
  failure-identity:
    needs: [resolve-aoi]
    uses: ./.github/workflows/reusable-junit-identity.yml
"""
PAGES_TEXT = """name: Studio Pages
on:
  workflow_run:
    workflows: ["Tests"]
jobs:
  deploy:
    steps:
      - uses: actions/deploy-pages@abc
"""
LABEL_TEXT = """name: CI failure label
on:
  workflow_run:
    workflows:
      - 'Tests'
      - 'Shared Python launcher'
jobs:
  label:
    runs-on: ubuntu-latest
"""


class ConventionTests(unittest.TestCase):
    def test_workflow_facts_without_yaml(self):
        facts = ci.workflow_facts(CI_TEXT)
        self.assertEqual(facts["name"], "Tests")
        self.assertEqual(facts["jobs"], ["changes", "resolve-aoi", "vendor-dispatch", "failure-identity"])
        self.assertEqual(facts["job_names"], {"vendor-dispatch": "vendor lock dispatch"})
        self.assertEqual(ci.workflow_facts(PAGES_TEXT)["workflow_run"], ["Tests"])
        self.assertEqual(facts["events"], ["pull_request"])
        self.assertEqual(ci.workflow_facts("name: X\non: [push, workflow_dispatch]\njobs:\n  a:\n")["events"], ["push", "workflow_dispatch"])
        self.assertEqual(ci.workflow_facts("name: X\non:\n  workflow_dispatch:\n    inputs:\n      x:\n        type: string\njobs:\n  a:\n")["events"], ["workflow_dispatch"])
        self.assertEqual(ci.workflow_facts(LABEL_TEXT)["workflow_run"], ["Tests", "Shared Python launcher"])

    def test_check_conventions_reports_every_rule_once(self):
        paths = [".github/workflows/tests.yml", ".github/workflows/Studio_Pages.yml", ".github/workflows/label.yml",
                 ".github/PULL_REQUEST_TEMPLATE.md", "docs/ANCHOR_MATCHER.md", "docs/good-name.md", "docs/README.md", "README.md"]
        found = ci.check_conventions("Demo", paths, {"tests.yml": CI_TEXT, "Studio_Pages.yml": PAGES_TEXT, "label.yml": LABEL_TEXT})
        by_rule = {}
        for f in found:
            by_rule.setdefault(f["rule"], []).append((f["path"], f["found"], f["expected"]))
        self.assertEqual(by_rule["workflow-role"], [(".github/workflows/Studio_Pages.yml", "Studio_Pages.yml", "pages.yml"),
                                                    (".github/workflows/tests.yml", "tests.yml", "ci.yml")])
        self.assertEqual(by_rule["workflow-file"], [(".github/workflows/Studio_Pages.yml", "Studio_Pages.yml", "lowercase, hyphens, .yml")])
        self.assertEqual(by_rule["workflow-run"], [(".github/workflows/label.yml", "Shared Python launcher", "an existing workflow name")])
        self.assertEqual(by_rule["doc-file"], [("docs/ANCHOR_MATCHER.md", "1 file(s)", "lowercase kebab-case .md")])
        self.assertEqual(by_rule["github-file"], [(".github/PULL_REQUEST_TEMPLATE.md", ".github/PULL_REQUEST_TEMPLATE.md", ".github/pull_request_template.md")])
        self.assertNotIn("job-id", by_rule, "job ids are checked in ci.yml only")
        manual = ci.check_conventions("Demo", [], {"nano-bun-fanout.yml": "name: Nano bun fan-out\non:\n  workflow_dispatch:\n    inputs:\n      n:\n        type: number\njobs:\n  run:\n    runs-on: ubuntu-latest\n"})
        self.assertEqual([(f["rule"], f["found"]) for f in manual], [("dispatch-only", "workflow_dispatch のみ")])
        renamed = ci.check_conventions("Demo", [".github/workflows/ci.yml"], {"ci.yml": CI_TEXT.replace("name: Tests", "name: CI")})
        self.assertEqual([(f["rule"], f["found"], f["expected"]) for f in renamed],
                         [("job-id", "resolve-aoi", "resolve-vendor"), ("job-name", "vendor lock dispatch", "Vendor dispatch")])

    def test_exemptions_hub_locked_lane_and_generated_docs(self):
        hub = ci.check_conventions("myon-bioinformatics", [".github/workflows/python-tests.yml"], {"python-tests.yml": CI_TEXT.replace("name: Tests", "name: Python tests")})
        self.assertEqual(hub, [])
        locked = ci.check_conventions("flutter_navigation_basic", [], {"non-dart.yml": CI_TEXT.replace("name: Tests", "name: Non-Dart checks")})
        self.assertEqual(locked, [])
        generated = ci.check_conventions("Aoi", ["docs/cycles/C001_REPLAY.md"], {})
        self.assertEqual(generated, [])
        self.assertTrue(ci.check_conventions("Other", ["docs/cycles/C001_REPLAY.md"], {}))
        self.assertEqual(ci.check_conventions("Other", ["tests/fixtures/mini_readme.md"], {}), [], "only docs/ is held to the rule")

    def test_conventions_report_and_cli(self):
        with patch.object(ci, "conventions_repository", return_value=([{"repository": "Demo", "rule": "job-id", "path": "p", "found": "deps", "expected": "resolve-vendor"}], False)):
            report = ci.conventions(["Demo"])
            out = io.StringIO()
            with redirect_stdout(out):
                code = ci.main(["--conventions", "--repos", "Demo"])
        self.assertEqual(report["findings"][0]["expected"], "resolve-vendor")
        self.assertEqual(code, 1)
        self.assertIn("1 finding(s) across 1 repositories", out.getvalue())
        self.assertIn("deps -> resolve-vendor", out.getvalue())
        with patch.object(ci, "conventions_repository", return_value=([], False)):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ci.main(["--conventions", "--repos", "Demo"]), 0)

    def test_audit_snapshot_carries_conventions_into_the_page(self):
        def fake_get(path):
            return {"mergeable": True, "mergeable_state": "clean"}
        finding = [{"repository": "Demo", "rule": "job-id", "path": ".github/workflows/ci.yml#deps", "found": "deps", "expected": "resolve-vendor"}]
        with patch.object(ci.ghi, "pull_requests", return_value=PULLS), patch.object(ci.ghi, "issues", return_value=ISSUES), \
                patch.object(ci, "get", side_effect=fake_get), patch.object(ci.ghi, "checks_for_sha", return_value=CHECKS), \
                patch.object(ci, "conventions_repository", return_value=(finding, False)):
            snap = ci.audit(["Demo"])
        self.assertEqual(snap["summary"]["conventions"], 1)
        html = ci.render_audit_html(snap)
        self.assertIn('data-ui-view="rules"', html)
        self.assertIn("resolve-vendor", html)

# Graph and pin contracts, offline and compatible with python -S.
import base64


def node(key, deps=(), **fields):
    return dict(id=key, depends_on=list(deps), **fields)


class DependencyTests(unittest.TestCase):
    def test_provider_consumer_parent_and_waiting(self):
        rows = [node('parent-pin', ['consumer']), node('consumer', ['provider']),
                node('provider', state='done')]
        plan = ci.dependency_plan(rows)
        self.assertEqual(plan['order'], ['provider', 'consumer', 'parent-pin'])
        self.assertEqual(plan['ready'], ['provider'])
        self.assertEqual(plan['waiting']['consumer'], ['provider'])
        rows[-1]['merged'] = True
        self.assertEqual(ci.dependency_plan(rows)['ready'], ['consumer'])

    def test_cycle_and_descendant_are_blocked_but_independent_orders(self):
        plan = ci.dependency_plan([node('a', ['b']), node('b', ['a']),
                                   node('child', ['a']), node('independent')])
        self.assertTrue(plan['cycle'])
        self.assertEqual(plan['blocked'], ['a', 'b', 'child'])
        self.assertEqual(plan['order'], ['independent'])

    def test_missing_is_not_silently_inserted_by_graphlib(self):
        plan = ci.dependency_plan([node('a', ['absent']), node('b', ['a']), node('ok')])
        self.assertEqual(plan['missing'], {'a': ['absent']})
        self.assertEqual(plan['order'], ['ok'])
        self.assertEqual(plan['blocked'], ['a', 'b'])

    def test_empty_self_loop_duplicates_and_bad_edges(self):
        self.assertEqual(ci.dependency_plan([])['order'], [])
        self.assertEqual(ci.dependency_plan([node('x', ['x'])])['blocked'], ['x'])
        for rows in ([node('x'), node('x')], [dict(id='x', depends_on='bad')]):
            with self.assertRaises(ValueError):
                ci.dependency_plan(rows)

    def test_deterministic_diamond_and_duplicate_edges(self):
        rows = [node('d', ['b', 'c', 'b']), node('c', ['a']), node('a'), node('b', ['a'])]
        self.assertEqual(ci.dependency_plan(rows), ci.dependency_plan(list(reversed(rows))))
        self.assertEqual(ci.dependency_plan(rows)['order'], ['a', 'b', 'c', 'd'])

    def test_stacked_and_explicit_cross_repo_edges_survive(self):
        rows = [dict(id='A#1', kind='pr', repository='A', head_ref='provider', base_ref='main'),
                dict(id='A#2', kind='pr', repository='A', head_ref='consumer', base_ref='provider', depends_on=['B#1'])]
        ci.link_dependencies(rows)
        self.assertEqual(rows[1]['depends_on'], ['A#1', 'B#1'])

    def test_pin_match_difference_unknown_and_cache(self):
        entries = [dict(repository=ci.OWNER+'/source', commit=c, ref=c, destination='vendor/x')
                   for c in ('a'*40, 'b'*40, 'main')]
        content = base64.b64encode(json.dumps(dict(schema='vendor-lock/1', files=entries)).encode()).decode()
        def get(path):
            if '/contents/' in path:
                self.assertIn('ref='+'c'*40, path)
                return dict(content=content)
            if path.endswith('/git/ref/heads/main'):
                return dict(object=dict(sha='a'*40))
            if '/git/ref/' in path:
                return dict(object=dict(sha='c'*40))
            return dict(default_branch='trunk', private=False, visibility='public', archived=False)
        with patch.object(ci, 'get', side_effect=get) as mock:
            rows = ci.observe_vendor_pins('consumer')
        self.assertEqual([r['freshness'] for r in rows], ['match', 'different', 'unknown'])
        self.assertEqual({r['compatibility'] for r in rows}, {'not_assessed'})
        self.assertEqual(sum(c.args[0].endswith('/git/ref/heads/main') for c in mock.call_args_list), 1)
        with patch.object(ci, 'get', side_effect=lambda path: get(path) if '/source' not in path else (_ for _ in ()).throw(OSError())):
            self.assertEqual({r['freshness'] for r in ci.observe_vendor_pins('consumer')}, {'unknown'})

    def test_report_escapes_and_legacy_snapshot(self):
        html = ci.render_dependency_report({'dependency_plan': {'order': ['<script>']},
                  'vendor_pins': [dict(repository='<img>', freshness='different')]})
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img>', html)
        self.assertIn('互換性未評価', html)
        self.assertIn('依存順序', ci.render_dependency_report({}))

class IntegrationStatusTests(unittest.TestCase):
    def test_zero_open_prs_keeps_main_checks_and_only_merged_history(self):
        from unittest.mock import patch
        import ci_status as ci
        sha = 'a' * 40
        def get(path):
            if path.endswith('/Demo'):
                return {'private': False, 'visibility': 'public', 'default_branch': 'main'}
            if '/git/ref/' in path:
                return {'object': {'sha': sha}}
            return [{'number': 2, 'title': 'merged', 'html_url': 'https://github.com/o/r/pull/2',
                     'merged_at': '2026-11-09T00:00:00Z', 'merge_commit_sha': sha, 'base': {'ref': 'main'}},
                    {'number': 3, 'merged_at': None}]
        with patch.object(ci, 'get', side_effect=get), patch.object(ci.ghi, 'pages', return_value=[]), patch.object(ci.ghi, 'checks_for_sha', return_value={'complete': True, 'checks': []}):
            row = ci.integration_status('Demo', 0)
        self.assertEqual(row['open_prs'], 0)
        self.assertEqual(row['ci'], 'NO CHECKS')
        self.assertEqual([p['number'] for p in row['merged']], [2])
        self.assertNotIn('depends_on', row)

    def test_moving_main_requires_retry(self):
        from unittest.mock import patch
        import ci_status as ci
        values = [{'private': False, 'visibility': 'public', 'default_branch': 'main'},
                  {'object': {'sha': 'a'*40}}, [], {'object': {'sha': 'b'*40}}]
        with patch.object(ci, 'get', side_effect=values), patch.object(ci.ghi, 'pages', return_value=[]), patch.object(ci.ghi, 'checks_for_sha', return_value={'complete': True, 'checks': []}):
            with self.assertRaises(RuntimeError):
                ci.integration_status('Demo', 0)

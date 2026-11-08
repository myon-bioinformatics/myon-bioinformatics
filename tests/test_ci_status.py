import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch
import ci_status as ci
from pathlib import Path
import hashlib

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
                patch.object(ci, "get", side_effect=fake_get), patch.object(ci.ghi, "checks_for_sha", return_value=CHECKS):
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
                                           "conflicts": 1, "stacked": 1, "done": 0})
        self.assertEqual(snap["ignored_checks"], ["copilot-pull-request-reviewer"])

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

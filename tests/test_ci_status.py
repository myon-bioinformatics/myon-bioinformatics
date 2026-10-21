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

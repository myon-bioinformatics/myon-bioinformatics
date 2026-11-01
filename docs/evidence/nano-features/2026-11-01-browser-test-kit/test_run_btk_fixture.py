"""Stdlib-only orchestration tests; never execute Node or real package workloads."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("btk_fixture", ROOT / "run_btk_fixture.py")
btk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(btk)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="btk-fake-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_private_stage_preserves_source_and_sanitizes_environment(self):
        project = self.root / "installed"
        modules = project / "node_modules"
        for name, version in btk.VERSIONS.items():
            package = modules / name / "package.json"
            package.parent.mkdir(parents=True)
            package.write_text(json.dumps({"version": version}))
        output = self.root / "output"
        output.mkdir()
        with patch.dict(os.environ, {"NODE_OPTIONS": "--require evil", "PYTHONPATH": "evil", "PW_TEST_REPORTER": "evil"}):
            stage, env = btk.stage_inputs(project, output)
        self.assertEqual((stage / "node_modules").resolve(), modules.resolve())
        self.assertEqual(env["NODE_OPTIONS"], "")
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PW_TEST_REPORTER", env)
        self.assertEqual(env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"], "1")
        self.assertEqual((output / "bin/python").resolve(), Path(btk.sys.executable).resolve())
        for filename in btk.SOURCE_HASHES:
            self.assertEqual((stage / filename).read_bytes(), (btk.SOURCE / filename).read_bytes())

    def test_junit_two_original_cases_only(self):
        report = self.root / "junit.xml"
        report.write_text('<testsuites><testsuite>' + ''.join('<testcase name="' + name + '"/>' for name in btk.TITLES) + '</testsuite></testsuites>')
        result = btk.parse_junit(report)
        self.assertEqual((result["passed"], result["failed"], result["skipped"], result["excluded_xlsx"]), (2, 0, 0, 1))
        report.write_text('<testsuite><testcase name="ExcelJS writes"/></testsuite>')
        with self.assertRaises(btk.FixtureError):
            btk.parse_junit(report)

    def test_timeout_kills_owned_process_group(self):
        (self.root / "logs").mkdir()
        class Process:
            pid = 12345
            returncode = -9
            calls = 0
            def wait(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("fake-node", timeout)
                return self.returncode
        runner = btk.Runner(self.root, self.root, {})
        with patch.object(btk.subprocess, "Popen", return_value=Process()) as popen, patch.object(btk.os, "killpg") as kill:
            with self.assertRaisesRegex(btk.FixtureError, "process group killed"):
                runner.run("fake-timeout", ["fake-node"], timeout=1)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            kill.assert_called_with(12345, btk.signal.SIGKILL)
        self.assertTrue(runner.records[0]["timed_out"])

    def test_total_deadline_prevents_launch_and_caps_timeout(self):
        (self.root / "logs").mkdir()
        runner = btk.Runner(self.root, self.root, {})
        runner.deadline = 10.0
        with patch.object(btk.time, "monotonic", return_value=10.0), patch.object(btk.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(btk.FixtureError, "before launch"):
                runner.run("expired", ["fake-node"])
            popen.assert_not_called()
        class Process:
            pid = 12346
            returncode = 0
            timeouts = []
            def wait(self, timeout):
                self.timeouts.append(timeout)
                return 0
        process = Process()
        with patch.object(btk.time, "monotonic", return_value=8.0), patch.object(btk.subprocess, "Popen", return_value=process), patch.object(btk.os, "killpg"):
            runner.run("capped", ["fake-node"], timeout=30)
        self.assertEqual(process.timeouts, [2.0])
        self.assertEqual(runner.records[0]["timeout_seconds"], 2.0)

    def test_case_contracts_retain_intentional_bare_url_difference(self):
        cases = {name: (text, expected) for name, text, expected in btk.cases()}
        self.assertEqual(set(cases), {"mirror", "unicode", "escaped-link", "code", "raw-html", "bare-url"})
        self.assertEqual(cases["bare-url"][1]["links"], ["https://example.org/angle"])
        self.assertIn("https://example.org/bare", cases["bare-url"][0])
        self.assertEqual(len(cases["code"][1]["code"]), 2)


if __name__ == "__main__":
    unittest.main()

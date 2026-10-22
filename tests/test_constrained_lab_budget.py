"""Bounded-output and direct-worker resource regression tests (stdlib only)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from tools import constrained_lab as lab
from tools import constrained_lab_bench as bench


def case(name, code, exit=0):
    return {"id": name, "argv": [sys.executable, "-S", "-c", code],
            "expected_exit": exit}


class OutputBudgetTests(unittest.TestCase):
    def test_exact_output_and_expected_nonzero_exit(self):
        x = case("x", "import sys;sys.stdout.buffer.write(b'abc');sys.stderr.buffer.write(b'xyz');sys.exit(7)", 7)
        row = lab.campaign([x], rounds=1, max_output_bytes=3)["reports"][0]["results"][0]
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["stdout_sha256"], hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(row["stderr_sha256"], hashlib.sha256(b"xyz").hexdigest())
        self.assertEqual((row["stdout_bytes"], row["stderr_bytes"]), (3, 3))

    def test_stdout_limit_kills_worker_before_timeout(self):
        x = case("x", "import os,time;os.write(1,b'a'*4096);time.sleep(4)")
        t = time.monotonic()
        row = lab.campaign([x], rounds=1, timeout=2,
                           max_output_bytes=16)["reports"][0]["results"][0]
        self.assertEqual(row["status"], "output_limit")
        self.assertEqual(row["stdout_bytes"], 16)
        self.assertNotIn("stdout_sha256", row)
        self.assertLess(time.monotonic()-t, 1.7)

    def test_stderr_limit_kills_worker(self):
        x = case("x", "import os,time;os.write(2,b'z'*4096);time.sleep(4)")
        row = lab.campaign([x], rounds=1, timeout=2,
                           max_output_bytes=20)["reports"][0]["results"][0]
        self.assertEqual(row["status"], "output_limit")
        self.assertEqual(row["stderr_bytes"], 20)

    def test_two_pipes_parallel_are_drained_independently(self):
        xs = [case(str(i), "import os,time;os.write(1,b'a'*3000);os.write(2,b'b'*3000);time.sleep(2)")
              for i in range(4)]
        report = lab.campaign(xs, rounds=1, workers=4, timeout=1.5,
                              max_output_bytes=128, keep_going=True)
        self.assertEqual(report["cases_completed"], 4)
        self.assertTrue(all(r["status"] == "output_limit"
                            for r in report["reports"][0]["results"]))

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_descendant_inheriting_pipe_cannot_block_completion(self):
        x = case("x", "import subprocess,sys;subprocess.Popen([sys.executable,'-S','-c','import time;time.sleep(4)']);print('parent exited')")
        start = time.monotonic()
        row = lab.campaign([x], rounds=1, timeout=0.25)["reports"][0]["results"][0]
        self.assertEqual(row["status"], "timeout")
        self.assertLess(time.monotonic() - start, 1.5)

    def test_timeout_and_failure_remain_distinct(self):
        xs = [case("slow", "import time;time.sleep(2)"),
              case("bad", "raise SystemExit(9)")]
        report = lab.campaign(xs, rounds=1, workers=2, timeout=0.1)
        self.assertEqual({r["id"]: r["status"] for r in report["reports"][0]["results"]},
                         {"slow": "timeout", "bad": "fail"})

    def test_invalid_limit_rejected_before_launch(self):
        for value in (0, -1, False, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lab.campaign([case("x", "print('x')")], rounds=1,
                             max_output_bytes=value)

    def test_python_S_cli_reports_output_limit_and_failure_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "input.json"
            f.write_text(json.dumps([case("x", "import os;os.write(1,b'x'*1000)")]))
            result = subprocess.run([sys.executable, "-S", str(Path(lab.__file__).resolve()),
                                     str(f), "--rounds", "1", "--max-output-bytes", "12"],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)["reports"][0]["results"][0]["status"],
                             "output_limit")

    def test_benchmark_correctness_cpu_memory_coverage(self):
        xs = [case(str(i), "import time;print('ok');time.sleep(0.035)") for i in range(4)]
        result = bench.compare(xs, rounds=1, workers=2)
        self.assertTrue(result["equivalent"])
        self.assertEqual([r["workers"] for r in result["runs"]], [1, 2])
        for run in result["runs"]:
            res = run["resources"]
            self.assertIsNone(res["aggregate_host_peak_rss_bytes"])
            if os.name == "posix" and hasattr(os, "wait4"):
                self.assertEqual(res["cpu_seconds_measured_workers"], 4)
                self.assertGreater(res["cpu_seconds_sum_measured"], 0)
            if sys.platform.startswith("linux"):
                self.assertEqual(res["peak_rss_measured_workers"], 4)
                self.assertGreater(res["peak_rss_bytes_sum_measured"], 0)
                self.assertGreater(res["sampled_concurrent_child_rss_peak_bytes"], 0)

    def test_missing_executable_has_no_fabricated_resource_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = lab.campaign([{"id": "bad", "argv": [str(Path(tmp) / "missing")],
                                 "expected_exit": 0}], rounds=1)["reports"][0]["results"][0]
        self.assertEqual(row["status"], "spawn_error")
        self.assertIsNone(row["cpu_seconds"])
        self.assertIsNone(row["peak_rss_bytes"])


if __name__ == "__main__":
    unittest.main()

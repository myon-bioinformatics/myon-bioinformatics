import sys
import json
import unittest
from unittest.mock import patch
from tools import constrained_lab_bench

def case(i, code="print('ok')", expected=0):
    return {"id": str(i), "argv": [sys.executable, "-S", "-c", code],
            "expected_exit": expected}

class ComparisonTests(unittest.TestCase):
    def test_equal_coverage_and_outcomes(self):
        cases = [case(i) for i in range(4)]
        result = constrained_lab_bench.compare(cases, workers=2, rounds=1)
        self.assertTrue(result["equivalent"])
        self.assertEqual([r["campaign"]["cases_completed"] for r in result["runs"]], [4, 4])
        self.assertEqual([r["workers"] for r in result["runs"]], [1, 2])
        self.assertGreater(result["speedup"], 0)
    def test_expected_failure_is_preserved(self):
        cases = [case(0), case(1, "raise SystemExit(7)", 0)]
        result = constrained_lab_bench.compare(cases, workers=2)
        self.assertTrue(result["equivalent"])
        self.assertEqual(result["runs"][0]["campaign"]["rounds_passed"], 0)
        self.assertEqual(result["runs"][1]["campaign"]["rounds_passed"], 0)
    def test_invalid_workers(self):
        with self.assertRaises(ValueError):
            constrained_lab_bench.compare([case(0)], workers=1)

    def compare_outputs(self, left, right, contract=True):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "seen"
            payloads = [json.dumps(v) for v in (left, right)]
            script = ("from pathlib import Path; p=Path(%r); "
                      "i=1 if p.exists() else 0; p.write_text('x'); "
                      "print([%r,%r][i])") % (str(marker), payloads[0], payloads[1])
            row = case(0, script)
            if contract:
                row["stdout_contract"] = "result-measurements/1"
            return constrained_lab_bench.compare([row])

    def test_measurement_changes_do_not_change_result_identity(self):
        left = {"result": {"pass": True, "png_sha256": "abc", "png_bytes": 12},
                "measurements": {"wall_seconds": 0.001, "python_alloc_peak_bytes": 100}}
        right = {"measurements": {"wall_seconds": 0.2, "python_alloc_peak_bytes": 400},
                 "result": {"png_bytes": 12, "png_sha256": "abc", "pass": True}}
        report = self.compare_outputs(left, right)
        self.assertTrue(report["equivalent"])
        rows = [run["campaign"]["reports"][0]["results"][0] for run in report["runs"]]
        self.assertNotEqual(rows[0]["stdout_sha256"], rows[1]["stdout_sha256"])
        self.assertEqual(rows[0]["result_sha256"], rows[1]["result_sha256"])
        self.assertEqual([row["measurements"] for row in rows],
                         [left["measurements"], right["measurements"]])
        # Existing arbitrary commands retain strict whole-stdout comparison.
        self.assertFalse(self.compare_outputs(left, right, contract=False)["equivalent"])

    def test_deterministic_result_changes_are_detected(self):
        baseline = {"result": {"pass": True, "cases": 40, "rounds": 10,
                               "shard_sizes": [4] * 10, "png_sha256": "abc", "png_bytes": 12},
                    "measurements": {"wall_seconds": 0.001}}
        for key, value in (("pass", False), ("cases", 39), ("rounds", 9),
                           ("shard_sizes", [5] * 10), ("png_sha256", "def"), ("png_bytes", 13)):
            with self.subTest(key=key):
                changed = {**baseline, "result": {**baseline["result"], key: value}}
                self.assertFalse(self.compare_outputs(baseline, changed)["equivalent"])

    def test_invalid_envelope_fails_closed(self):
        for output in ([], {}, {"result": {}, "measurements": []},
                       {"result": {}, "measurements": {}, "extra": 1}):
            with self.subTest(output=output):
                report = self.compare_outputs(output, output)
                for run in report["runs"]:
                    self.assertEqual(run["campaign"]["rounds_passed"], 0)
                    self.assertEqual(run["campaign"]["reports"][0]["results"][0]["status"],
                                     "invalid_output")

    def test_unknown_stdout_contract_rejected_before_execution(self):
        row = {**case(0), "stdout_contract": "unknown"}
        with patch("tools.constrained_lab.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "unknown stdout contract"):
                constrained_lab_bench.compare([row])
            run.assert_not_called()

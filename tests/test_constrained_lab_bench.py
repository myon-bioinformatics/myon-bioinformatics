import sys
import unittest
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

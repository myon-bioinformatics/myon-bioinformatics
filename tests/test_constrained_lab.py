import unittest
import sys
from tools import constrained_lab

def case(i, code="print('ok')", expected=0):
    return {"id": str(i), "argv": [sys.executable, "-S", "-c", code], "expected_exit": expected}

class CampaignTests(unittest.TestCase):
    def test_shards_are_disjoint_and_seeded(self):
        cases = [case(i) for i in range(13)]
        a = constrained_lab.plan(cases, 10, 7)
        b = constrained_lab.plan(cases, 10, 7)
        self.assertEqual(a, b)
        self.assertEqual(sorted(c["id"] for shard in a for c in shard),
                         sorted(c["id"] for c in cases))
        self.assertNotEqual(a, constrained_lab.plan(cases, 10, 8))
    def test_normal_and_expected_error(self):
        rows = [case(0), case(1, "raise SystemExit(3)", 3)]
        result = constrained_lab.campaign(rows, rounds=2)
        self.assertEqual(result["rounds_passed"], 2)
        self.assertEqual(result["cases_completed"], 2)
    def test_stop_or_continue(self):
        rows = [case(0), case(1, "raise SystemExit(3)", 0), case(2)]
        stopped = constrained_lab.campaign(rows, rounds=3, seed=7)
        continued = constrained_lab.campaign(rows, rounds=3, seed=7, keep_going=True)
        shards = constrained_lab.plan(rows, 3, 7)
        failing_round = next(i for i, shard in enumerate(shards, 1)
                             if any(c["id"] == "1" for c in shard))
        self.assertEqual(stopped["rounds_completed"], failing_round)
        self.assertEqual(stopped["rounds_passed"], failing_round - 1)
        self.assertEqual(stopped["cases_completed"], failing_round)
        self.assertEqual(stopped["reports"][-1]["results"][0]["id"], "1")
        self.assertEqual(continued["rounds_completed"], 3)
        self.assertEqual(continued["rounds_passed"], 2)
    def test_invalid(self):
        with self.assertRaises(ValueError):
            constrained_lab.campaign([case(0), case(0)])
        with self.assertRaises(ValueError):
            constrained_lab.campaign([case(0)], rounds=0)

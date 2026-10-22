import unittest
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock
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
    def test_rejects_empty_shards_before_execution(self):
        for rows, rounds in (([], 10), ([case(0)], 10)):
            with self.subTest(cases=len(rows), rounds=rounds):
                with self.assertRaises(ValueError):
                    constrained_lab.plan(rows, rounds, 7)
                for keep_going in (False, True):
                    with mock.patch.object(constrained_lab, "execute") as execute:
                        with self.assertRaises(ValueError):
                            constrained_lab.campaign(rows, rounds=rounds,
                                                     keep_going=keep_going)
                        execute.assert_not_called()
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

class CLITests(unittest.TestCase):
    def run_cli(self, script, rows, rounds):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "cases.json"
            manifest.write_text(json.dumps(rows), encoding="utf-8")
            root = Path(__file__).resolve().parents[1]
            return subprocess.run(
                [sys.executable, "-S", str(root / "tools" / script), str(manifest),
                 "--rounds", str(rounds)], capture_output=True, text=True, check=False)

    def test_empty_or_overpartitioned_input_is_not_success_evidence(self):
        for script in ("constrained_lab.py", "constrained_lab_bench.py"):
            for rows in ([], [case(0)]):
                with self.subTest(script=script, cases=len(rows)):
                    result = self.run_cli(script, rows, 10)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("ValueError", result.stderr)

    def test_nonempty_round_outcomes_and_counts(self):
        for script in ("constrained_lab.py", "constrained_lab_bench.py"):
            for exit_code, expected_cli in ((0, 0), (3, 1)):
                with self.subTest(script=script, exit_code=exit_code):
                    result = self.run_cli(script, [case(0, f"raise SystemExit({exit_code})")], 1)
                    self.assertEqual(result.returncode, expected_cli, result.stderr)
                    report = json.loads(result.stdout)
                    campaigns = ([run["campaign"] for run in report["runs"]]
                                 if "runs" in report else [report])
                    for campaign in campaigns:
                        self.assertEqual(campaign["rounds_planned"], 1)
                        self.assertEqual(campaign["rounds_completed"], 1)
                        self.assertEqual(campaign["rounds_passed"], int(exit_code == 0))
                        self.assertEqual(campaign["cases_completed"], 1)
                        self.assertEqual(len(campaign["reports"][0]["results"]), 1)

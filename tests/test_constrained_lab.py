import unittest
import hashlib
import json
import os
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
    def test_injected_seeds_reproduce_across_worker_counts(self):
        rows = [{"id": str(i), "argv": [sys.executable, "-S", "-c",
                "import sys;print(sys.argv[1]+':'+sys.argv[2])", "{seed}", "{round}"],
                 "expected_exit": 0} for i in range(9)]
        def identities(seed, workers):
            report = constrained_lab.campaign(rows, rounds=3, seed=seed, workers=workers)
            self.assertEqual(report["cases_completed"], len(rows))
            self.assertEqual(report["rounds_passed"], 3)
            result = {}
            for part in report["reports"]:
                for row in part["results"]:
                    self.assertEqual(row["status"], "pass")
                    self.assertNotIn(row["id"], result)
                    result[row["id"]] = (part["round"], row["stdout_sha256"])
            return result
        baseline = identities(7, 1)
        self.assertEqual(baseline, identities(7, 4))
        self.assertEqual(baseline, identities(7, 2))
        self.assertNotEqual(baseline, identities(8, 4))
        for case_id, (round_number, output_hash) in baseline.items():
            seed_bytes = hashlib.sha256(f"7:{round_number}:{case_id}".encode()).digest()
            injected = int.from_bytes(seed_bytes[:8], "big")
            expected = f"{injected}:{round_number}{os.linesep}".encode()
            self.assertEqual(output_hash, hashlib.sha256(expected).hexdigest())
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
    def run_cli(self, script, rows, rounds, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "cases.json"
            manifest.write_text(json.dumps(rows), encoding="utf-8")
            root = Path(__file__).resolve().parents[1]
            return subprocess.run(
                [sys.executable, "-S", str(root / "tools" / script), str(manifest),
                 "--rounds", str(rounds), *extra], capture_output=True, text=True, check=False)

    def test_timeout_and_spawn_error_never_succeed(self):
        for script in ("constrained_lab.py", "constrained_lab_bench.py"):
            with tempfile.TemporaryDirectory() as directory:
                missing = str(Path(directory) / "missing-program")
                for row, status in (
                    (case(0, "import time;time.sleep(5)"), "timeout"),
                    ({"id": "missing", "argv": [missing], "expected_exit": 0}, "spawn_error"),
                ):
                    with self.subTest(script=script, status=status):
                        result = self.run_cli(script, [row], 1, "--timeout", "0.05")
                        self.assertEqual(result.returncode, 1, result.stderr)
                        report = json.loads(result.stdout)
                        campaigns = ([run["campaign"] for run in report["runs"]]
                                     if "runs" in report else [report])
                        for campaign in campaigns:
                            self.assertEqual(campaign["rounds_passed"], 0)
                            self.assertEqual(campaign["cases_completed"], 1)
                            self.assertEqual(campaign["reports"][0]["results"][0]["status"], status)

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

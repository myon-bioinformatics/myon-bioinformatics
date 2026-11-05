"""Local experiment only. Run from repository root; pytest needed for parity."""
import importlib
import io
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = 'test_runtime_plan'
CLASS = 'RuntimePlanTests'
METHODS = ['test_examples_match_only_declared_requirements', 'test_version_comparison_is_numeric']
COUNT = 27


def require(condition, message):
    if not condition:
        raise SystemExit(message)


class RecordingResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.ids = []

    def startTest(self, test):
        super().startTest(test)
        self.ids.append(test.id())


def main():
    # Explicit local selection, using public APIs without walking directories.
    with patch.object(sys, "path", [str(ROOT / "tests"), str(ROOT), *sys.path]):
        subject = importlib.import_module(MODULE)
        names = unittest.TestLoader().getTestCaseNames(getattr(subject, CLASS))
        all_ids = {f"{MODULE}.{CLASS}.{name}" for name in names}
        selections = {"all": all_ids, "single": {f"{MODULE}.{CLASS}.{METHODS[0]}"},
                      "multiple": {f"{MODULE}.{CLASS}.{name}" for name in METHODS},
                      "zero": set()}
        report = {"python": platform.python_version(), "platform": platform.platform(),
                  "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "samples": {}, "cli": {},
                  "selection_ids": {key: sorted(value) for key, value in selections.items()}}
        for selection, expected in selections.items():
            samples = {"discover": [], "names": []}
            for iteration in range(21):
                for mode in (("discover", "names") if iteration % 2 == 0 else ("names", "discover")):
                    loader = unittest.TestLoader()
                    start = time.perf_counter_ns()
                    if mode == "discover":
                        if selection != "all":
                            loader.testNamePatterns = (["*." + item for item in
                                (METHODS[:1] if selection == "single" else METHODS)]
                                if expected else ["*.test_no_such_method"])
                        suite = loader.discover(str(ROOT / "tests"), pattern=MODULE + ".py")
                    else:
                        suite = loader.loadTestsFromNames([MODULE] if selection == "all" else sorted(expected))
                    loaded = time.perf_counter_ns()
                    require(not loader.errors, str(loader.errors))
                    require(suite.countTestCases() == len(expected), "count mismatch")
                    result = RecordingResult()
                    suite.run(result)
                    done = time.perf_counter_ns()
                    require(set(result.ids) == expected, "ID mismatch")
                    require(result.testsRun == len(expected) and result.wasSuccessful(), "result mismatch")
                    samples[mode].append({"load_ms": (loaded-start)/1e6, "total_ms": (done-start)/1e6})
            report["samples"][selection] = {mode: {"raw": rows,
                "median_load_ms": statistics.median(row["load_ms"] for row in rows),
                "median_total_ms": statistics.median(row["total_ms"] for row in rows)}
                for mode, rows in samples.items()}
            args = [sys.executable, "-S", "-m", "unittest", "discover", "-s", "tests", "-p", MODULE + ".py", "-v"]
            if selection != "all":
                for name in (METHODS[:1] if selection == "single" else METHODS if expected else ["test_no_such_method"]):
                    args += ["-k", name]
            completed = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=120)
            # Compare full verbose labels using known metadata; no output name parser.
            for name in names:
                label = f"{name} ({MODULE}.{CLASS}.{name}) ... ok"
                require((label in completed.stderr) == (f"{MODULE}.{CLASS}.{name}" in expected), "CLI ID mismatch")
            require(completed.returncode == 0 if expected else completed.returncode in (0, 5), "CLI exit mismatch")
            report["cli"][selection] = {"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}

            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--pytest", selection],
                                       cwd=ROOT, text=True, capture_output=True, timeout=120)
            require(completed.returncode == (0 if expected else 5), "pytest exit mismatch: " + completed.stderr + completed.stdout)
            data = json.loads(completed.stdout)
            require(set(data["ids"]) == expected and set(data["passed"]) == expected, "pytest parity mismatch")
            require(not data["failed"], "pytest failures")
            report["cli"][selection]["pytest"] = data
        loader = unittest.TestLoader()
        invalid = loader.loadTestsFromNames([f"{MODULE}.{CLASS}.test_no_such_method"])
        result = unittest.TestResult()
        invalid.run(result)
        require(bool(loader.errors) and len(result.errors) == 1 and not result.wasSuccessful(), "invalid name accepted")
        report["invalid"] = {"loader_errors": len(loader.errors), "run_errors": len(result.errors)}
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "tests"), str(ROOT)]))
        for tool, args, expected_code in (
            ("unittest", [sys.executable, "-S", "-m", "unittest", f"{MODULE}.{CLASS}.test_no_such_method"], 1),
            ("pytest", [sys.executable, "-m", "pytest", f"tests/{MODULE}.py::{CLASS}::test_no_such_method", "-q"], 4),
        ):
            completed = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=120)
            require(completed.returncode == expected_code, tool + " accepted invalid name")
            report["invalid"][tool] = {"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
        report["ids"] = sorted(all_ids)
        print(json.dumps(report, indent=2))


def pytest_comparison(selection):
    import pytest
    data = {"ids": [], "passed": [], "failed": []}
    class Evidence:
        def pytest_collection_finish(self, session):
            items = session.items
            self.ids = {item.nodeid: f"{item.module.__name__}.{item.cls.__name__}.{item.originalname}" for item in items}
            data["ids"] = list(self.ids.values())
        def pytest_runtest_logreport(self, report):
            if report.failed:
                data["failed"].append(self.ids[report.nodeid])
            elif report.when == "call" and report.passed:
                data["passed"].append(self.ids[report.nodeid])
    args = [f"tests/{MODULE}.py", "-q", "-o", "addopts="]
    if selection != "all":
        args += ["-k", " or ".join(METHODS[:1] if selection == "single" else METHODS if selection == "multiple" else ["test_no_such_method"])]
    from contextlib import redirect_stdout, redirect_stderr
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = pytest.main(args, plugins=[Evidence()])
    print(json.dumps(data))
    raise SystemExit(code)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--pytest":
        pytest_comparison(sys.argv[2])
    else:
        main()

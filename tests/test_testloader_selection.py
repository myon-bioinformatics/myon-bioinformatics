"""Repository-local TestLoader regression; no reusable runner or source cache."""
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = 'test_runtime_plan'
CLASS = 'RuntimePlanTests'
METHODS = ['test_examples_match_only_declared_requirements', 'test_version_comparison_is_numeric']
COUNT = 27


class TestLoaderSelectionTests(unittest.TestCase):
    def test_discovery_and_named_loading(self):
        with patch.object(sys, "path", [str(ROOT / "tests"), *sys.path]):
            subject = importlib.import_module(MODULE)
            expected = {f"{MODULE}.{CLASS}.{name}" for name in
                        unittest.TestLoader().getTestCaseNames(getattr(subject, CLASS))}
            loader = unittest.TestLoader()
            suite = loader.discover(str(ROOT / "tests"), pattern=MODULE + ".py")
            self.assertEqual(loader.errors, [])
            self.assertEqual(suite.countTestCases(), COUNT)
            class RecordingResult(unittest.TestResult):
                def startTest(self, test):
                    super().startTest(test)
                    self.ids.add(test.id())
            for suite in (suite, unittest.TestLoader().loadTestsFromNames([MODULE])):
                result = RecordingResult()
                result.ids = set()
                suite.run(result)
                self.assertEqual(result.ids, expected)
                self.assertEqual(result.testsRun, COUNT)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

    def test_single_and_multiple_patterns_and_names(self):
        with patch.object(sys, "path", [str(ROOT / "tests"), *sys.path]):
            for names in (METHODS[:1], METHODS):
                for mode in ("patterns", "names"):
                    loader = unittest.TestLoader()
                    expected = [f"{MODULE}.{CLASS}.{name}" for name in names]
                    if mode == "patterns":
                        loader.testNamePatterns = ["*." + name for name in names]
                        suite = loader.discover(str(ROOT / "tests"), pattern=MODULE + ".py")
                    else:
                        suite = loader.loadTestsFromNames(expected)
                    self.assertEqual(loader.errors, [])
                    self.assertEqual(suite.countTestCases(), len(names))
                    result = unittest.TestResult()
                    with patch.object(result, "startTest", wraps=result.startTest) as started:
                        suite.run(result)
                    self.assertEqual({call.args[0].id() for call in started.call_args_list}, set(expected))
                    self.assertEqual(result.testsRun, len(names))
                    self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

    def test_zero_requires_explicit_count_guard(self):
        with patch.object(sys, "path", list(sys.path)):
            loader = unittest.TestLoader()
            loader.testNamePatterns = ["*.test_no_such_method"]
            suite = loader.discover(str(ROOT / "tests"), pattern=MODULE + ".py")
            self.assertEqual(loader.errors, [])
            self.assertEqual(suite.countTestCases(), 0)
            with self.assertRaisesRegex(SystemExit, "No tests selected"):
                if suite.countTestCases() == 0:
                    raise SystemExit("No tests selected")
            result = unittest.TestResult()
            suite.run(result)
            self.assertTrue(result.wasSuccessful())
            self.assertEqual(result.testsRun, 0)

    def test_invalid_name_records_error(self):
        with patch.object(sys, "path", [str(ROOT / "tests"), *sys.path]):
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromNames([f"{MODULE}.{CLASS}.test_no_such_method"])
            self.assertTrue(loader.errors)
            result = unittest.TestResult()
            suite.run(result)
            self.assertFalse(result.wasSuccessful())
            self.assertEqual(len(result.errors), 1)
            # Error state belongs to this loader; a fresh loader has no old errors.
            fresh = unittest.TestLoader()
            valid = fresh.loadTestsFromNames([f"{MODULE}.{CLASS}.{METHODS[0]}"])
            self.assertEqual(fresh.errors, [])
            self.assertEqual(valid.countTestCases(), 1)

    def test_repeated_runs_rebuild_consumed_suites(self):
        with patch.object(sys, "path", [str(ROOT / "tests"), *sys.path]):
            for _ in range(3):
                loader = unittest.TestLoader()
                suite = loader.loadTestsFromNames([MODULE])
                self.assertEqual(loader.errors, [])
                self.assertEqual(suite.countTestCases(), COUNT)
                result = unittest.TestResult()
                suite.run(result)
                self.assertEqual(result.testsRun, COUNT)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

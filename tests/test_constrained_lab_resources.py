import sys
import tempfile
import unittest
from pathlib import Path
from tools import constrained_lab_resources as resources

class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "memory.current").write_text("123\n")
        (self.root / "memory.peak").write_text("456\n")
        (self.root / "memory.max").write_text("134217728\n")
        (self.root / "cpu.stat").write_text("usage_usec 1000\nthrottled_usec 10\n")
    def test_cgroup_parse(self):
        result = resources.read_cgroup(self.root)
        self.assertEqual(result["memory_max"], 134217728)
        self.assertEqual(result["cpu_usage_usec"], 1000)
        self.assertEqual(result["memory_peak"], 456)
    def test_max_is_unbounded(self):
        (self.root / "memory.max").write_text("max\n")
        self.assertIsNone(resources.read_cgroup(self.root)["memory_max"])
    def test_probe_expected_exit(self):
        result = resources.probe([sys.executable, "-S", "-c", "print(1)"], root=self.root)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["measurement_scope"], "entire-cgroup-not-per-process")
    def test_probe_failure_and_timeout(self):
        failure = resources.probe([sys.executable, "-S", "-c", "raise SystemExit(7)"], root=self.root)
        self.assertEqual(failure["returncode"], 7)
        timeout = resources.probe([sys.executable, "-S", "-c", "import time;time.sleep(1)"],
                                  root=self.root, timeout=0.05)
        self.assertTrue(timeout["timeout"])
    def test_invalid(self):
        with self.assertRaises(ValueError):
            resources.probe([], root=self.root)

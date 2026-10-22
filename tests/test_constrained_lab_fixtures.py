import hashlib
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from tools import constrained_lab_fixtures as fixtures

def case(text, expected=0):
    return {"name": "fixture", "argv": [sys.executable, "-S", "-c", "print(%r)" % text],
            "expected_exit": expected, "sha256": hashlib.sha256((text+"\\n").replace("\\n", "\n").encode()).hexdigest()}

class FixtureTests(unittest.TestCase):
    def test_verified_output(self):
        report = fixtures.run([case("ok")], ".")
        self.assertEqual((report["passed"], report["total"]), (1, 1))
    def test_detect_mismatch(self):
        item = case("ok")
        item["sha256"] = "0"*64
        self.assertFalse(fixtures.run([item], ".")["results"][0]["pass"])
    def test_wrong_exit(self):
        self.assertFalse(fixtures.run([case("ok", expected=4)], ".")["results"][0]["pass"])
    def test_invalid_manifest(self):
        with self.assertRaises(ValueError):
            fixtures.run({"not": "list"}, ".")
    def test_empty_manifest_is_rejected_before_execution(self):
        with mock.patch.object(fixtures, "compare_fixture") as execute:
            with self.assertRaisesRegex(ValueError, "nonempty"):
                fixtures.run([], ".")
            execute.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "empty.json"
            manifest.write_text("[]", encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "tools/constrained_lab_fixtures.py"
            result = subprocess.run(
                [sys.executable, "-S", str(script), str(manifest)],
                capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
    def test_invalid_digest(self):
        item = case("ok")
        item["sha256"] = "x"*64
        with self.assertRaises(ValueError):
            fixtures.run([item], ".")

    def test_staged_source_hash_verified_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "worker.py"
            source.write_text("print('hello')\\n".replace("\\n", "\n"), encoding="utf-8")
            spec = {"name": "staged", "argv": [sys.executable, "-S", "worker.py"],
                    "expected_exit": 0, "sha256": hashlib.sha256(b"hello\n").hexdigest(),
                    "sources": {"worker.py": hashlib.sha256(source.read_bytes()).hexdigest()}}
            self.assertTrue(fixtures.run([spec], directory)["results"][0]["pass"])
            source.write_text("print('changed')\\n".replace("\\n", "\n"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source SHA-256 mismatch"):
                fixtures.run([spec], directory)

    def test_source_traversal_rejected(self):
        spec = case("ok")
        spec["sources"] = {"../outside.py": "0"*64}
        with self.assertRaisesRegex(ValueError, "source path"):
            fixtures.run([spec], ".")

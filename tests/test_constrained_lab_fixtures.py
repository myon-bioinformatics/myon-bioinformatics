import hashlib
import sys
import tempfile
import unittest
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
    def test_invalid_digest(self):
        item = case("ok")
        item["sha256"] = "x"*64
        with self.assertRaises(ValueError):
            fixtures.run([item], ".")

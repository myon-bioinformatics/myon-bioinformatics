"""Real Lean checks; optional locally, mandatory in the formal CI lane."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lean_verify import STANDARD_AXIOMS, verify


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lean"


class LeanVerifyIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lean = shutil.which("lean")
        if cls.lean is None:
            message = "Lean is not installed; formal verification is unverified"
            if os.environ.get("LEAN_VERIFY_REQUIRE") == "1":
                raise RuntimeError(message + " (LEAN_VERIFY_REQUIRE=1)")
            raise unittest.SkipTest(message)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        # Elan selects by cwd; CI intentionally has no default toolchain.
        shutil.copyfile(FIXTURES / "lean-toolchain", self.project / "lean-toolchain")

    def check_source(self, source, theorem):
        proof = self.project / "Proof.lean"
        proof.write_text(source, encoding="utf-8")
        return verify("Proof.lean", cwd=self.project, theorems=[theorem], timeout=30)

    def assert_passed(self, result, theorem):
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["returncode"], 0, result)
        self.assertEqual(result["axiom_audit"], "passed", result)
        self.assertTrue(set(result["axioms"][theorem]).issubset(STANDARD_AXIOMS), result)
        self.assertIn("Lean", result["lean_version"])

    def test_valid_proof_reports_audited_snapshot(self):
        theorem = "VerificationFixture.identity"
        result = verify("Valid.lean", cwd=FIXTURES, theorems=[theorem], timeout=30)
        self.assert_passed(result, theorem)
        self.assertEqual(result["axioms"][theorem], [])
        self.assertEqual(result["sha256"], hashlib.sha256((FIXTURES / "Valid.lean").read_bytes()).hexdigest())

    def test_false_statement_fails(self):
        result = self.check_source("theorem wrong : (1 : Nat) = 2 := by omega\n", "wrong")
        self.assertEqual(result["status"], "failed", result)
        self.assertNotEqual(result["returncode"], 0, result)
        self.assertEqual(result["reason"], "proof_failed", result)

    def test_direct_sorry_fails(self):
        result = self.check_source("theorem incomplete : False := by sorry\n", "incomplete")
        self.assertEqual(result["status"], "failed", result)
        self.assertNotEqual(result["returncode"], 0, result)
        self.assertTrue(any(message.get("kind") == "hasSorry" for message in result["diagnostics"]))

    def test_disabling_warning_errors_does_not_pass(self):
        result = self.check_source(
            "set_option warningAsError false\ntheorem incomplete : False := by sorry\n",
            "incomplete",
        )
        self.assertEqual(result["returncode"], 0, result)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["reason"], "proof_failed", result)
        self.assertTrue(any(message["severity"] == "warning" for message in result["diagnostics"]))

    def test_custom_axiom_dependency_fails(self):
        result = self.check_source(
            "axiom assumed : False\ntheorem suspicious : False := assumed\n",
            "suspicious",
        )
        self.assertEqual(result["returncode"], 0, result)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["reason"], "axiom_audit_failed", result)
        self.assertEqual(result["unexpected_axioms"], {"suspicious": ["assumed"]})

    def test_missing_named_theorem_fails(self):
        result = self.check_source("theorem present : True := True.intro\n", "absent")
        self.assertEqual(result["status"], "failed", result)
        self.assertNotEqual(result["returncode"], 0, result)

    def test_imported_sorry_fails_without_local_warning(self):
        dependency = self.project / "Broken.lean"
        dependency.write_text("theorem inherited_hole : False := by sorry\n", encoding="utf-8")
        # Deliberately build an admitted dependency to test transitive auditing.
        built = subprocess.run(
            [self.lean, "-o", "Broken.olean", "Broken.lean"],
            cwd=self.project, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        search_path = os.pathsep.join(filter(None, (str(self.project), os.environ.get("LEAN_PATH", ""))))
        with patch.dict(os.environ, {"LEAN_PATH": search_path}):
            result = self.check_source(
                "import Broken\ntheorem inherited : False := inherited_hole\n",
                "inherited",
            )
        self.assertEqual(result["returncode"], 0, result)
        self.assertFalse(any(message["severity"] in {"warning", "error"} for message in result["diagnostics"]))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["reason"], "axiom_audit_failed", result)
        self.assertEqual(result["unexpected_axioms"], {"inherited": ["sorryAx"]})

    def test_lake_project_checks_the_same_proof_offline(self):
        if shutil.which("lake") is None:
            if os.environ.get("LEAN_VERIFY_REQUIRE") == "1":
                self.fail("Lake is not installed (LEAN_VERIFY_REQUIRE=1)")
            self.skipTest("Lake is not installed; Lake integration is unverified")
        (self.project / "lakefile.toml").write_text(
            'name = "verification_fixture"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        shutil.copyfile(FIXTURES / "Valid.lean", self.project / "Valid.lean")
        # No dependencies or lake update: use the already installed toolchain.
        theorem = "VerificationFixture.identity"
        result = verify("Valid.lean", cwd=self.project, lake=True, theorems=[theorem], timeout=30)
        self.assert_passed(result, theorem)
        self.assertEqual(result["command"][1:3], ["env", "lean"])
        self.assertEqual(result["cwd"], str(self.project.resolve()))


if __name__ == "__main__":
    unittest.main()

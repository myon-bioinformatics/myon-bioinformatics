"""Runner behavior without requiring Lean in the ordinary test environment."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import lean_verify


def diagnostic(data, severity="information"):
    return json.dumps({"severity": severity, "data": data}) + "\n"


class LeanVerifyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.proof = self.root / "Proof with spaces.lean"
        self.proof.write_text("theorem example_proof : True := trivial\n", encoding="utf-8")

    def run_mock(self, *, output="", returncode=0, **kwargs):
        completed = subprocess.CompletedProcess([], returncode, output, "")
        version = subprocess.CompletedProcess([], 0, "Lean 4.test\n", "")
        with patch("lean_verify.shutil.which", return_value="/tool dir/lean"), \
                patch("lean_verify._run", side_effect=[version, completed]) as run:
            result = lean_verify.verify(self.proof, **kwargs)
        return result, run

    def test_missing_lean_is_unverified(self):
        with patch("lean_verify.shutil.which", return_value=None), patch("lean_verify._run") as run:
            result = lean_verify.verify(self.proof)
        self.assertEqual((result["status"], result["reason"]), ("unverified", "tool_missing"))
        self.assertEqual(result["sha256"], hashlib.sha256(self.proof.read_bytes()).hexdigest())
        run.assert_not_called()

    def test_missing_proof_fails_even_without_lean(self):
        result = lean_verify.verify(self.root / "missing.lean")
        self.assertEqual((result["status"], result["reason"]), ("failed", "proof_not_found"))

    def test_relative_path_is_resolved_from_project(self):
        completed = subprocess.CompletedProcess([], 0, "Lean 4.test", "")
        with patch("lean_verify.shutil.which", return_value="lean"), \
                patch("lean_verify._run", return_value=completed) as run:
            result = lean_verify.verify(self.proof.name, cwd=self.root)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["path"], str(self.proof))
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)

    def test_lake_uses_argument_list_and_source_snapshot(self):
        original = self.proof.read_text(encoding="utf-8")
        result, run = self.run_mock(lake=True)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["axiom_audit"], "not_requested")
        self.assertEqual(result["lean_version"], "Lean 4.test")
        self.assertEqual(run.call_args.args[0], ["/tool dir/lean", "env", "lean", "--json",
                                               "-DwarningAsError=true", "--stdin", str(self.proof)])
        self.assertEqual(run.call_args.kwargs["source"], original)
        self.assertEqual(self.proof.read_text(encoding="utf-8"), original)

    def test_nonzero_exit_fails(self):
        result, _ = self.run_mock(returncode=1)
        self.assertEqual((result["status"], result["reason"]), ("failed", "proof_failed"))

    def test_warning_cannot_be_hidden_by_zero_exit(self):
        for severity in ("warning", "error"):
            with self.subTest(severity=severity):
                result, _ = self.run_mock(output=diagnostic("uses sorry", severity))
                self.assertEqual(result["status"], "failed")

    def test_standard_axioms_and_no_axioms_pass(self):
        output = diagnostic("'Check.one' depends on axioms: [propext, Quot.sound]")
        output += diagnostic("'Check.two' does not depend on any axioms") * 2
        result, run = self.run_mock(output=output, theorems=["Check.one", "Check.two"])
        self.assertEqual((result["status"], result["axiom_audit"]), ("passed", "passed"))
        self.assertEqual(result["axioms"], {"Check.one": ["Quot.sound", "propext"], "Check.two": []})
        self.assertIn("#print axioms Check.one\n#print axioms Check.two\n", run.call_args.kwargs["source"])

    def test_primed_theorem_name(self):
        result, _ = self.run_mock(output=diagnostic("'Check.identity'' does not depend on any axioms"),
                                  theorems=["Check.identity'"])
        self.assertEqual(result["axiom_audit"], "passed")

    def test_missing_or_unparseable_axiom_report_fails_closed(self):
        for output in ("", "not JSON\n", diagnostic("unknown future output format")):
            with self.subTest(output=output):
                result, _ = self.run_mock(output=output, theorems=["Check.required"])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["missing_theorems"], ["Check.required"])

    def test_custom_and_imported_sorry_axioms_are_rejected(self):
        for axiom in ("sorryAx", "Trusted.assumption"):
            with self.subTest(axiom=axiom):
                result, _ = self.run_mock(
                    output=diagnostic(f"'Check.required' depends on axioms: [{axiom}]"),
                    theorems=["Check.required"],
                )
                self.assertEqual(result["reason"], "axiom_audit_failed")
                self.assertEqual(result["unexpected_axioms"], {"Check.required": [axiom]})

    def test_unavailable_toolchain_is_not_a_proof_result(self):
        with patch("lean_verify.shutil.which", return_value="lean"), \
                patch("lean_verify._run", return_value=subprocess.CompletedProcess([], 1, "", "missing toolchain")):
            result = lean_verify.verify(self.proof)
        self.assertEqual((result["status"], result["reason"]), ("unverified", "toolchain_unavailable"))

    def test_timeout_retains_partial_output(self):
        with patch("lean_verify.shutil.which", return_value="lean"), \
                patch("lean_verify._run", side_effect=subprocess.TimeoutExpired("lean", 1, output=b"partial", stderr=b"details")):
            result = lean_verify.verify(self.proof, timeout=1)
        self.assertEqual((result["status"], result["reason"]), ("failed", "timeout"))
        self.assertEqual((result["stdout"], result["stderr"]), ("partial", "details"))

    def test_execution_error_is_not_passed(self):
        with patch("lean_verify.shutil.which", return_value="lean"), \
                patch("lean_verify._run", side_effect=PermissionError("not executable")):
            result = lean_verify.verify(self.proof)
        self.assertEqual((result["status"], result["reason"]), ("failed", "execution_error"))

    def test_invalid_timeout_and_theorem_inputs_do_not_launch(self):
        cases = [{"timeout": value} for value in (0, -1, float("nan"), float("inf"))]
        cases += [{"theorems": ["Valid\naxiom false : False"]}, {"cwd": self.root / "missing"}]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), patch("lean_verify._run") as run:
                self.assertEqual(lean_verify.verify(self.proof, **kwargs)["status"], "failed")
                run.assert_not_called()

    def test_cli_missing_lean_policy_and_saved_json(self):
        for required in (False, True):
            report = self.root / "reports" / "lean.json"
            stdout = io.StringIO()
            argv = [str(self.proof), "--json-output", str(report)] + (["--required"] if required else [])
            with patch("lean_verify.shutil.which", return_value=None), redirect_stdout(stdout):
                code = lean_verify.main(argv)
            data = json.loads(stdout.getvalue())
            self.assertEqual(code, int(required))
            self.assertEqual(data["status"], "unverified")
            self.assertEqual(data["required"], required)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), data)

    def test_cli_mixed_results_and_report_write_failure_fail(self):
        result = {"status": "passed"}
        with patch("lean_verify.verify", side_effect=[result, {"status": "failed"}]), redirect_stdout(io.StringIO()):
            self.assertEqual(lean_verify.main([str(self.proof), "missing.lean"]), 1)
        with patch("lean_verify.verify", return_value=result), \
                patch("lean_verify._write_report", side_effect=OSError("disk full")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(lean_verify.main([str(self.proof), "--json-output", "report.json"]), 1)

    def test_cli_rejects_ambiguous_theorem_targets(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            lean_verify.main([str(self.proof), str(self.proof), "--theorem", "Check.required"])
        self.assertEqual(exc.exception.code, 2)

    def test_real_subprocess_timeout_is_bounded(self):
        command = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(10)"]
        with self.assertRaises(subprocess.TimeoutExpired) as exc:
            lean_verify._run(command, cwd=self.root, timeout=0.5)
        self.assertIn("started", exc.exception.stdout)


if __name__ == "__main__":
    unittest.main()

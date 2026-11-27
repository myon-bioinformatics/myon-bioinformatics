"""Execute the reusable Lean lane's input validation and subprocess plumbing."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-lean-verify.yml"


class LeanWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.workspace = Path(self.scratch.name) / "workspace"
        self.project = self.workspace / "consumer" / "proof space"
        self.project.mkdir(parents=True)
        (self.project / "Valid.lean").write_text("-- fixture\n", encoding="utf-8")
        (self.project / "lean-toolchain").write_text("leanprover/lean4:v4.31.0\n", encoding="utf-8")
        self.temp = Path(self.scratch.name) / "runner"
        self.env = dict(os.environ, GITHUB_WORKSPACE=str(self.workspace), RUNNER_TEMP=str(self.temp),
                        LEAN_PROOF="Valid.lean", LEAN_THEOREMS="Example.first\nExample.second'\n",
                        LEAN_PROJECT_DIRECTORY="proof space", LEAN_USE_LAKE="false",
                        LEAN_TIMEOUT_SECONDS="120")
        self.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.steps = self.workflow["jobs"]["verify"]["steps"]
        self.assertEqual(self.run_python_step("Initialize verification report").returncode, 0)

    def step(self, name):
        return next(step for step in self.steps if step.get("name") == name)

    def run_python_step(self, name, **env):
        script = self.step(name)["run"]
        source = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        return subprocess.run([sys.executable, "-S", "-c", source], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=20)

    def validate(self, **env):
        return self.run_python_step("Validate proof inputs and pinned toolchain", **env)

    def test_validation_preserves_space_paths_and_theorem_names(self):
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((self.temp / "lean-verification/inputs.json").read_text())
        self.assertEqual(Path(config["project"]), self.project.resolve())
        self.assertEqual(config["theorems"], ["Example.first", "Example.second'"])
        self.assertFalse(config["lake"])

    def test_invalid_inputs_fail_before_installation(self):
        for environment in [
            {"LEAN_PROJECT_DIRECTORY": str(self.project)},
            {"LEAN_PROJECT_DIRECTORY": ".."},
            {"LEAN_PROOF": str(self.project / "Valid.lean")},
            {"LEAN_PROOF": "../outside.lean"},
            {"LEAN_THEOREMS": ""},
            {"LEAN_THEOREMS": "Example.good\n#eval IO.println 1"},
            {"LEAN_THEOREMS": "Example.good; echo unsafe"},
            {"LEAN_TIMEOUT_SECONDS": "nan"},
            {"LEAN_TIMEOUT_SECONDS": "0"},
            {"LEAN_USE_LAKE": "yes"},
        ]:
            with self.subTest(environment=environment):
                self.assertNotEqual(self.validate(**environment).returncode, 0)
        names = [step.get("name") for step in self.steps]
        self.assertLess(names.index("Validate proof inputs and pinned toolchain"),
                        names.index("Install pinned Elan"))

    def test_unpinned_toolchain_is_rejected(self):
        for value in ["stable", "leanprover/lean4:nightly", "leanprover/lean4:v4.31.0-rc1"]:
            with self.subTest(toolchain=value):
                (self.project / "lean-toolchain").write_text(value, encoding="utf-8")
                self.assertNotEqual(self.validate().returncode, 0)

    def test_symlink_outside_project_is_rejected(self):
        outside = self.workspace / "outside.lean"
        outside.write_text("-- outside\n", encoding="utf-8")
        link = self.project / "Escape.lean"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"symlink unavailable: {error}")
        self.assertNotEqual(self.validate(LEAN_PROOF="Escape.lean").returncode, 0)

    def test_setup_failure_leaves_explicit_unverified_report(self):
        report = json.loads((self.temp / "lean-verification/report.json").read_text())
        self.assertEqual(report["status"], "unverified")
        self.assertTrue(report["required"])
        self.assertEqual(report["results"], [])
        upload = self.step("Preserve verification report")
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertFalse(any(step.get("continue-on-error") for step in self.steps))

    def test_wrapper_arguments_and_failure_exit_survive_subprocess_boundary(self):
        self.assertEqual(self.validate(LEAN_USE_LAKE="true").returncode, 0)
        verifier = self.workspace / ".lean-verifier"
        verifier.mkdir()
        (verifier / "lean_verify.py").write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            "Path(os.environ['RUNNER_TEMP'], 'invocation.json').write_text("
            "json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            "sys.exit(7)\n", encoding="utf-8")
        result = self.run_python_step("Verify selected theorems")
        self.assertEqual(result.returncode, 7, result.stderr)
        invocation = json.loads((self.temp / "invocation.json").read_text())
        arguments = invocation["argv"]
        self.assertEqual(invocation["cwd"], str(self.project.resolve()))
        self.assertIn("--required", arguments)
        self.assertIn("--lake", arguments)
        self.assertEqual(arguments.count("--theorem"), 2)
        self.assertIn("Example.second'", arguments)
        self.assertEqual(arguments[-1], str((self.project / "Valid.lean").resolve()))
        report = arguments[arguments.index("--json-output") + 1]
        self.assertEqual(Path(report), self.temp / "lean-verification/report.json")

    def test_integration_tests_use_project_cwd_shared_imports_and_required_mode(self):
        self.assertEqual(self.validate().returncode, 0)
        verifier = self.workspace / ".lean-verifier"
        tests = verifier / "tests"
        tests.mkdir(parents=True)
        (verifier / "shared_fixture.py").write_text("VALUE = 42\n", encoding="utf-8")
        (tests / "test_lean_verify_integration.py").write_text(
            "import os, unittest\nfrom pathlib import Path\nimport shared_fixture\n"
            "class Probe(unittest.TestCase):\n"
            "    def test_environment(self):\n"
            "        self.assertEqual(os.environ['LEAN_VERIFY_REQUIRE'], '1')\n"
            "        self.assertTrue(Path('lean-toolchain').is_file())\n"
            "        self.assertEqual(shared_fixture.VALUE, 42)\n", encoding="utf-8")
        result = self.run_python_step("Exercise verifier with actual Lean", LEAN_VERIFY_REQUIRE="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)

    def test_workflow_source_is_pinned_and_parent_runs_actual_lean(self):
        checkout = self.step("Checkout verifier at reusable workflow SHA")["with"]
        self.assertEqual(checkout["repository"], "${{ job.workflow_repository }}")
        self.assertEqual(checkout["ref"], "${{ job.workflow_sha }}")
        install = self.step("Install pinned Elan")["run"]
        self.assertIn("releases/download/v4.4.0/", install)
        self.assertIn("0e8250aa2be86c7af929ade68db617af9edc8b9ccf6eaf5ece228250d0d032e2", install)
        self.assertLess(install.index("sha256sum --check --status"), install.index("tar -xzf"))
        caller = yaml.safe_load((ROOT / ".github/workflows/lean-verification.yml").read_text())
        call = caller["jobs"]["verify"]
        self.assertEqual(call["uses"], "./.github/workflows/reusable-lean-verify.yml")
        self.assertTrue(call["with"]["verifier-self-test"])
        self.assertEqual(call["with"]["theorems"], "VerificationFixture.identity")
        if shutil.which("bash"):
            for step in self.steps:
                if "run" in step:
                    result = subprocess.run(["bash", "-n"], input=step["run"], text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

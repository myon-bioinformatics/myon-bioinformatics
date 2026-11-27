import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess, TimeoutExpired
from lean_verify import verify

class LeanVerifyTest(unittest.TestCase):
    def test_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Proof.lean"
            path.write_text("theorem example_proof : True := trivial", encoding="utf-8")
            with patch("lean_verify.shutil.which", return_value=None):
                self.assertEqual(verify(path)["status"], "unverified")
            with patch("lean_verify.shutil.which", return_value="/fake/lean"):
                for rc, status in [(0, "passed"), (1, "failed")]:
                    with patch("lean_verify.subprocess.run", return_value=CompletedProcess([], rc, "", "")) as run:
                        self.assertEqual(verify(path, lake=True)["status"], status)
                        self.assertIn("-DwarningAsError=true", run.call_args.args[0])
                with patch("lean_verify.subprocess.run", side_effect=TimeoutExpired("lean", 1)):
                    self.assertEqual(verify(path)["status"], "failed")
            self.assertEqual(verify(path.with_suffix(".missing"))["status"], "failed")

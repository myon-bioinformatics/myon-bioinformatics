"""pytest_brief: requirement parsing, missing-only install, and the kept summary lines."""
import importlib.util
import subprocess
import sys
import json
import os
import tempfile
import unittest
import venv
import zipfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pytest_brief.py"
spec = importlib.util.spec_from_file_location("pytest_brief", SCRIPT)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


class ResolverRegressionTests(unittest.TestCase):
    """Exercise the real target pip offline; no package index or PEP parser mocks."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        venv.EnvBuilder(with_pip=True).create(cls.root / "venv")
        cls.python = str(cls.root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        cls.wheels = cls.root / "wheels"
        cls.wheels.mkdir()
        for name, version, extra in [
            ("brief_base", "1.0", False), ("brief_base", "2.0", True),
            ("brief_extra", "1.0", False),
        ]:
            info = f"{name}-{version}.dist-info"
            metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            if extra:
                metadata += 'Provides-Extra: feature\nRequires-Dist: brief-extra>=1; extra == "feature"\n'
            with zipfile.ZipFile(cls.wheels / f"{name}-{version}-py3-none-any.whl", "w") as archive:
                archive.writestr(info + "/METADATA", metadata)
                archive.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr(info + "/RECORD", "")
        cls.pip = [cls.python, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
        subprocess.run(cls.pip + ["--no-index", "--find-links", str(cls.wheels), "brief-base==1.0"],
                       capture_output=True, text=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_versions_markers_extras_in_separate_interpreter(self):
        req = self.root / "requirements.txt"
        included = self.root / "included.txt"
        constraint = self.root / "constraints.txt"
        included.write_text('brief-base[feature]>=2\nabsent-package; python_version < "0"\n', encoding="utf-8")
        constraint.write_text("brief-base==2.0\n", encoding="utf-8")
        req.write_text(f'--no-index\n--find-links {self.wheels}\n-r included.txt\n-c constraints.txt\n', encoding="utf-8")
        need = pb.resolve(self.pip, ["-r", str(req)])
        self.assertEqual(set(need), {"brief_base", "brief_extra"})
        # A dry run must leave the installed 1.0 alone.
        version = subprocess.run([self.python, "-c", "from importlib.metadata import version; print(version('brief-base'))"],
                                 capture_output=True, text=True, check=True)
        self.assertEqual(version.stdout.strip(), "1.0")
        base_req = self.root / "base.txt"
        base_req.write_text(f"--no-index\n--find-links {self.wheels}\nbrief-base>=2\n", encoding="utf-8")
        subprocess.run(self.pip + ["-r", str(base_req)], capture_output=True, text=True, check=True)
        # Base distribution present at the right version does not satisfy an extra.
        self.assertEqual(pb.resolve(self.pip, ["-r", str(req)]), ["brief_extra"])
        subprocess.run(self.pip + ["-r", str(req)], capture_output=True, text=True, check=True)
        self.assertEqual(pb.resolve(self.pip, ["-r", str(req)]), [])
        self.assertFalse(pb.externally_managed(self.python))

    def test_real_target_managed_marker_and_venv_exemption(self):
        site = self.root / "probe-site"
        site.mkdir()
        (site / "EXTERNALLY-MANAGED").write_text("test marker", encoding="utf-8")
        customize = site / "sitecustomize.py"
        customize.write_text(
            f"import sysconfig\nsysconfig.get_path = lambda name: {str(site)!r}\n",
            encoding="utf-8")
        with patch.dict(os.environ, {"PYTHONPATH": str(site)}):
            self.assertTrue(pb.externally_managed(sys.executable))
            self.assertFalse(pb.externally_managed(self.python))

    def test_resolution_failure_stops_before_pytest(self):
        req = self.root / "invalid.txt"
        req.write_text("--no-index\nbrief-base>=999\n", encoding="utf-8")
        with patch.object(pb.subprocess, "run", wraps=subprocess.run) as run:
            self.assertEqual(pb.main(["--python", self.python, "--requirements", str(req)]), 3)
        self.assertFalse(any("-m" in c.args[0] and "pytest" in c.args[0] for c in run.call_args_list))


class CommandRegressionTests(unittest.TestCase):
    def test_target_managed_probe_and_flag(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            if "-c" in command:
                return subprocess.CompletedProcess(command, 0, "1\n", "")
            if "--dry-run" in command:
                Path(command[command.index("--report") + 1]).write_text(
                    json.dumps({"version": "1", "install": [{"metadata": {"name": "demo"}}]}))
            return subprocess.CompletedProcess(command, 0, "1 passed in 0.01s\n", "")

        with patch.object(pb.subprocess, "run", side_effect=run):
            self.assertEqual(pb.main(["--python", "other-python", "--requirements", "req.txt"]), 0)
        self.assertTrue(all(c[0] == "other-python" for c in commands))
        installs = [c for c in commands if "install" in c]
        self.assertEqual(len(installs), 2)
        self.assertTrue(all("--break-system-packages" in c for c in installs))
        self.assertTrue(all(c[-2:] == ["-r", "req.txt"] for c in installs))

    def test_install_failure_and_satisfied_skip(self):
        for need, install_code, expected in [(["demo"], 1, 3), ([], 0, 1)]:
            with self.subTest(need=need):
                def run(command, **kwargs):
                    if "pip" in command:
                        raise subprocess.CalledProcessError(install_code, command, "", "install failed")
                    return subprocess.CompletedProcess(command, 1, "1 failed in 0.01s\n", "")
                with patch.object(pb, "externally_managed", return_value=False), patch.object(pb, "resolve", return_value=need), patch.object(pb.subprocess, "run", side_effect=run) as mock:
                    self.assertEqual(pb.main(["--requirements", "req.txt"]), expected)
                self.assertEqual(len(mock.call_args_list), 1)
                command = mock.call_args.args[0]
                self.assertNotIn("--break-system-packages", command)
                self.assertEqual("pip" in command, bool(need))

    def test_broken_report_or_interpreter_is_dependency_failure(self):
        with patch.object(pb, "externally_managed", side_effect=FileNotFoundError("python missing")):
            self.assertEqual(pb.main(["--requirements", "req.txt"]), 3)
        with patch.object(pb, "externally_managed", return_value=False), patch.object(pb, "resolve", side_effect=ValueError("bad report")):
            self.assertEqual(pb.main(["--requirements", "req.txt"]), 3)


def test_brief_keeps_failures_coverage_and_summary():
    out = """....F
=========================== short test summary info ============================
FAILED tests/test_a.py::test_x - assert 1 == 2
Name        Stmts   Miss Branch BrPart  Cover   Missing
xprobe.py     560      4    296      3    99%   314
TOTAL         560      4    296      3    99%
Required test coverage of 90% reached. Total coverage: 99.18%
1 failed, 229 passed in 6.16s
"""
    assert pb.brief(out, 1, 5) == ["FAILED tests/test_a.py::test_x - assert 1 == 2",
                                   "TOTAL         560      4    296      3    99%",
                                   "Required test coverage of 90% reached. Total coverage: 99.18%",
                                   "1 failed, 229 passed in 6.16s"]


def test_brief_falls_back_to_tail_when_pytest_could_not_run():
    out = "ERROR: usage: python -m pytest\npython -m pytest: error: unrecognized arguments: --cov=x\n"
    assert pb.brief(out, 4, 1) == ["python -m pytest: error: unrecognized arguments: --cov=x"]


def test_cli_runs_a_project_and_returns_pytest_code(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "requirements.txt").write_text("pytest\n")
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n\ndef test_bad():\n    assert 1 == 2\n")
    done = subprocess.run([sys.executable, "-S", str(SCRIPT), "--", "-o", "addopts="], cwd=tmp_path,
                          capture_output=True, text=True)
    assert done.returncode == 1
    lines = done.stdout.strip().splitlines()
    assert lines[0].startswith("FAILED tests/test_ok.py::test_bad")
    assert lines[-1].startswith("1 failed, 1 passed") and "installed" not in done.stdout
    bad = subprocess.run([sys.executable, "-S", str(SCRIPT), "--requirements", "nope.txt"], cwd=tmp_path,
                         capture_output=True, text=True)
    assert bad.returncode == 3

"""Explicit module packaging must not make unrelated source adapters importable."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vendor_dispatch", ROOT / "pilot/vendor_dispatch.py")
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)
PROJECT = """[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"
[project]
name = "sample"
requires-python = ">=3.11"
dependencies = []
[tool.setuptools]
py-modules = ["xprobe"]
"""
ENV = dispatch.Env((3, 11, 0), False, False, True, 26)

class ExplicitModules(unittest.TestCase):
    def test_installed_module_uses_uv(self):
        self.assertEqual(dispatch.classify("xprobe.py", ENV, dispatch.Probe(PROJECT, None)).action, "uv")

    def test_adapter_uses_verified_fetch(self):
        self.assertEqual(dispatch.classify("scripts/xprobe_pytest.py", ENV, dispatch.Probe(PROJECT, None)).action, "fetch")

    def test_missing_explicit_metadata_keeps_existing_import_check(self):
        project = PROJECT.split("[tool.setuptools]")[0]
        self.assertEqual(dispatch.classify("adapter.py", ENV, dispatch.Probe(project, None)).action, "uv")

    def test_offline_and_no_site_do_not_fetch(self):
        for env in (ENV._replace(offline=True), ENV._replace(no_site=True)):
            self.assertEqual(dispatch.classify("scripts/xprobe_pytest.py", env, dispatch.Probe(PROJECT, None)).action, "skip")

    def test_fetch_not_install_and_failure_not_hidden(self):
        catalog = {"schema": "vendor-catalog/1", "tools": [{"repository": "example/xprobe", "source": "scripts/xprobe_pytest.py", "commit": "a"*40}]}
        with patch.object(dispatch, "load_probe", return_value=dispatch.Probe(PROJECT, None)), patch.object(dispatch, "fetch_source", return_value=("a"*40, "b"*64)) as fetch:
            rows = dispatch.dispatch(catalog, ENV, None, None, lambda *a: self.fail("must not install"))
        self.assertEqual(rows[0]["action"], "fetch")
        self.assertEqual(rows[0]["sha256"], "b"*64)
        fetch.assert_called_once()
        with patch.object(dispatch, "load_probe", return_value=dispatch.Probe(PROJECT, None)), patch.object(dispatch, "fetch_source", side_effect=RuntimeError("bad source")):
            rows = dispatch.dispatch(catalog, ENV, None, None, lambda *a: self.fail("must not install"))
        self.assertEqual(rows[0]["action"], "error")

if __name__ == "__main__":
    unittest.main()

"""Fixed Gradio frontend export tests; fake uv, no installation or server."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import nano_export as ne

PROJECT = b'''[project]
name = "md-market"
requires-python = ">=3.11"
dependencies = []
[dependency-groups]
dev = ["pytest>=8.4.2", "PyYAML>=6.0.3"]
frontend = ["gradio>=6.30.0", "streamlit>=1.66.0", "playwright>=1.64.0"]
[tool.uv]
environments = ["sys_platform != 'emscripten' and python_version >= '3.11'"]
override-dependencies = ["pillow>=13.0.0", "safehttpx>=0.2.0", "tomlkit>=0.15.2"]
[tool.uv.dependency-groups]
dev = {requires-python = ">=3.11"}
frontend = {requires-python = ">=3.11"}
'''
LOCK = b'''version = 1
revision = 5
[[package]]
name = "md-market"
source = {editable = "."}
[[package]]
name = "gradio"
source = {registry = "https://pypi.org/simple"}
'''


class MarkdownGradioRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.outputs = self.root / "source", self.root / "outputs"
        self.source.mkdir(); self.outputs.mkdir()
        self.inventory = self.root / "inventory.json"
        self.pin = {"repository": "myon-bioinformatics/markdown", "commit": "e" * 40,
                    "input_blobs_unchanged": {"pyproject.toml": ne._blob(PROJECT), "uv.lock": ne._blob(LOCK)}}
        self.catalog = {"current_snapshot": {"schema": "nano-screening/1", "repositories": [{
            "repository": self.pin["repository"], "commit": self.pin["commit"],
            "visibility": "public", "private": False, "archived": False}]}}
        self.inventory.write_text(json.dumps(self.catalog))
        (self.source / "pyproject.toml").write_bytes(PROJECT)
        (self.source / "uv.lock").write_bytes(LOCK)
        self.calls = []
        text = "".join(name + "==1.0\n" for name in sorted(ne.MARKDOWN_GRADIO_PACKAGES - {"numpy"}))
        text += 'numpy==2.4.6 ; python_full_version < "3.12"\n'
        text += 'numpy==2.5.4 ; python_full_version >= "3.12"\n'
        self.export = text.encode()

    def fake(self, argv, **kwargs):
        self.calls.append(argv)
        kwargs["stdout"].write(b"uv 0.13.4\n" if argv[-1] == "--version" else self.export)
        return subprocess.CompletedProcess(argv, 0)

    def prepare(self):
        with patch.object(ne, "MARKDOWN_GRADIO_PIN", self.pin), \
             patch.object(ne.shutil, "which", return_value=sys.executable), \
             patch.object(ne.subprocess, "run", side_effect=self.fake):
            return ne.prepare(self.source, self.outputs, inventory=self.inventory, feature="markdown-gradio")

    def test_only_other_frontends_are_pruned_and_markers_preserved(self):
        out = self.prepare()
        argv = self.calls[-1]
        self.assertEqual(argv[argv.index("--group"):],
                         ["--group", "frontend", "--prune", "streamlit", "--prune", "playwright"])
        for flag in ("--offline", "--locked", "--no-python-downloads", "--no-default-groups", "--no-emit-project"):
            self.assertIn(flag, argv)
        self.assertNotIn("--all-groups", argv)
        self.assertEqual((out / "requirements.txt").read_bytes(), self.export)
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(receipt["recipe"], "markdown-gradio")
        self.assertEqual(receipt["pruned_packages"], ["streamlit", "playwright"])
        self.assertEqual(receipt["exported_requirement_rows"], 56)
        self.assertFalse(receipt["installations_performed"])
        self.assertFalse(receipt["workloads_executed"])
        self.assertFalse(receipt["checkout_head_verified"])

    def test_unreviewed_input_rejected_before_tool(self):
        (self.source / "uv.lock").write_bytes(LOCK + b"# changed\n")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.outputs.iterdir()), [])

    def test_private_or_mismatched_identity_rejected(self):
        for key, value in (("private", True), ("commit", "f" * 40)):
            with self.subTest(key=key):
                catalog = json.loads(json.dumps(self.catalog))
                catalog["current_snapshot"]["repositories"][0][key] = value
                self.inventory.write_text(json.dumps(catalog))
                with self.assertRaisesRegex(ValueError, "source identity"):
                    self.prepare()
        self.assertEqual(self.calls, [])

    def test_foreign_source_rejected_even_with_matching_pin(self):
        lock = LOCK.replace(b'{registry = "https://pypi.org/simple"}', b'{directory = "../elsewhere"}')
        (self.source / "uv.lock").write_bytes(lock)
        self.pin["input_blobs_unchanged"]["uv.lock"] = ne._blob(lock)
        with self.assertRaisesRegex(ValueError, "path, Git or workspace"):
            self.prepare()
        self.assertEqual(self.calls, [])

    def test_missing_extra_or_wrong_duplicate_rows_fail_closed(self):
        good = self.export
        for output in (good.replace(b"gradio==1.0\n", b""), good + b"streamlit==1.66.0\n",
                       good + b"numpy==2.5.4\n", good.replace(b"numpy==2.5.4", b"pandas==2.5.4")):
            with self.subTest(output=output[-90:]):
                self.export = output
                with self.assertRaisesRegex(ValueError, "Gradio closure"):
                    self.prepare()
                self.assertEqual(list(self.outputs.iterdir()), [])

    def test_unknown_frontend_shape_fails_closed(self):
        project = PROJECT.replace(b"playwright>=1.64.0", b"another-frontend")
        (self.source / "pyproject.toml").write_bytes(project)
        self.pin["input_blobs_unchanged"]["pyproject.toml"] = ne._blob(project)
        with self.assertRaisesRegex(ValueError, "project/lock structure"):
            self.prepare()


if __name__ == "__main__":
    unittest.main()

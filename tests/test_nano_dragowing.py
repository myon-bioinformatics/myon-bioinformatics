"""Fixed DRAgoWing recipe tests; fake uv, no package installation."""
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
name = "dragowing"
dependencies = ["markdown", "numpy", "polars"]
[dependency-groups]
dev = ["pytest"]
[tool.uv]
package = false
'''
LOCK = b'''version = 1
revision = 5
[[package]]
name = "dragowing"
source = {virtual = "."}
[[package]]
name = "markdown"
source = {registry = "https://pypi.org/simple"}
'''


class DragowingRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.outputs = self.root / "source", self.root / "outputs"
        self.source.mkdir(); self.outputs.mkdir()
        self.inventory = self.root / "inventory.json"
        self.pin = {"repository": "myon-bioinformatics/DRAgoWing", "commit": "b" * 40,
                    "input_blobs_unchanged": {"pyproject.toml": ne._blob(PROJECT), "uv.lock": ne._blob(LOCK)}}
        self.catalog = {"current_snapshot": {"schema": "nano-screening/1", "repositories": [{
            "repository": self.pin["repository"], "commit": self.pin["commit"],
            "visibility": "public", "private": False, "archived": False}]}}
        self.inventory.write_text(json.dumps(self.catalog))
        (self.source / "pyproject.toml").write_bytes(PROJECT)
        (self.source / "uv.lock").write_bytes(LOCK)
        self.calls = []
        self.export = b"markdown==3.11.2\n"

    def fake(self, argv, **kwargs):
        self.calls.append(argv)
        kwargs["stdout"].write(b"uv 0.13.4\n" if argv[-1] == "--version" else self.export)
        return subprocess.CompletedProcess(argv, 0)

    def prepare(self):
        with patch.object(ne, "DRAGOWING_PIN", self.pin), \
             patch.object(ne.shutil, "which", return_value=sys.executable), \
             patch.object(ne.subprocess, "run", side_effect=self.fake):
            return ne.prepare(self.source, self.outputs, inventory=self.inventory, feature="dragowing-html")

    def test_explicit_markdown_only_selection_and_receipt(self):
        out = self.prepare()
        argv = self.calls[-1]
        self.assertNotIn("--group", argv)
        self.assertIn("--no-default-groups", argv)
        self.assertEqual([argv[i + 1] for i, value in enumerate(argv) if value == "--prune"], ["numpy", "polars"])
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(receipt["recipe"], "dragowing-html")
        self.assertEqual(receipt["source_repository"], self.pin["repository"])
        self.assertEqual(receipt["exported_package_names"], ["markdown"])
        self.assertEqual(receipt["exported_requirement_rows"], 1)
        self.assertEqual(receipt["groups"], [])
        self.assertEqual(receipt["pruned_packages"], ["numpy", "polars"])
        self.assertFalse(receipt["installations_performed"])
        self.assertFalse(receipt["checkout_head_verified"])
        self.assertIsNone(receipt["installed_bytes"])
        self.assertEqual((self.source / "uv.lock").read_bytes(), LOCK)

    def test_wrong_snapshot_and_changed_file_fail_before_uv(self):
        self.catalog["current_snapshot"]["repositories"][0]["commit"] = "c" * 40
        self.inventory.write_text(json.dumps(self.catalog))
        with self.assertRaisesRegex(ValueError, "source identity"):
            self.prepare()
        self.catalog["current_snapshot"]["repositories"][0]["commit"] = "b" * 40
        self.inventory.write_text(json.dumps(self.catalog))
        (self.source / "uv.lock").write_bytes(LOCK + b"# changed\n")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertFalse(self.calls)
        self.assertFalse(list(self.outputs.iterdir()))

    def test_unsupported_source_with_matching_blob_is_rejected(self):
        changed = LOCK.replace(b'{registry = "https://pypi.org/simple"}', b'{path = "../foreign"}')
        (self.source / "uv.lock").write_bytes(changed)
        self.pin["input_blobs_unchanged"]["uv.lock"] = ne._blob(changed)
        with self.assertRaisesRegex(ValueError, "path, Git or workspace"):
            self.prepare()
        self.assertFalse(self.calls)

    def test_unexpected_closure_leaves_no_artifact(self):
        for extra in (b"numpy==1\n", b"pytest==1\n", b"markdown==3.11.2\n"):
            with self.subTest(extra=extra):
                self.export = b"markdown==3.11.2\n" + extra
                with self.assertRaisesRegex(ValueError, "Markdown-only"):
                    self.prepare()
                self.assertFalse(list(self.outputs.iterdir()))

    def test_unknown_feature_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported named feature"):
            ne.prepare(self.source, self.outputs, feature="all")
        self.assertFalse(list(self.outputs.iterdir()))


if __name__ == "__main__":
    unittest.main()

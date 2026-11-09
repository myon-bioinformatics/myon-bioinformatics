"""Fixed MCP SDK-client export tests; fake uv, no install, socket or model."""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path; from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import nano_export as ne

PROJECT = b'''[project]
name = "mcp-toolcall-lab"
requires-python = ">=3.12"
dependencies = ["fastmcp>=4.1.3", "mcp>=2.3.0", "pydantic>=2.14.1"]
[project.optional-dependencies]
galleria = ["gradio"]
test = ["pytest"]
browser-test = ["playwright"]
[tool.uv]
override-dependencies = ["pillow>=13.0.0", "safehttpx>=0.2.0", "tomlkit>=0.15.2", "pyee>=15.0.0"]
'''
LOCK = b'''version = 1
revision = 5
[[package]]
name = "mcp-toolcall-lab"
source = {editable = "."}
[[package]]
name = "mcp"
source = {registry = "https://pypi.org/simple"}
'''


class McpClientRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.outputs = self.root / "source", self.root / "outputs"
        self.source.mkdir(); self.outputs.mkdir()
        self.inventory = self.root / "inventory.json"
        self.pin = {"repository": "myon-bioinformatics/mcp-toolcall-lab", "commit": "c" * 40,
                    "input_blobs_unchanged": {"pyproject.toml": ne._blob(PROJECT), "uv.lock": ne._blob(LOCK)}}
        self.catalog = {"current_snapshot": {"schema": "nano-screening/1", "repositories": [{
            "repository": self.pin["repository"], "commit": self.pin["commit"],
            "visibility": "public", "private": False, "archived": False}]}}
        self.inventory.write_text(json.dumps(self.catalog))
        (self.source / "pyproject.toml").write_bytes(PROJECT)
        (self.source / "uv.lock").write_bytes(LOCK)
        self.calls = []
        self.export = "".join(name + "==1.0\n" for name in sorted(ne.MCP_CLIENT_PACKAGES)).encode()

    def fake(self, argv, **kwargs):
        self.calls.append(argv)
        kwargs["stdout"].write(b"uv 0.13.4\n" if argv[-1] == "--version" else self.export)
        return subprocess.CompletedProcess(argv, 0)

    def prepare(self):
        with patch.object(ne, "MCP_CLIENT_PIN", self.pin), \
             patch.object(ne.shutil, "which", return_value=sys.executable), \
             patch.object(ne.subprocess, "run", side_effect=self.fake):
            return ne.prepare(self.source, self.outputs, inventory=self.inventory, feature="mcp-chat-client")

    def test_sdk_closure_excludes_only_hosted_server(self):
        out = self.prepare()
        argv = self.calls[-1]
        self.assertEqual(argv[argv.index("--prune") + 1:], ["fastmcp"])
        for flag in ("--offline", "--locked", "--no-python-downloads", "--no-default-groups", "--no-emit-project"):
            self.assertIn(flag, argv)
        self.assertNotIn("--extra", argv)
        self.assertNotIn("--all-extras", argv)
        receipt = json.loads((out / "receipt.json").read_text())
        self.assertEqual(receipt["recipe"], "mcp-chat-client")
        self.assertEqual(receipt["pruned_packages"], ["fastmcp"])
        self.assertEqual(receipt["exported_requirement_rows"], 30)
        self.assertFalse(receipt["installations_performed"])
        self.assertFalse(receipt["workloads_executed"])
        self.assertFalse(receipt["checkout_head_verified"])

    def test_unreviewed_manifest_rejected_before_tool(self):
        (self.source / "pyproject.toml").write_bytes(PROJECT + b"# changed\n")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.outputs.iterdir()), [])

    def test_private_or_mismatched_identity_rejected(self):
        for key, value in (("private", True), ("commit", "d" * 40)):
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

    def test_missing_extra_and_duplicate_rows_fail_closed(self):
        good = self.export
        for output in (good.replace(b"mcp==1.0\n", b""), good + b"fastmcp==4.1.3\n", good + b"mcp==1.0\n"):
            with self.subTest(output=output[-30:]):
                self.export = output
                with self.assertRaisesRegex(ValueError, "client closure"):
                    self.prepare()
                self.assertEqual(list(self.outputs.iterdir()), [])

    def test_unknown_optional_group_shape_fails_closed(self):
        project = PROJECT.replace(b"browser-test", b"unknown-extra")
        (self.source / "pyproject.toml").write_bytes(project)
        self.pin["input_blobs_unchanged"]["pyproject.toml"] = ne._blob(project)
        with self.assertRaisesRegex(ValueError, "project/lock structure"):
            self.prepare()


if __name__ == "__main__":
    unittest.main()

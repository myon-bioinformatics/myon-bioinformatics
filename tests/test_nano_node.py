"""Offline fake-npm regression tests; no dependency installation or network."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("nano_node", Path(__file__).parents[1] / "tools/nano_node.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)


class NanoNodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.output = self.root / "out"
        self.source.mkdir()
        manifest = {"private": True, "engines": {"node": ">=24"},
                    "dependencies": {"exceljs": "4.4.0", "pptxgenjs": "4.0.1"},
                    "overrides": {"image-size": "2.0.4", "uuid": "14.0.3"}}
        self.lock = {"name": "workspace", "lockfileVersion": 3, "requires": True,
                     "packages": {"": {"dependencies": manifest["dependencies"]}}}
        for path in sorted(nano.EXPECTED_PATHS | {"node_modules/exceljs"}):
            self.lock["packages"][path] = {"version": "1.0.0", "integrity": "sha512-test",
                "resolved": "https://registry.npmjs.org/test/-/test-1.0.0.tgz"}
        data = {"package.json": json.dumps(manifest).encode(),
                "package-lock.json": json.dumps(self.lock).encode(),
                "pptx_render.py": b"# pinned render\n", "LICENSE": b"test license\n"}
        for name, content in data.items():
            (self.source / name).write_bytes(content)
        self.hashes = {name: nano.git_blob(content) for name, content in data.items()}
        self.patcher = patch.object(nano, "SOURCE_BLOBS", self.hashes)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.npm = self.root / "npm"
        self.fake()

    def fake(self, mode="good"):
        code = '''#!/usr/bin/env python3
import json, os, pathlib, sys
if sys.argv[1:] == ["--version"]:
    print("11.9.0-test"); sys.exit(0)
assert sys.argv[1:7] == ["install", "--package-lock-only", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"]
assert not any(k.lower().startswith("npm_config_") for k in os.environ)
assert not any(k in os.environ for k in ("NODE_OPTIONS", "NODE_PATH", "NODE_ENV"))
for flag in ("--userconfig", "--globalconfig"):
    assert pathlib.Path(sys.argv[sys.argv.index(flag)+1]).read_text() == ""
p = pathlib.Path("package-lock.json")
data = json.loads(p.read_text())
data["name"] = "Ironmate"
data["packages"] = {k:v for k,v in data["packages"].items() if not k or k in PATHS}
data["packages"][""]["dependencies"] = {"pptxgenjs": "4.0.1"}
data["packages"][""]["engines"] = {"node": ">=24"}
if MODE == "fail": sys.exit(2)
if MODE == "record": data["packages"]["node_modules/pptxgenjs"]["version"] = "9.9.9"
if MODE == "missing": del data["packages"]["node_modules/image-size"]
if MODE == "metadata": data["requires"] = False
p.write_text(json.dumps(data))
if MODE == "manifest": pathlib.Path("package.json").write_text("{}")
if MODE == "source": pathlib.Path("pptx_render.py").write_text("changed")
if MODE == "installed": pathlib.Path("node_modules").mkdir()
'''
        code = code.replace("import json, os, pathlib, sys", "import json, os, pathlib, sys\nMODE = " + repr(mode) + "\nPATHS = " + repr(set(nano.EXPECTED_PATHS)))
        self.npm.write_text(code)
        self.npm.chmod(0o755)

    def prepare(self):
        return nano.prepare(self.source, self.output, str(self.npm))

    def test_success_has_private_atomic_result_and_no_install_claim(self):
        result = self.prepare()
        receipt = json.loads((result / "receipt.json").read_text())
        self.assertEqual(result.stat().st_mode & 0o777, 0o700)
        self.assertEqual(receipt["candidate_locked_package_records"], 18)
        self.assertIsNone(receipt["installed_bytes"])
        self.assertFalse(receipt["checkout_head_verified"])
        self.assertEqual(list(self.output.iterdir()), [result])
        self.assertFalse((result / "Ironmate/node_modules").exists())
        self.assertEqual(nano.read_snapshot(self.source)["LICENSE"], b"test license\n")

    def test_bad_source_fails_before_output(self):
        (self.source / "LICENSE").write_text("bad")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_oversized_source_fails_before_output(self):
        with patch.object(nano, "MAX_INPUT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "bounded"):
                self.prepare()
        self.assertFalse(self.output.exists())

    def test_symlink_input_rejected(self):
        path = self.source / "LICENSE"
        target = self.root / "license"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.prepare()

    def test_output_must_be_outside_source(self):
        before = sorted(self.source.rglob('*'))
        for output in (self.source, self.source / "new" / "outputs"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "outside"):
                nano.prepare(self.source, output, str(self.npm))
        self.assertEqual(sorted(self.source.rglob('*')), before)

    def test_failed_npm_cleans_only_its_own_output(self):
        self.output.mkdir()
        keep = self.output / "keep"
        keep.write_text("keep")
        self.fake("fail")
        with self.assertRaisesRegex(ValueError, "exit 2"):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [keep])

    def test_unexpected_records_manifest_source_or_install_fail_closed(self):
        for mode in ("record", "missing", "metadata", "manifest", "source", "installed"):
            with self.subTest(mode=mode):
                self.fake(mode)
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertEqual(list(self.output.iterdir()), [])

    def test_unsupported_graph_rejected(self):
        for field in nano.UNSUPPORTED:
            value = json.loads(json.dumps(self.lock))
            value["packages"]["node_modules/pptxgenjs"][field] = False
            manifest = json.loads((self.source / "package.json").read_text())
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "graph"):
                nano.validate_original(manifest, value)

    def test_untrusted_npm_and_node_config_not_inherited(self):
        with patch.dict(os.environ, {"npm_config_registry": "http://bad.invalid", "NPM_CONFIG_OFFLINE": "false",
                                    "NODE_OPTIONS": "--bad", "NODE_PATH": "/bad", "NODE_ENV": "production"}):
            self.prepare()

    def test_parallel_calls_have_disjoint_outputs(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.prepare(), range(2)))
        self.assertNotEqual(*results)
        self.assertEqual(set(self.output.iterdir()), set(results))

    def test_relative_npm_path_resolves_before_chdir(self):
        relative = os.path.relpath(self.npm)
        result = nano.prepare(self.source, self.output, relative)
        self.assertTrue((result / "receipt.json").is_file())


if __name__ == "__main__":
    unittest.main()

"""Fixed BTK graph/failure tests; fake npm, no installs or browser execution."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("nano_node_btk", Path(__file__).parents[1] / "tools/nano_node.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)


class BtkNodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.output = self.root / "source", self.root / "out"
        self.source.mkdir()
        self.manifest = {"private": True, "engines": {"node": ">=24"},
                         "devDependencies": {**nano.BTK_SELECTED, "exceljs": "^4.4.0"},
                         "overrides": {"uuid": "^14.0.3", "minimatch": "^11.0.1"}}
        self.lock = {"name": "dep-browser-test-kit", "lockfileVersion": 3, "requires": True,
                     "packages": {"": {"devDependencies": self.manifest["devDependencies"],
                                           "engines": {"node": ">=24"}}}}
        for path in nano.BTK_EXPECTED_PATHS | {"node_modules/exceljs"}:
            self.lock["packages"][path] = {"version": "1.0.0", "dev": True,
                "resolved": "https://registry.npmjs.org/test/-/test-1.0.0.tgz", "integrity": "sha512-fixture"}
        self.lock["packages"]["node_modules/linkedom"].update(
            peerDependencies={"canvas": ">= 2"}, peerDependenciesMeta={"canvas": {"optional": True}})
        data = {"package.json": json.dumps(self.manifest).encode(),
                "package-lock.json": json.dumps(self.lock).encode(), "LICENSE": b"test license\n"}
        for name, content in data.items():
            (self.source / name).write_bytes(content)
        self.hashes = {name: nano.git_blob(content) for name, content in data.items()}
        self.patcher = patch.object(nano, "BTK_SOURCE_BLOBS", self.hashes)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.npm = self.root / "npm"
        self.fake()

    def fake(self, mode="good"):
        script = '''#!/usr/bin/env python3
import json, pathlib, sys
if sys.argv[1:] == ["--version"]:
    print("11.9.0-test"); sys.exit(0)
assert "--offline" in sys.argv and "--package-lock-only" in sys.argv
assert "--ignore-scripts" in sys.argv
if MODE == "fail": sys.exit(2)
p=pathlib.Path("package-lock.json")
d=json.loads(p.read_text()); d["name"]="browser-test-kit"
d["packages"]={k:v for k,v in d["packages"].items() if not k or k in PATHS}
d["packages"][""]["devDependencies"]=SELECTED
if MODE == "peer": d["packages"]["node_modules/linkedom"]["peerDependenciesMeta"]["canvas"]["optional"]=False
if MODE == "missing": del d["packages"]["node_modules/marked"]
if MODE == "version": d["packages"]["node_modules/markdown-it"]["version"]="99.0.0"
if MODE == "canvas": d["packages"]["node_modules/canvas"]={"version":"3.0.0"}
p.write_text(json.dumps(d))
'''
        script = script.replace("import json, pathlib, sys", "import json, pathlib, sys\nMODE=" + repr(mode)
                                + "\nPATHS=" + repr(set(nano.BTK_EXPECTED_PATHS)) + "\nSELECTED=" + repr(nano.BTK_SELECTED))
        self.npm.write_text(script)
        self.npm.chmod(0o755)

    def prepare(self):
        return nano.prepare(self.source, self.output, str(self.npm), feature=nano.BTK_FEATURE)

    def test_success_retains_both_readers_runner_and_exact_optional_peer(self):
        result = self.prepare()
        r = json.loads((result / "receipt.json").read_text())
        projected = json.loads((result / "browser-test-kit/package-lock.json").read_text())
        self.assertEqual(r["candidate_locked_package_records"], 30)
        self.assertEqual(r["source_commit"], nano.BTK_COMMIT)
        self.assertEqual(r["feature"], nano.BTK_FEATURE)
        self.assertIsNone(r["installed_bytes"])
        self.assertEqual(projected["packages"][""]["devDependencies"], nano.BTK_SELECTED)
        self.assertEqual(projected["packages"]["node_modules/linkedom"], self.lock["packages"]["node_modules/linkedom"])
        self.assertNotIn("node_modules/canvas", projected["packages"])
        self.assertFalse((result / "browser-test-kit/node_modules").exists())
        self.assertEqual(r["preserved_overrides"], self.manifest["overrides"])

    def test_wrong_blob_rejected_before_output(self):
        (self.source / "LICENSE").write_text("changed")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_dependency_only_ignores_but_never_stages_live_source(self):
        # A later live test/source edit must neither become a historical staged
        # test nor require a parent manifest-pin refresh.
        (self.source / "LICENSE").write_text("changed live checkout")
        (self.source / "tests").mkdir()
        (self.source / "tests/new.spec.ts").write_text("throw new Error('do not execute')")
        result = nano.prepare(self.source, self.output, str(self.npm),
                              feature=nano.BTK_FEATURE, dependencies_only=True)
        self.assertEqual({p.name for p in (result / "browser-test-kit").iterdir()},
                         {"package.json", "package-lock.json"})
        receipt = json.loads((result / "receipt.json").read_text())
        self.assertEqual(set(receipt["source_git_blobs"]), {"package.json", "package-lock.json"})
        self.assertTrue(receipt["dependencies_only"])
        self.assertFalse(receipt["application_source_staged"])
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()  # The existing full-snapshot default stays strict.

    def test_dependency_only_still_rejects_changed_package_inputs(self):
        (self.source / "package.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            nano.prepare(self.source, self.output, str(self.npm),
                         feature=nano.BTK_FEATURE, dependencies_only=True)
        self.assertFalse(self.output.exists())

    def test_only_exact_linkedom_optional_canvas_peer_allowed(self):
        for mode in ("required", "version", "extra", "foreign", "canvas", "dev"):
            value = json.loads(json.dumps(self.lock))
            link = value["packages"]["node_modules/linkedom"]
            if mode == "required": link["peerDependenciesMeta"]["canvas"]["optional"] = False
            if mode == "version": link["peerDependencies"]["canvas"] = ">= 3"
            if mode == "extra": link["peerDependencies"]["other"] = "*"
            if mode == "foreign": value["packages"]["node_modules/marked"]["peerDependencies"] = {"canvas": ">= 2"}
            if mode == "canvas": value["packages"]["node_modules/canvas"] = dict(value["packages"]["node_modules/marked"])
            if mode == "dev": link["dev"] = False
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                nano.validate_original(self.manifest, value, feature=nano.BTK_FEATURE)

    def test_projection_change_or_failure_cleans_own_output(self):
        for mode in ("fail", "peer", "missing", "version", "canvas"):
            self.fake(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(list(self.output.iterdir()), [])

    def test_parallel_calls_are_isolated(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.prepare(), range(2)))
        self.assertNotEqual(*results)
        self.assertEqual(set(self.output.iterdir()), set(results))

    def test_unknown_feature_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError, "unsupported named feature"):
            nano.prepare(self.source, self.output, str(self.npm), feature="all-peers")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

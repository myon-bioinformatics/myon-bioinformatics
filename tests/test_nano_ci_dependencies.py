"""Dependency-only CI contracts: stdlib mocks, never install or execute tests."""
import copy, importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path; from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("nano_ci_dependencies", Path(__file__).parents[1] / "tools/nano_node.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)


class DependencyOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "out"
        self.manifest = {"private": True, "type": "module", "devDependencies": {
            "linkedom": "0.18.13", "markdown-it": "15.0.3", "marked": "18.1.1",
            "string-width": "8.3.0", "smol-toml": "1.9.1"}}
        self.lock = {"name": "mirror", "lockfileVersion": 3, "requires": True,
                     "packages": {"": {"devDependencies": self.manifest["devDependencies"]}}}
        for name in self.manifest["devDependencies"]:
            self.lock["packages"]["node_modules/" + name] = {
                "version": self.manifest["devDependencies"][name], "dev": True,
                "resolved": "https://registry.npmjs.org/fixture/-/fixture.tgz", "integrity": "sha512-fixture"}
        self.lock["packages"]["node_modules/linkedom"].update(
            peerDependencies={"canvas": ">= 2"}, peerDependenciesMeta={"canvas": {"optional": True}})
        self.blobs = {}
        for name, value in (("package.json", self.manifest), ("package-lock.json", self.lock)):
            data = json.dumps(value).encode()
            (self.source / name).write_bytes(data)
            self.blobs[name] = nano.git_blob(data)
        self.patchers = [patch.object(nano, "MARKDOWN_SOURCE_BLOBS", self.blobs),
                         patch.object(nano.shutil, "which", return_value=sys.executable),
                         patch.object(nano.subprocess, "run", side_effect=self.fake_npm)]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)
        self.mode = "good"

    def fake_npm(self, argv, **kwargs):
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "11.9.0-test\n", "")
        self.assertEqual(argv[1:7], ["install", "--package-lock-only", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"])
        project = Path(kwargs["cwd"])
        self.assertEqual({p.name for p in project.iterdir()}, {"package.json", "package-lock.json"})
        if self.mode == "fail":
            return subprocess.CompletedProcess(argv, 2, "", "failed")
        lock = json.loads((project / "package-lock.json").read_bytes())
        lock["name"] = "markdown-toml"
        lock["packages"] = {k: v for k, v in lock["packages"].items() if not k or k == "node_modules/smol-toml"}
        lock["packages"][""]["devDependencies"] = nano.MARKDOWN_SELECTED
        if self.mode == "missing": del lock["packages"]["node_modules/smol-toml"]
        if self.mode == "version": lock["packages"]["node_modules/smol-toml"]["version"] = "99"
        if self.mode == "extra": lock["packages"]["node_modules/other"] = {}
        if self.mode == "engines": lock["packages"][""]["engines"] = {"node": ">=24"}
        (project / "package-lock.json").write_text(json.dumps(lock))
        if self.mode == "source": (self.source / "package.json").write_text("{}")
        if self.mode == "installed": (project / "node_modules").mkdir()
        return subprocess.CompletedProcess(argv, 0, "", "")

    def prepare(self, **kwargs):
        return nano.prepare(self.source, self.output, feature=nano.MARKDOWN_FEATURE,
                            dependencies_only=True, **kwargs)

    def test_only_two_pinned_inputs_staged_and_scope_explicit(self):
        (self.source / "tests").mkdir()
        (self.source / "tests/test_live.py").write_text("raise RuntimeError('never executed or copied')")
        (self.source / ".npmrc").write_text("registry=https://invalid.example")
        result = self.prepare()
        receipt = json.loads((result / "receipt.json").read_text())
        self.assertEqual(set(receipt["source_git_blobs"]), {"package.json", "package-lock.json"})
        self.assertTrue(receipt["dependencies_only"])
        self.assertFalse(receipt["application_source_staged"])
        self.assertFalse(receipt["checkout_head_verified"])
        self.assertEqual(receipt["candidate_locked_package_records"], 1)
        self.assertIsNone(receipt["installed_package_count"])
        self.assertEqual({p.name for p in (result / "markdown-toml").iterdir()}, {"package.json", "package-lock.json"})
        self.assertEqual(json.loads((result / "markdown-toml/package-lock.json").read_text())["packages"]["node_modules/smol-toml"], self.lock["packages"]["node_modules/smol-toml"])

    def test_markdown_requires_explicit_dependency_only(self):
        with self.assertRaisesRegex(ValueError, "requires --dependencies-only"):
            nano.prepare(self.source, self.output, feature=nano.MARKDOWN_FEATURE)
        self.assertFalse(self.output.exists())
        nano.subprocess.run.assert_not_called()

    def test_dependency_inputs_fail_before_npm_or_output(self):
        (self.source / "package-lock.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "blob mismatch") as raised:
            self.prepare()
        self.assertIn(self.blobs["package-lock.json"], str(raised.exception))
        self.assertIn(nano.git_blob(b"{}"), str(raised.exception))
        self.assertIn("coordinate a fixed parent selector-pin update", str(raised.exception))
        nano.subprocess.run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_or_changed_projection_fails_without_partial_output(self):
        for mode in ("fail", "missing", "version", "extra", "engines", "installed"):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(list(self.output.iterdir()), [])

    def test_input_change_during_preparation_fails(self):
        self.mode = "source"
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_exact_original_graph_and_overrides_remain_required(self):
        for change in ("override", "peer", "missing", "url", "dev"):
            manifest, lock = copy.deepcopy(self.manifest), copy.deepcopy(self.lock)
            if change == "override": manifest["overrides"] = {"smol-toml": "2"}
            if change == "peer": lock["packages"]["node_modules/smol-toml"]["peerDependencies"] = {"other": "*"}
            if change == "missing": del manifest["devDependencies"]["marked"]
            if change == "url": lock["packages"]["node_modules/smol-toml"]["resolved"] = "file:../unreviewed"
            if change == "dev": lock["packages"]["node_modules/smol-toml"]["dev"] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                nano.validate_original(manifest, lock, feature=nano.MARKDOWN_FEATURE)

    def test_output_inside_source_is_rejected_before_tools(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            nano.prepare(self.source, self.source / "out", feature=nano.MARKDOWN_FEATURE, dependencies_only=True)
        nano.subprocess.run.assert_not_called()

    def test_symlinked_package_input_rejected(self):
        target = self.root / "lock.json"
        path = self.source / "package-lock.json"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.prepare()
        nano.subprocess.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

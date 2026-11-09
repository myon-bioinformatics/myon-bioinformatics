"""Offline tests: fake uv, synthetic reviewed manifests, no package installation."""
import hashlib, json, os, subprocess, sys, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor; from pathlib import Path; from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import nano_export as ne

PROJECT = b"""[project]
name = "aoi"
[dependency-groups]
dev = ["pytest", "coverage", "selectolax"]
runtime = ["polars", "numpy", "httpx", "markdown"]
[tool.uv]
default-groups = ["dev", "runtime"]
"""
LOCK = b"""version = 1
revision = 5
[[package]]
name = "aoi"
source = {editable = "."}
[[package]]
name = "numpy"
source = {registry = "https://pypi.org/simple"}
"""
EXPORT = b"httpx==1.0\nmarkdown==1.0\nnumpy==1.0\npolars==1.0\n"


class NanoExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.parent = self.root / "outputs"
        self.source.mkdir()
        self.parent.mkdir()
        self.inventory = self.root / "inventory.json"
        (self.source / "pyproject.toml").write_bytes(PROJECT)
        (self.source / "uv.lock").write_bytes(LOCK)
        self.catalog = {"current_snapshot": {
            "schema": "nano-screening/1",
            "repositories": [{"repository": ne.REPOSITORY, "commit": "a" * 40,
                              "visibility": "public", "private": False, "archived": False}],
            "offline_export_validation": {"results": [{
                "repository": ne.REPOSITORY, "commit": "a" * 40, "selected_group": "runtime",
                "input_blobs_unchanged": {"pyproject.toml": ne._blob(PROJECT),
                                         "uv.lock": ne._blob(LOCK)}}]}}}
        self.write_inventory()
        self.calls = []

    def write_inventory(self):
        self.inventory.write_text(json.dumps(self.catalog), encoding="utf-8")

    def run_fake(self, argv, cwd, env, stdin, stdout, stderr, timeout, check):
        self.calls.append((argv, cwd, env))
        stdout.write(b"uv 0.13.4\n" if argv[-1] == "--version" else EXPORT)
        return subprocess.CompletedProcess(argv, 0)

    def prepare(self, fake=None):
        with patch.object(ne.shutil, "which", return_value=sys.executable), \
             patch.object(ne.subprocess, "run", side_effect=fake or self.run_fake):
            return ne.prepare(self.source, self.parent, inventory=self.inventory)

    def test_success_has_fixed_selection_receipt_and_private_output(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        with patch.dict(os.environ, {"UV_NO_GROUP": "runtime", "UV_PYTHON": "/bad",
                                     "PIP_INDEX_URL": "https://invalid", "PYTHONPATH": "/bad"}):
            directory = self.prepare()
        receipt = json.loads((directory / "receipt.json").read_text())
        self.assertEqual(receipt["verified_input_blobs"], self.catalog["current_snapshot"][
            "offline_export_validation"]["results"][0]["input_blobs_unchanged"])
        self.assertEqual(receipt["requirements_sha256"], hashlib.sha256(EXPORT).hexdigest())
        self.assertEqual(receipt["exported_requirement_rows"], 4)
        self.assertFalse(receipt["checkout_head_verified"])
        self.assertFalse(receipt["installations_performed"])
        self.assertFalse(receipt["workloads_executed"])
        self.assertIsNone(receipt["installed_bytes"])
        self.assertIsNone(receipt["install_seconds"])
        argv, cwd, env = self.calls[-1]
        for flag in ("--no-config", "--offline", "--locked", "--no-python-downloads",
                     "--no-default-groups", "--no-emit-project", "--no-header"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--group") + 1], "runtime")
        self.assertEqual(argv[argv.index("--python") + 1], sys.executable)
        self.assertNotIn("UV_NO_GROUP", env)
        self.assertNotIn("UV_PYTHON", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PIP_INDEX_URL", env)
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, before)
        self.assertEqual((directory / "inputs/uv.lock").read_bytes(), LOCK)
        if os.name != "nt":
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_wrong_input_fails_before_tool_or_output(self):
        (self.source / "uv.lock").write_bytes(LOCK + b"# dirty\n")
        with self.assertRaisesRegex(ValueError, "blob mismatch"):
            self.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_relative_tool_path_is_resolved_before_changing_cwd(self):
        with patch.object(ne.shutil, "which", return_value=os.path.relpath(sys.executable)), \
             patch.object(ne.subprocess, "run", side_effect=self.run_fake):
            ne.prepare(self.source, self.parent, inventory=self.inventory)
        for argv, _, _ in self.calls:
            self.assertEqual(argv[0], str(Path(sys.executable).resolve()))
            self.assertTrue(Path(argv[0]).is_absolute())

    def test_unknown_inventory_identity_or_group_fails(self):
        snapshot = self.catalog["current_snapshot"]
        for field, value in (("schema", "nano-screening/2"),):
            original = snapshot[field]
            snapshot[field] = value
            self.write_inventory()
            with self.assertRaises(ValueError):
                self.prepare()
            snapshot[field] = original
        pin = snapshot["offline_export_validation"]["results"][0]
        for field, value in (("commit", "b" * 40), ("selected_group", "dev")):
            original = pin[field]
            pin[field] = value
            self.write_inventory()
            with self.assertRaises(ValueError):
                self.prepare()
            pin[field] = original
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_external_source_even_with_matching_blob_is_rejected(self):
        bad = LOCK.replace(b'{registry = "https://pypi.org/simple"}', b'{path = "../elsewhere"}')
        (self.source / "uv.lock").write_bytes(bad)
        self.catalog["current_snapshot"]["offline_export_validation"]["results"][0][
            "input_blobs_unchanged"]["uv.lock"] = ne._blob(bad)
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, "path, Git or workspace"):
            self.prepare()
        self.assertEqual(self.calls, [])

    def test_failure_timeout_and_bad_exports_leave_no_artifact(self):
        for mode in ("fail", "timeout", "oversize", "dev", "editable", "empty", "wrong-version"):
            with self.subTest(mode=mode):
                def fake(argv, **kwargs):
                    if argv[-1] == "--version":
                        kwargs["stdout"].write(b"wrong\n" if mode == "wrong-version" else b"uv 0.13.4\n")
                        return subprocess.CompletedProcess(argv, 0)
                    kwargs["stdout"].write(EXPORT)
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(argv, 1)
                    if mode == "oversize":
                        kwargs["stdout"].write(b"x" * (ne.LIMIT + 1))
                    if mode == "dev":
                        kwargs["stdout"].write(b"pytest==1.0\n")
                    if mode == "editable":
                        kwargs["stdout"].write(b"-e .\n")
                    if mode == "empty":
                        kwargs["stdout"].seek(0); kwargs["stdout"].truncate()
                    return subprocess.CompletedProcess(argv, 7 if mode == "fail" else 0)
                with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                    self.prepare(fake)
                self.assertEqual(list(self.parent.iterdir()), [])

    def test_source_or_staged_mutation_fails_without_publishing(self):
        for mode in ("source", "stage"):
            with self.subTest(mode=mode):
                (self.source / "uv.lock").write_bytes(LOCK)
                def fake(argv, **kwargs):
                    result = self.run_fake(argv, **kwargs)
                    if argv[-1] != "--version":
                        target = self.source if mode == "source" else Path(argv[argv.index("--project") + 1])
                        (target / "uv.lock").write_bytes(b"changed")
                    return result
                with self.assertRaisesRegex(ValueError, "input changed"):
                    self.prepare(fake)
                self.assertEqual(list(self.parent.iterdir()), [])

    def test_parallel_calls_have_independent_complete_artifacts(self):
        with patch.object(ne.shutil, "which", return_value=sys.executable), \
             patch.object(ne.subprocess, "run", side_effect=self.run_fake):
            with ThreadPoolExecutor(max_workers=2) as pool:
                dirs = list(pool.map(lambda _: ne.prepare(self.source, self.parent,
                    inventory=self.inventory), range(2)))
        self.assertNotEqual(dirs[0], dirs[1])
        for directory in dirs:
            self.assertEqual((directory / "requirements.txt").read_bytes(), EXPORT)
            self.assertTrue((directory / "receipt.json").is_file())

    def test_output_inside_source_and_missing_tool_are_rejected(self):
        with self.assertRaises(ValueError):
            ne.prepare(self.source, self.source, inventory=self.inventory)
        with patch.object(ne.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "not found"):
                ne.prepare(self.source, self.parent, inventory=self.inventory)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_symlink_manifest_and_oversized_input_are_rejected(self):
        path = self.source / "uv.lock"
        path.unlink()
        other = self.root / "original.lock"
        other.write_bytes(LOCK)
        try:
            path.symlink_to(other)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.prepare()
        path.unlink()
        path.write_bytes(b"x" * (ne.LIMIT + 1))
        with self.assertRaisesRegex(ValueError, "bounded"):
            self.prepare()

    def test_invalid_timeout_is_rejected(self):
        for timeout in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ne.prepare(self.source, self.parent, timeout=timeout, inventory=self.inventory)


if __name__ == "__main__":
    unittest.main()

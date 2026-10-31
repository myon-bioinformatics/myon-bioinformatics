"""Offline runtime behavior checks; run with python -S on Python 3.9/3.10."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vendor_sync


class StdlibRuntimeTests(unittest.TestCase):
    def test_runtime_plan_only_compares_declared_facts(self):
        from tools import runtime_plan
        manifest = runtime_plan.load_json(ROOT / "runtime-profiles.json")
        fixture = runtime_plan.load_json(ROOT / "tests/fixtures/runtime_plan/synthetic-debian13-amd64.json")
        result = runtime_plan.plan(manifest, "aoi-numeric-native", fixture)
        self.assertEqual(result["static_requirements_status"], "requirements_match")
        self.assertFalse(result["execution_ready"])
        self.assertFalse(result["measured"])
        fixture["facts"] = {}
        self.assertEqual(runtime_plan.plan(manifest, "stdlib-core", fixture)["static_requirements_status"], "unknown")

    def test_minpy_deep_dependency_cycles(self):
        from tools import minpy
        limit = sys.getrecursionlimit()
        nodes = ["n%06d" % i for i in range(max(1500, limit + 100))]
        edges = {left: {right} for left, right in zip(nodes, nodes[1:])}
        self.assertEqual(minpy._cycles(edges), [])
        edges[nodes[-1]] = {nodes[0]}
        self.assertEqual(minpy._cycles(edges), [tuple(nodes)])
        self.assertEqual(sys.getrecursionlimit(), limit)

    def test_vendor_materialization_and_rejection(self):
        data = b"VALUE = 42\n"
        entry = dict(repository="example/runtime", ref="main", commit="a" * 40,
                     source="runtime.py", destination="vendor/runtime.py",
                     blob_sha=vendor_sync.git_blob(data),
                     sha256=hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vendor.lock.json").write_text(json.dumps(
                {"schema": vendor_sync.SCHEMA, "files": [entry]}), encoding="utf-8")
            result = vendor_sync.synchronize("vendor.lock.json", root, "materialize", get=lambda url: data)
            self.assertEqual(result["changed_paths"], ["vendor/runtime.py"])
            self.assertEqual((root / "vendor/runtime.py").read_bytes(), data)
            vendor_sync.synchronize("vendor.lock.json", root, "check")
            (root / "vendor/runtime.py").write_bytes(b"tampered\n")
            with self.assertRaises(ValueError):
                vendor_sync.synchronize("vendor.lock.json", root, "check")

    def test_minpy_bundle_executes(self):
        fixture = ROOT / "tests/fixtures/minpy_widget"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "app_min.py"
            subprocess.run([sys.executable, "-S", str(ROOT / "tools/minpy.py"), "bundle",
                            "--root", str(fixture), "--entry", "app.py", "--repository", "example/widget",
                            "--commit", "a" * 40, "--skip-head-check", "--output", str(output)], check=True, timeout=30)
            subprocess.run([sys.executable, "-S", str(ROOT / "tools/minpy.py"), "check",
                            "--bundle", str(output), "--root", str(fixture), "--skip-head-check"], check=True, timeout=30)
            subprocess.run([sys.executable, "-S", "-c",
                            "import runpy,sys; assert runpy.run_path(sys.argv[1])['VALUE'] == 3", str(output)], check=True, timeout=30)

    @unittest.skipIf(sys.version_info < (3, 10), "workflow wrapper requires Python 3.10+")
    def test_workflow_trigger_behavior(self):
        import gh_workflow
        self.assertIs(gh_workflow._dispatch_trigger("on: workflow_dispatch\n"), True)
        self.assertIs(gh_workflow._dispatch_trigger("on: push\n"), False)
        self.assertIsNone(gh_workflow._dispatch_trigger("on: {workflow_dispatch: {}}\n"))
        with self.assertRaises(ValueError):
            gh_workflow._text("--unsafe", "repository")


if __name__ == "__main__":
    unittest.main()

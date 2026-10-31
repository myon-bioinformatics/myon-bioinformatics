"""Fake-only profile checks. No environment discovery or native workload runs."""
import ast
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tools import runtime_plan as runtime


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/runtime_plan"


class RuntimePlanTests(unittest.TestCase):
    def setUp(self):
        self.manifest = runtime.load_json(ROOT / "runtime-profiles.json")
        self.environment = runtime.load_json(FIXTURES / "synthetic-debian13-amd64.json")

    def plan(self, profile="aoi-numeric-native"):
        return runtime.plan(self.manifest, profile, self.environment)

    def assert_status(self, status, profile="aoi-numeric-native"):
        result = self.plan(profile)
        self.assertEqual(result["static_requirements_status"], status)
        self.assertFalse(result["execution_ready"])
        self.assertFalse(result["measured"])
        self.assertEqual(result["execution_verification"], "not_performed")
        return result

    def test_examples_match_only_declared_requirements(self):
        for name in self.manifest["profiles"]:
            with self.subTest(name=name):
                result = self.assert_status("requirements_match", name)
                self.assertEqual(result["input_kind"], "synthetic")
                self.assertEqual(set(result["evidence_gaps"]), set(runtime.GAPS))
                self.assertTrue(all(value is None for value in result["evidence"].values()))

    def test_caller_provided_is_not_host_attestation(self):
        self.environment["kind"] = "provided"
        result = self.assert_status("requirements_match")
        self.assertEqual(result["source_verification"], "not_performed")
        self.assertEqual(result["adoption"], "unchanged")

    def test_no_facts_are_unknown_not_success(self):
        self.environment = runtime.load_json(FIXTURES / "unknown.json")
        self.assert_status("unknown")
        self.assert_status("unknown", "stdlib-core")

    def test_missing_and_null_differ(self):
        self.environment["facts"]["stdlib.ctypes"] = False
        self.assert_status("missing")
        self.environment["facts"]["stdlib.ctypes"] = None
        self.assert_status("unknown")

    def test_wrong_platform_and_abi_are_unsupported(self):
        for key, value in [("os.id", "ubuntu"), ("os.release", "12"), ("os.arch", "arm64"),
                           ("os.libc", "musl"), ("native.integer_bits", 64),
                           ("python.version", "3.10.9"),
                           ("package.libblas3.version", "3.12.1-7"),
                           ("package.libblas3.arch", "arm64")]:
            with self.subTest(key=key):
                original = self.environment["facts"][key]
                self.environment["facts"][key] = value
                self.assert_status("unsupported")
                self.environment["facts"][key] = original

    def test_missing_symbols_cannot_use_native_profile(self):
        self.environment["facts"]["native.lapack_symbols"] = ["dgesdd_"]
        self.assert_status("unsupported")

    def test_optional_and_dev_are_separate_from_runtime(self):
        result = self.assert_status("requirements_match")
        self.assertEqual(result["requirements"]["optional"][0]["status"], "missing")
        self.assertEqual(result["requirements"]["development"][1]["status"], "missing")
        self.assertEqual(result["requirements"]["build"], [])

    def test_version_comparison_is_numeric(self):
        self.environment["facts"]["python.version"] = "3.9.10"
        self.assert_status("requirements_match", "stdlib-core")
        self.environment["facts"]["python.version"] = "3.8.99"
        self.assert_status("unsupported", "stdlib-core")

    def test_catalog_sources_refer_to_existing_advisory_entries(self):
        catalog = runtime.load_json(ROOT / "vendor-catalog.json")
        entries = {(item["repository"], item["source"]) for item in catalog["tools"]}
        core = self.manifest["profiles"]["stdlib-core"]
        for source in core["sources"]:
            self.assertEqual(source["ledger"], "vendor-catalog.json")
            self.assertIn((source["repository"], source["path"]), entries)
            self.assertIsNone(source["evidence_url"])
        numeric = self.manifest["profiles"]["aoi-numeric-native"]["sources"][0]
        self.assertIsNone(numeric["ledger"])
        self.assertIn("/2f6907f69856a0f1b1570a039d1feefcf6b2edea/", numeric["evidence_url"])

    def test_unknown_fields_and_missing_fields_fail_closed(self):
        for container in (self.manifest, self.manifest["profiles"]["stdlib-core"],
                          self.manifest["profiles"]["stdlib-core"]["requirements"],
                          self.manifest["profiles"]["stdlib-core"]["requirements"]["runtime"][0],
                          self.manifest["profiles"]["stdlib-core"]["sources"][0],
                          self.manifest["profiles"]["stdlib-core"]["evidence"]):
            for key in list(container):
                with self.subTest(key=key):
                    value = container.pop(key)
                    with self.assertRaises(ValueError):
                        runtime.validate_manifest(self.manifest)
                    container[key] = value
            container["command"] = "do-not-execute"
            with self.assertRaises(ValueError):
                runtime.validate_manifest(self.manifest)
            del container["command"]

    def test_invalid_manifest_shapes(self):
        for value in [None, [], {}, {"schema": "nano-profiles/2", "profiles": {}},
                      {"schema": runtime.SCHEMA, "profiles": {}},
                      {"schema": runtime.SCHEMA, "profiles": []}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.validate_manifest(value)

    def test_empty_runtime_and_duplicate_checks(self):
        lane = self.manifest["profiles"]["stdlib-core"]["requirements"]["runtime"]
        lane.append(copy.deepcopy(lane[0]))
        with self.assertRaises(ValueError):
            runtime.validate_manifest(self.manifest)
        lane.clear()
        with self.assertRaises(ValueError):
            runtime.validate_manifest(self.manifest)

    def test_duplicate_sources_and_collection_bounds(self):
        sources = self.manifest["profiles"]["stdlib-core"]["sources"]
        sources.append(copy.deepcopy(sources[0]))
        with self.assertRaises(ValueError):
            runtime.validate_manifest(self.manifest)
        sources.pop()
        lane = self.manifest["profiles"]["stdlib-core"]["requirements"]["optional"]
        lane[:] = [dict(lane[0], id="check-" + str(index)) for index in range(129)]
        with self.assertRaisesRegex(ValueError, "bounded"):
            runtime.validate_manifest(self.manifest)

    def test_symbols_and_presence_expected_values_are_strict(self):
        checks = self.manifest["profiles"]["aoi-numeric-native"]["requirements"]["runtime"]
        symbols = next(check for check in checks if check["kind"] == "contains_all")
        original = symbols["expected"]
        for value in [[], ["x", "x"], [True], "x"]:
            symbols["expected"] = value
            with self.assertRaises(ValueError):
                runtime.validate_manifest(self.manifest)
        symbols["expected"] = original
        presence = next(check for check in checks if check["kind"] == "present")
        presence["expected"] = 1
        with self.assertRaises(ValueError):
            runtime.validate_manifest(self.manifest)

    def test_no_evidence_claims_can_be_injected(self):
        for field in runtime.GAPS:
            self.manifest["profiles"]["stdlib-core"]["evidence"][field] = "verified"
            with self.assertRaises(ValueError):
                runtime.validate_manifest(self.manifest)
            self.manifest["profiles"]["stdlib-core"]["evidence"][field] = None

    def test_paths_and_moving_source_refs_are_rejected(self):
        source = self.manifest["profiles"]["stdlib-core"]["sources"][0]
        for path in ["../x.py", "/tmp/x.py", "a/../x.py", "C:\\x.py", "a//x.py", "a/./x.py",
                     "a/%2e%2e/x.py", "x.py;touch-x", "x.py\n", "x.py?query", "x.py#fragment"]:
            with self.subTest(path=path):
                source["path"] = path
                with self.assertRaises(ValueError):
                    runtime.validate_manifest(self.manifest)
        source["path"] = "node_launch.py"
        upstream = self.manifest["profiles"]["aoi-numeric-native"]["sources"][0]
        original = upstream["evidence_url"]
        for url in [original.replace("2f6907f69856a0f1b1570a039d1feefcf6b2edea", "main"),
                    original.replace("/Aoi/", "/other/"), original + "?x=1",
                    "file:///tmp/code.py", "https://example.com/code.py"]:
            with self.subTest(url=url):
                upstream["evidence_url"] = url
                with self.assertRaises(ValueError):
                    runtime.validate_manifest(self.manifest)

    def test_invalid_profile_selection_is_not_a_path(self):
        for name in ["unknown", "../runtime-profiles.json", "/tmp/x.py", "x; echo injected", None]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.plan(name)

    def test_invalid_fact_types_and_code_are_rejected(self):
        for key, values in {"python.version": [True, 3.13, "3.13rc1", "3.13;touch /tmp/injected", "3", []],
                            "native.integer_bits": [True, "32", -1],
                            "stdlib.ctypes": ["true", 1, []],
                            "native.blas_symbols": ["cblas_sgemm", ["x", "x"], [32]]}.items():
            original = self.environment["facts"][key]
            for value in values:
                with self.subTest(key=key, value=value):
                    self.environment["facts"][key] = value
                    with self.assertRaises(ValueError):
                        self.plan()
            self.environment["facts"][key] = original

    def test_unknown_and_incomplete_environment_shape(self):
        for key in list(self.environment):
            value = self.environment.pop(key)
            with self.assertRaises(ValueError):
                self.plan()
            self.environment[key] = value
        self.environment["facts"]["command"] = "do-not-execute"
        with self.assertRaises(ValueError):
            self.plan()

    def test_check_kind_and_inconsistent_fact_types(self):
        check = self.manifest["profiles"]["stdlib-core"]["requirements"]["runtime"][0]
        check["kind"] = "shell"
        with self.assertRaises(ValueError):
            runtime.validate_manifest(self.manifest)
        check["kind"], check["expected"] = "present", True
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            runtime.validate_manifest(self.manifest)

    def test_plan_is_deterministic_and_does_not_read_or_mutate(self):
        before = copy.deepcopy((self.manifest, self.environment))
        with patch("builtins.open", side_effect=AssertionError("unexpected read")), \
                patch.object(Path, "open", side_effect=AssertionError("unexpected read")):
            first = self.plan()
            self.assertEqual(first, self.plan())
        self.assertEqual(before, (self.manifest, self.environment))

    def test_module_has_no_execution_or_discovery_dependencies(self):
        tree = ast.parse((ROOT / "tools/runtime_plan.py").read_text(encoding="utf-8"))
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(imports, {"__future__", "argparse", "json", "pathlib", "re", "sys"})
        calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertFalse(calls & {"eval", "exec", "compile", "__import__"})

    def test_json_duplicate_nonfinite_nesting_and_size_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            for data in [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'\xff',
                         b'[' * 2000 + b']' * 2000, b' ' * (runtime.MAX_BYTES + 1)]:
                with self.subTest(prefix=data[:20]):
                    path.write_bytes(data)
                    with self.assertRaises(ValueError):
                        runtime.load_json(path)

    def test_non_regular_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                runtime.load_json(directory)
            with self.assertRaises(ValueError):
                runtime.load_json(Path(directory) / "missing.json")

    def test_symlink_input_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, link = root / "target.json", root / "link.json"
            target.write_text("{}", encoding="utf-8")
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("test filesystem cannot create symlinks")
            with self.assertRaises(ValueError):
                runtime.load_json(link)

    def test_nesting_limit_ignores_quoted_brackets_and_escapes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quoted.json"
            value = {"text": '[' * 100 + '\\"' + '}' * 100}
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(runtime.load_json(path), value)

    def test_cli_exit_contract_with_only_explicit_fixture_inputs(self):
        for fixture, expected_code, expected_status in [("synthetic-debian13-amd64.json", 0, "requirements_match"),
                                                       ("unknown.json", 1, "unknown")]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = runtime.main(["--profiles", str(ROOT / "runtime-profiles.json"),
                                     "--profile", "aoi-numeric-native", "--environment", str(FIXTURES / fixture)])
            self.assertEqual(code, expected_code)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["static_requirements_status"], expected_status)
            self.assertFalse(result["execution_ready"])
            self.assertEqual(stderr.getvalue(), "")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = runtime.main(["--profiles", str(ROOT / "runtime-profiles.json"),
                                 "--profile", "../code.py", "--environment", str(FIXTURES / "unknown.json")])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["schema"], "nano-plan-error/1")


if __name__ == "__main__":
    unittest.main()

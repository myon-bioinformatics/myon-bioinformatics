"""Stdlib-only preparation tests; no installed SDK or listener is exercised."""
import ast
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import prepare_mcp_comparison as bench


class PreparationTests(unittest.TestCase):
    def test_source_receipt(self):
        self.assertEqual(bench.verify()["status"], "verified")

    def test_receipt_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_bytes(b"changed")
            (root / "source-receipt.json").write_text(json.dumps({"revision": bench.REVISION, "files": [
                {"local_path": "a.py", "bytes": 7, "sha256": bench.digest(b"correct")}
            ]}))
            with self.assertRaisesRegex(ValueError, "source changed"):
                bench.verify(root)

    def test_exact_local_endpoint(self):
        self.assertEqual(bench.endpoint("http://127.0.0.1:8765/mcp"), ("127.0.0.1", 8765))

    def test_external_or_ambiguous_endpoints_rejected(self):
        for url in ("https://127.0.0.1:8765/mcp", "http://localhost:8765/mcp",
                    "http://127.0.0.1/mcp", "http://example.com:80/mcp",
                    "http://u:p@127.0.0.1:8765/mcp", "http://127.0.0.1:8765/mcp?x=1",
                    "http://127.0.0.1:8765/mcp#x", "http://127.0.0.1:0/mcp"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bench.endpoint(url)

    def test_client_network_policy_is_exact_target(self):
        target = ("127.0.0.1", 8765)
        self.assertTrue(bench.audit_policy("socket.connect", (None, target), target))
        self.assertFalse(bench.audit_policy("socket.connect", (None, ("127.0.0.1", 8766)), target))
        self.assertFalse(bench.audit_policy("socket.connect", (None, ("1.1.1.1", 8765)), target))
        self.assertTrue(bench.audit_policy("socket.getaddrinfo", ("127.0.0.1", 8765), target))
        self.assertFalse(bench.audit_policy("socket.getaddrinfo", ("example.com", 8765), target))

    def test_server_policy_denies_all_outbound_and_public_bind(self):
        self.assertFalse(bench.audit_policy("socket.connect", (None, ("127.0.0.1", 1)), None))
        self.assertFalse(bench.audit_policy("socket.getaddrinfo", ("example.com", 443), None))
        self.assertTrue(bench.audit_policy("socket.bind", (None, ("127.0.0.1", 0)), None))
        self.assertFalse(bench.audit_policy("socket.bind", (None, ("0.0.0.0", 0)), None))

    def test_process_spawn_denied(self):
        for event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork"):
            self.assertFalse(bench.audit_policy(event, (), None))

    def test_only_mock_cases_and_one_explicit_error_name(self):
        names = {row[1] for row in bench.CASES}
        self.assertEqual(names, {"find_municipalities", "find_stations", "find_transaction_prices", "find_transaction_price"})
        self.assertEqual(len(bench.CASES), 8)

    def test_all_metrics_unknown_before_execution(self):
        plan = bench.plan()
        for name in ("selected_environment_trial", "installed_packages", "install_seconds", "venv_bytes", "output_parity", "runtime_seconds", "rss_bytes"):
            self.assertIsNone(plan[name])

    def test_no_installer_entrypoint(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            bench.main(["install"])
        self.assertEqual(error.exception.code, 2)

    def test_loopback_gate_precedes_verification_and_worker(self):
        with mock.patch.object(bench, "verify", side_effect=AssertionError("must not run")):
            for args in (["serve", "--ready", "ready.json", "--log", "server.jsonl"],
                         ["client", "--url", "http://127.0.0.1:1/mcp", "--output", "out.json", "--run-tag", "fake"]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    bench.main(args)
                self.assertEqual(error.exception.code, 2)

    def test_runtime_slice_contains_no_import_substitutions(self):
        src = bench.ROOT / "runtime-source" / "src" / "mcp_toolcall_lab"
        for name in bench.CLIENT_FILES:
            tree = ast.parse((src / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertNotIn("fastmcp", [item.name.split(".")[0] for item in node.names])
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual((node.module or "").split(".")[0], "fastmcp")
        init = ast.parse((src / "__init__.py").read_text())
        server_imports = [node for node in ast.walk(init) if isinstance(node, ast.ImportFrom) and node.module == "server"]
        self.assertEqual(len(server_imports), 1)
        lazy = next(node for node in init.body if isinstance(node, ast.FunctionDef) and node.name == "__getattr__")
        self.assertIn(server_imports[0], list(ast.walk(lazy)))

    def test_canonical_preserves_every_field(self):
        original = {"content": [{"text": "[]"}], "isError": False, "_meta": {"x": None}}
        self.assertEqual(json.loads(bench.canonical(original)), original)


if __name__ == "__main__":
    unittest.main()

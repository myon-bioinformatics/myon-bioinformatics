"""Fake process/receipt tests only: never install or create a listener."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import measure_mcp as bench


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout):
        self.waited = True
        self.returncode = -15
        return self.returncode


class OrchestratorTests(unittest.TestCase):
    def test_identity_guard_detects_source_or_harness_change(self):
        with mock.patch.object(bench, 'identities', return_value={'sha': 'current'}):
            bench.verify_unchanged({'sha': 'current'})
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                bench.verify_unchanged({'sha': 'old'})

    def test_worker_environment_has_no_credentials_proxies_or_model_flags(self):
        with mock.patch.dict(bench.os.environ, {'OPENAI_API_KEY': 'fake', 'HTTP_PROXY': 'fake',
                                               'MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA': '1', 'PYTHONPATH': '/fake'}):
            env = bench.workload_env()
        for key in ('OPENAI_API_KEY', 'HTTP_PROXY', 'MCP_TOOLCALL_LAB_LIVE_WIKIPEDIA', 'PYTHONPATH'):
            self.assertNotIn(key, env)
        self.assertEqual(env['HOME'], str(bench.ROOT / 'workload-home'))

    def test_exclusive_gate_precedes_work(self):
        with mock.patch.object(bench, 'install', side_effect=AssertionError('must not run')):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bench.main(['install'])

    def test_workload_requires_explicit_trial(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench.main(['workloads', '--exclusive-window'])

    def test_symlink_target_excluded_from_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'outside').write_bytes(b'x' * 1000)
            (path / 'env').mkdir()
            (path / 'env' / 'own').write_bytes(b'ab')
            (path / 'env' / 'link').symlink_to(path / 'outside')
            result = bench.tree_bytes(path / 'env')
            self.assertEqual(result['apparent_regular_bytes'], 2)
            self.assertEqual(result['symlinks'], 1)

    def test_partial_launch_error_reaps_server_and_running_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'fake'
            server, client = FakeProcess(1), FakeProcess(2)
            calls = []

            def spawn(command, **kwargs):
                calls.append(command)
                if len(calls) == 1:
                    (folder / 'server-ready.json').write_text(json.dumps({'url': 'http://127.0.0.1:1234/mcp'}))
                    return server
                if len(calls) == 2:
                    return client
                raise OSError('injected launch failure')

            with mock.patch.object(bench.subprocess, 'Popen', side_effect=spawn), mock.patch.object(bench, 'rss', return_value=0):
                with self.assertRaisesRegex(OSError, 'injected'):
                    bench.batch(folder, Path('/fake/python'), Path('/fake/python'), 2)
            self.assertTrue(server.terminated and server.waited)
            self.assertTrue(client.terminated and client.waited)
            receipt = json.loads((folder / 'batch-receipt.json').read_text())
            self.assertEqual(receipt['status'], 'failed')
            self.assertEqual(receipt['all_clients_final_exit_codes'], [-15])

    def test_trace_validation_checks_inputs_outcomes_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            rows = []
            for name, tool, args, is_error in bench.core.CASES:
                for mode in ('direct', 'chat'):
                    rows.append({'event': 'tools/call', 'tool': tool, 'arguments': args,
                                 'outcome': 'error' if is_error else 'success',
                                 'meta': {'source': mode, 'trace_id': f'nano-fake-{name}-{mode}'}})
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            self.assertEqual(bench.validate_calls(path, ['fake'])['calls'], 16)
            rows[-1] = rows[0]
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            with self.assertRaisesRegex(AssertionError, 'duplicate'):
                bench.validate_calls(path, ['fake'])

    def test_expected_oracle_does_not_call_application(self):
        self.assertEqual(bench.core.EXPECTED_SUCCESS['municipality'][0]['code'], '90001')
        self.assertEqual(bench.core.EXPECTED_SUCCESS['transaction'][0]['price_yen'], 52000000)
        self.assertEqual(bench.core.EXPECTED_SUCCESS['transaction'][0]['year'], 2025)
        self.assertEqual(bench.core.EXPECTED_SUCCESS['empty'], [])


if __name__ == '__main__':
    unittest.main()

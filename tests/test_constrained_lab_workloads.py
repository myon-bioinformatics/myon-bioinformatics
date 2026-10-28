"""Offline regression checks; no network, fetching, or third-party packages."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import constrained_lab_workloads as w


class WorkloadHarnessTests(unittest.TestCase):
    def test_default_fails_closed_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(manifest=str(w.ROOT/'fixtures/constrained-workloads/manifest.json'),
                                      fixture=str(w.ROOT/'fixtures/constrained-workloads/input.json'),
                                      staged=tmp, exploratory=False, address_space_mib=256, output=tmp+'/result.jsonl')
            with patch.object(w, 'validate_sources'), patch.object(w, 'verify_budget', side_effect=ValueError), patch.object(w, 'trial') as launch:
                with self.assertRaisesRegex(ValueError, 'budget unavailable'):
                    w.run(args)
                launch.assert_not_called()
            self.assertFalse(Path(args.output).exists())

    def test_fixture_tampering_fails_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp)/'input.json'; bad.write_text('{}')
            args = argparse.Namespace(manifest=str(w.ROOT/'fixtures/constrained-workloads/manifest.json'),
                                      fixture=str(bad), staged=tmp)
            with self.assertRaisesRegex(ValueError, 'fixture digest mismatch'):
                w.run(args)

    def test_wrong_pin_and_dirty_sources_rejected(self):
        manifest = {'sources': {lane: {'sha': 'a'*40} for lane in w.LANES}}
        with patch.object(w, 'git', return_value='b'*40):
            with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
                w.validate_sources(Path('/unused'), manifest)
        with patch.object(w, 'git', side_effect=['a'*40, ' M x.py']):
            with self.assertRaisesRegex(ValueError, 'must be clean'):
                w.validate_sources(Path('/unused'), manifest)

    def test_unavailable_audit_has_no_worker_metrics_or_success(self):
        with patch.object(w.subprocess, 'Popen') as popen, patch.object(w, 'wait_usage', return_value=(1, None)):
            popen.return_value.pid = 123
            row = w.trial('xprobe', Path('/unused'), Path('/unused.json'), True)
        self.assertFalse(row['correct'])
        self.assertIsNone(row['worker'])
        self.assertIsNone(row['external_total'])
        self.assertIsNone(row['launch_cpu_seconds'])

    def test_explicit_as_guard_is_forwarded_to_worker(self):
        with patch.object(w.subprocess, 'Popen') as popen, patch.object(w, 'wait_usage', return_value=(1, None)):
            popen.return_value.pid = 123
            w.trial('xprobe', Path('/unused'), Path('/unused.json'), False, 256)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('--address-space-mib')+1], '256')

    def test_failure_receipt_keeps_frames_and_explicit_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)/'fixture.json'; fixture.write_text('{}')
            receipt = Path(tmp)/'receipt.json'
            with patch.object(w.resource, 'setrlimit') as limit, patch.object(w, 'block_network', return_value='test'), patch.object(w, 'exercise', side_effect=MemoryError):
                rc = w.worker('mcp-toolcall-lab', Path('/unused'), fixture, receipt, 192)
            report = json.loads(receipt.read_text())
        self.assertEqual(rc, 1)
        self.assertFalse(report['correct'])
        self.assertEqual(report['error_type'], 'MemoryError')
        self.assertNotIn('result_sha256', report)
        self.assertTrue(report['error_frames'])
        self.assertEqual(report['address_space_limit_bytes'], 192*1024**2)
        limit.assert_any_call(w.resource.RLIMIT_AS, (192*1024**2,)*2)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux wait4 metrics')
    def test_wait4_per_child_usage(self):
        proc = subprocess.Popen([sys.executable, '-S', '-c', 'sum(range(10000))'])
        rc, usage = w.wait_usage(proc, 5)
        self.assertEqual(rc, 0)
        self.assertGreater(usage.ru_maxrss, 0)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux seccomp')
    def test_kernel_network_denial(self):
        # Filter installed only in a disposable child, never in the test runner.
        command = 'from tools.constrained_lab_workloads import block_network; print(block_network())'
        proc = subprocess.run([sys.executable, '-S', '-c', command], capture_output=True, text=True)
        if 'cannot open shared object file' in proc.stderr:
            self.skipTest('optional host libseccomp unavailable')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('seccomp-deny', proc.stdout)


if __name__ == '__main__':
    unittest.main()

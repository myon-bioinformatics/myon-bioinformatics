"""Fatal dpkg observation must never trigger installation."""
import json, subprocess, unittest
import test_nano_apt as base


class QueryFailureTests(unittest.TestCase):
    setUp = base.NanoAptTests.setUp
    lst = base.NanoAptTests.lst
    call = base.NanoAptTests.call

    def test_fatal_query_blocks_report_install_and_download(self):
        for mode in ([], ['--install'], ['--download-only']):
            calls = []
            def fail(argv, **kwargs):
                calls.append(argv)
                if not argv[0].endswith('dpkg-query'):
                    self.fail('apt must not run after observation failure')
                return subprocess.CompletedProcess(argv, 2, stdout='', stderr='database unreadable')
            with self.subTest(mode=mode):
                code, report = self.call(['--list', self.lst('packages.txt', 'git\n')] + mode, fail)
                self.assertEqual(code, 2)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['executed'], [])
                self.assertIn('dpkg-query failed', report['error'])
                self.assertEqual(len(calls), 1)

    def test_post_install_query_failure_preserves_before_and_receipt(self):
        queries = []
        def run(argv, **kwargs):
            if argv[0].endswith('dpkg-query'):
                queries.append(argv)
                return subprocess.CompletedProcess(
                    argv, 0 if len(queries) == 1 else 2,
                    stdout='git ii 1.0\n' if len(queries) == 1 else '', stderr='')
            return subprocess.CompletedProcess(argv, 0)
        receipt = self.root / 'receipt.json'
        code, report = self.call(['--list', self.lst('packages.txt', 'git=2.0\n'),
                                  '--install', '--receipt', str(receipt)], run)
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['before'], {'git': '1.0'})
        self.assertEqual(len(report['executed']), 2)
        self.assertEqual(len(queries), 2)
        self.assertEqual(json.loads(receipt.read_text()), report)

    def test_failed_download_query_publishes_failed_state(self):
        state = self.root / 'download.json'
        def run(argv, **kwargs):
            if not argv[0].endswith('dpkg-query'):
                self.fail('no apt command expected')
            return subprocess.CompletedProcess(argv, 2, stdout='', stderr='')
        code, report = self.call(['--list', self.lst('packages.txt', 'git\n'),
                                  '--download-only', '--state', str(state)], run)
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(json.loads(state.read_text())['status'], 'failed')
        self.assertEqual(report['executed'], [])

    def test_query_spawn_error_is_reported_without_apt(self):
        for exception in (FileNotFoundError('executable disappeared'),
                          PermissionError('executable denied')):
            for mode in ([], ['--install'], ['--download-only']):
                calls = []
                def run(argv, **kwargs):
                    calls.append(argv)
                    if not argv[0].endswith('dpkg-query'):
                        self.fail('apt must not run when observation cannot start')
                    raise exception
                with self.subTest(exception=type(exception).__name__, mode=mode):
                    code, report = self.call(['--list', self.lst('packages.txt', 'git\n')] + mode, run)
                    self.assertEqual(code, 2)
                    self.assertEqual(report['status'], 'failed')
                    self.assertIn('dpkg-query could not start', report['error'])
                    self.assertEqual(report['executed'], [])
                    self.assertEqual(len(calls), 1)

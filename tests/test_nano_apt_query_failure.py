"""Fatal dpkg observation must never trigger installation."""
import subprocess
import unittest
from unittest.mock import patch
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

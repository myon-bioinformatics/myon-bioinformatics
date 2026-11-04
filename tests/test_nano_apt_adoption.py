"""Offline adoption experiment for Ironmate #203 at 77b4a584795c.

Every command is intercepted; these tests never invoke host apt or dpkg.
"""
import subprocess
from unittest.mock import patch

import test_nano_apt as base

Fake, nano = base.Fake, base.nano


class AdoptionTests(base.NanoAptTests):
    def groups(self):
        return [self.lst('fonts.txt', 'fonts-noto-cjk\n'),
                self.lst('uno.txt', 'python3-uno\nlibreoffice-impress\n')]

    def test_exact_groups_and_duplicate_elimination(self):
        fonts, uno = self.groups()
        self.assertEqual(list(nano.read_list(fonts)), ['fonts-noto-cjk'])
        self.assertEqual(list(nano.read_list(uno)), ['python3-uno', 'libreoffice-impress'])
        self.assertEqual(list(nano.merge([fonts, uno, fonts])),
                         ['fonts-noto-cjk', 'python3-uno', 'libreoffice-impress'])

    def test_separate_calls_preserve_groups_but_update_twice(self):
        fake = Fake({})
        for group in self.groups():
            self.assertEqual(self.call(['--list', group, '--install'], fake)[0], 0)
        apt = [argv for argv, _ in fake.calls if argv[0].endswith('apt-get')]
        self.assertEqual(sum(argv[-1] == 'update' for argv in apt), 2)
        self.assertEqual(apt[1][-1:], ['fonts-noto-cjk'])
        self.assertEqual(apt[3][-2:], ['libreoffice-impress', 'python3-uno'])

    def test_merged_call_updates_once_but_sorts_across_groups(self):
        fonts, uno = self.groups()
        fake = Fake({})
        self.assertEqual(self.call(['--list', fonts, '--list', uno, '--install'], fake)[0], 0)
        apt = [argv for argv, _ in fake.calls if argv[0].endswith('apt-get')]
        self.assertEqual(len(apt), 2)
        self.assertEqual(apt[-1][-3:], ['fonts-noto-cjk', 'libreoffice-impress', 'python3-uno'])

    def test_second_run_never_repeats_apt(self):
        fonts, uno = self.groups()
        fake = Fake({})
        args = ['--list', fonts, '--list', uno, '--install']
        self.assertEqual(self.call(args, fake)[0], 0)
        fake.calls.clear()
        self.assertEqual(self.call(args, fake)[0], 0)
        self.assertTrue(all(argv[0].endswith('dpkg-query') for argv, _ in fake.calls))

    def test_install_failure_is_code_three(self):
        class InstallFailure(Fake):
            def __call__(self, argv, **kw):
                if 'install' in argv:
                    self.calls.append((list(argv), kw))
                    return subprocess.CompletedProcess(argv, 100)
                return super().__call__(argv, **kw)
        fake = InstallFailure({})
        code, result = self.call(['--list', self.groups()[0], '--install'], fake)
        self.assertEqual((code, result['status']), (3, 'failed'))
        self.assertEqual(len(fake.calls), 3)

    def test_empty_list_currently_succeeds(self):
        code, result = self.call(['--list', self.lst('empty.txt', '# comment\n'), '--install'], Fake({}))
        self.assertEqual((code, result['status']), (0, 'satisfied'))

    def test_query_failure_is_currently_treated_as_missing(self):
        def broken(argv, **kw):
            return subprocess.CompletedProcess(argv, 2, stdout='', stderr='database error')
        code, result = self.call(['--list', self.groups()[0]], broken)
        self.assertEqual((code, result['status']), (1, 'missing'))

    def test_real_dpkg_query_is_read_only(self):
        real_run = subprocess.run
        def readonly(argv, **kw):
            if not argv[0].endswith('dpkg-query'):
                raise RuntimeError('host mutation prohibited')
            return real_run(argv, **kw)
        with patch.object(nano.subprocess, 'run', readonly):
            super().test_real_dpkg_query_is_read_only()


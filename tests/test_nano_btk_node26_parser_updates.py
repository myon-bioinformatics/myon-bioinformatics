"""Additive Node26 parser profiles: exact inputs and fail-closed projections."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('nano_parser_updates', ROOT / 'tools/nano_node.py')
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)

class ParserProfileTests(unittest.TestCase):
    def profiles(self):
        for label, feature in [('marked', nano.BTK_NODE26_MARKED_FEATURE), ('parsers', nano.BTK_NODE26_PARSERS_FEATURE)]:
            source = ROOT / 'tests/fixtures' / ('btk-node26-' + label)
            manifest = json.loads((source / 'package.json').read_bytes())
            lock = json.loads((source / 'package-lock.json').read_bytes())
            commit, blobs, selected, paths = nano.BTK_NODE26_PROFILES[feature]
            yield label, feature, source, manifest, lock, commit, blobs, selected, paths

    @staticmethod
    def projected(lock, selected, paths):
        result = copy.deepcopy(lock)
        result['name'] = 'browser-test-kit'
        result['packages'] = {k: v for k, v in result['packages'].items() if not k or k in paths}
        result['packages']['']['devDependencies'] = selected
        return result

    def test_exact_snapshots_and_closures(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            with self.subTest(profile=label):
                self.assertRegex(commit, r'^[0-9a-f]{40}$')
                nano.read_snapshot(source, blobs=blobs)
                nano.validate_original(manifest, lock, feature=feature)
                self.assertEqual(len(paths), 30)
                self.assertEqual(manifest['engines'], {'node': '>=26'})
                self.assertEqual(lock['packages']['node_modules/marked']['version'], '18.1.2')
                self.assertEqual(lock['packages']['node_modules/linkedom']['version'], '0.18.14' if label == 'parsers' else '0.18.13')
                self.assertEqual(lock['packages']['node_modules/exceljs']['version'], '4.4.0')
                self.assertNotIn('node_modules/exceljs', paths)
                self.assertNotIn('node_modules/canvas', paths)
                self.assertEqual('node_modules/@acemir/cssom' in paths, label == 'parsers')
                self.assertEqual('node_modules/cssom' in paths, label == 'marked')
                projected = self.projected(lock, selected, paths)
                nano.validate_projection(lock, projected, feature=feature)
                for path in paths:
                    self.assertEqual(projected['packages'][path], lock['packages'][path])

    def test_changed_inputs_and_wrong_profile_are_rejected(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            with self.subTest(profile=label), tempfile.TemporaryDirectory() as tmp:
                changed = Path(tmp)
                for name in blobs:
                    (changed / name).write_bytes((source / name).read_bytes())
                with (changed / 'package.json').open('a') as stream:
                    stream.write(' ')
                with self.assertRaisesRegex(ValueError, 'blob mismatch'):
                    nano.read_snapshot(changed, blobs=blobs)
                with self.assertRaisesRegex(ValueError, 'blob mismatch'):
                    nano.read_snapshot(source, blobs=nano.BTK_NODE26_SOURCE_BLOBS)

    def test_projection_rejects_mutated_retained_records(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            for mode in ('version', 'integrity', 'peer', 'engine', 'extra', 'missing'):
                projected = self.projected(lock, selected, paths)
                if mode == 'version': projected['packages']['node_modules/marked']['version'] = '18.1.1'
                if mode == 'integrity': projected['packages']['node_modules/marked']['integrity'] = 'sha512-wrong'
                if mode == 'peer': projected['packages']['node_modules/linkedom']['peerDependenciesMeta']['canvas']['optional'] = False
                if mode == 'engine': projected['packages']['']['engines'] = {'node': '>=24'}
                if mode == 'extra': projected['packages']['node_modules/canvas'] = {}
                if mode == 'missing': del projected['packages']['node_modules/marked']
                with self.subTest(profile=label, mode=mode), self.assertRaises(ValueError):
                    nano.validate_projection(lock, projected, feature=feature)

    def test_original_rejects_wrong_engine_and_peer(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            for mode in ('engine', 'peer'):
                m, l = copy.deepcopy(manifest), copy.deepcopy(lock)
                if mode == 'engine': m['engines'] = {'node': '>=24'}
                if mode == 'peer': l['packages']['node_modules/linkedom']['peerDependencies']['canvas'] = '*'
                with self.subTest(profile=label, mode=mode), self.assertRaises(ValueError):
                    nano.validate_original(m, l, feature=feature)

    def test_dependency_only_required_before_npm(self):
        for label, feature, source, *_ in self.profiles():
            with self.subTest(profile=label), tempfile.TemporaryDirectory() as tmp, patch.object(nano.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'requires --dependencies-only'):
                    nano.prepare(source, Path(tmp), feature=feature)
                run.assert_not_called()

    def test_prepare_receipts_do_not_claim_install_or_copy_source(self):
        for label, feature, source, manifest, lock, commit, blobs, selected, paths in self.profiles():
            def fake_npm(argv, **kwargs):
                if argv[1:] == ['--version']:
                    return subprocess.CompletedProcess(argv, 0, '11.9.0-test\n', '')
                self.assertEqual(argv[1:7], ['install', '--package-lock-only', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'])
                project = Path(kwargs['cwd'])
                self.assertEqual({p.name for p in project.iterdir()}, {'package.json', 'package-lock.json'})
                (project / 'package-lock.json').write_text(json.dumps(self.projected(lock, selected, paths)))
                return subprocess.CompletedProcess(argv, 0, '', '')
            with self.subTest(profile=label), tempfile.TemporaryDirectory() as tmp, patch.object(nano.shutil, 'which', return_value=sys.executable), patch.object(nano.subprocess, 'run', side_effect=fake_npm):
                output = nano.prepare(source, Path(tmp), feature=feature, dependencies_only=True)
                receipt = json.loads((output / 'receipt.json').read_text())
                self.assertEqual(receipt['source_commit'], commit)
                self.assertEqual(receipt['source_git_blobs'], blobs)
                self.assertEqual(receipt['candidate_locked_package_records'], 30)
                self.assertEqual(receipt['npm_root_metadata_changes']['engines'], {'node': '>=26'})
                self.assertIsNone(receipt['installed_package_count'])
                self.assertFalse(receipt['application_source_staged'])
                self.assertFalse((output / 'browser-test-kit/node_modules').exists())

if __name__ == '__main__':
    unittest.main()

"""Node26-only snapshot contracts; fake npm, no installs or network."""
import copy, importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path; from unittest.mock import patch

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('nano_node26', ROOT / 'tools/nano_node.py')
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)
SOURCE = ROOT / 'tests/fixtures/btk-node26'


class Node26Tests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((SOURCE / 'package.json').read_bytes())
        self.lock = json.loads((SOURCE / 'package-lock.json').read_bytes())

    def projected(self):
        value = copy.deepcopy(self.lock)
        value['name'] = 'browser-test-kit'
        value['packages'] = {k: v for k, v in value['packages'].items() if not k or k in nano.BTK_NODE26_EXPECTED_PATHS}
        value['packages']['']['devDependencies'] = nano.BTK_NODE26_SELECTED
        return value

    def test_exact_inputs_and_unchanged_dependency_versions(self):
        nano.read_snapshot(SOURCE, blobs=nano.BTK_NODE26_SOURCE_BLOBS)
        nano.validate_original(self.manifest, self.lock, feature=nano.BTK_NODE26_FEATURE)
        self.assertEqual(nano.BTK_NODE26_SELECTED, nano.BTK_SELECTED)
        self.assertEqual(nano.BTK_NODE26_EXPECTED_PATHS, nano.BTK_EXPECTED_PATHS)
        self.assertEqual(len(nano.BTK_NODE26_EXPECTED_PATHS), 30)
        self.assertEqual(self.lock['packages']['node_modules/marked']['version'], '18.1.1')
        self.assertEqual(self.lock['packages']['node_modules/linkedom']['version'], '0.18.13')
        self.assertEqual(self.lock['packages']['node_modules/exceljs']['version'], '4.4.0')

    def test_historical_selector_remains_exact_and_rejects_node26_metadata(self):
        self.assertEqual(nano.BTK_SOURCE_BLOBS['package.json'], '9dbada6ad25af5051818d4399796d8d2cfc400c6')
        self.assertEqual(nano.BTK_SOURCE_BLOBS['package-lock.json'], 'b0191e266c3516596cb4c6a9ad69b47628551145')
        with self.assertRaisesRegex(ValueError, 'blob mismatch'):
            nano.read_snapshot(SOURCE, blobs={k: nano.BTK_SOURCE_BLOBS[k] for k in nano.BTK_NODE26_SOURCE_BLOBS})

    def test_projection_preserves_full_records_and_node26_floor(self):
        projected = self.projected()
        nano.validate_projection(self.lock, projected, feature=nano.BTK_NODE26_FEATURE)
        self.assertEqual(projected['packages']['']['engines'], {'node': '>=26'})
        for path, value in projected['packages'].items():
            if path:
                self.assertEqual(value, self.lock['packages'][path])

    def test_changed_versions_peer_or_engine_rejected(self):
        for mode in ('version', 'peer', 'engines', 'extra'):
            projected = self.projected()
            if mode == 'version': projected['packages']['node_modules/marked']['version'] = '18.1.2'
            if mode == 'peer': projected['packages']['node_modules/linkedom']['peerDependenciesMeta']['canvas']['optional'] = False
            if mode == 'engines': projected['packages']['']['engines'] = {'node': '>=24'}
            if mode == 'extra': projected['packages']['node_modules/canvas'] = {}
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                nano.validate_projection(self.lock, projected, feature=nano.BTK_NODE26_FEATURE)

    def test_original_node_floor_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['engines'] = {'node': '>=24'}
        with self.assertRaisesRegex(ValueError, 'Node 26 engines'):
            nano.validate_original(manifest, self.lock, feature=nano.BTK_NODE26_FEATURE)

    def test_requires_dependency_only_and_ignores_no_guard(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(nano.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'requires --dependencies-only'):
                nano.prepare(SOURCE, Path(tmp), feature=nano.BTK_NODE26_FEATURE)
            run.assert_not_called()

    def test_prepare_receipt_retains_real_source_and_no_install_claim(self):
        def fake_npm(argv, **kwargs):
            if argv[1:] == ['--version']:
                return subprocess.CompletedProcess(argv, 0, '11.9.0-test\n', '')
            self.assertEqual(argv[1:7], ['install', '--package-lock-only', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'])
            project = Path(kwargs['cwd'])
            self.assertEqual({p.name for p in project.iterdir()}, {'package.json', 'package-lock.json'})
            (project / 'package-lock.json').write_text(json.dumps(self.projected()))
            return subprocess.CompletedProcess(argv, 0, '', '')
        with tempfile.TemporaryDirectory() as tmp, patch.object(nano.shutil, 'which', return_value=sys.executable), patch.object(nano.subprocess, 'run', side_effect=fake_npm):
            output = nano.prepare(SOURCE, Path(tmp), feature=nano.BTK_NODE26_FEATURE, dependencies_only=True)
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertEqual(receipt['source_commit'], '8de4a8c145a1361c477c67e87f0e74a1c45faa50')
            self.assertEqual(receipt['npm_root_metadata_changes']['engines'], {'node': '>=26'})
            self.assertEqual(receipt['candidate_locked_package_records'], 30)
            self.assertIsNone(receipt['installed_bytes'])
            self.assertFalse(receipt['application_source_staged'])
            self.assertFalse((output / 'browser-test-kit/node_modules').exists())


if __name__ == '__main__':
    unittest.main()

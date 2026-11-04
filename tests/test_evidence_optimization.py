"""Real evidence guards must reject invalid inputs when Python removes assertions."""
import ast
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/evidence/nano-features'


class EvidenceOptimizationTests(unittest.TestCase):
    def test_measurement_rejects_failed_install_listing(self):
        for optimized in (False, True):
            for folder, script in [('2026-11-01-ironmate', 'measure_node.py'), ('2026-11-01-browser-test-kit', 'measure_btk.py')]:
                with self.subTest(optimized=optimized, script=script):
                    code = r'''
import json, runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import MagicMock, patch
source = Path(sys.argv[1])
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    script = base / source.name
    script.write_bytes(source.read_bytes())
    prepared = base / 'prepared'
    prepared.mkdir()
    (base / 'measurement-prepared.txt').write_text(str(prepared))
    (base / 'run_btk_fixture.py').write_text('fixture')
    (base / 'prototype/tools').mkdir(parents=True)
    (base / 'prototype/tools/nano_node.py').write_text('fixture')
    snapshot = {'package.json': b'{"dependencies":{}}', 'package-lock.json': b'{"packages":{}}'}
    nano = types.SimpleNamespace(COMMIT='fixed', BTK_COMMIT='fixed', BTK_FEATURE='fixed',
                                 BTK_SOURCE_BLOBS={}, read_snapshot=lambda *a, **kw: snapshot, validate_original=lambda *a, **kw: None)
    g = runpy.run_path(str(script))['main'].__globals__
    g['platform'].platform()
    def run(args, **kwargs):
        if args[1] == 'ci': return types.SimpleNamespace(returncode=0, stdout='', stderr='')
        if args[1] == 'ls': return types.SimpleNamespace(returncode=17, stdout='not-json', stderr='listing rejected sentinel')
        raise RuntimeError('unexpected subprocess')
    with patch.object(g['importlib'].util, 'spec_from_file_location', return_value=types.SimpleNamespace(loader=MagicMock())), \
         patch.object(g['importlib'].util, 'module_from_spec', return_value=nano), \
         patch.object(g['subprocess'], 'check_output', return_value='test-runtime'), \
         patch.object(g['subprocess'], 'run', side_effect=run), \
         patch.object(g['shutil'], 'which', return_value='/fake/npm'), \
         patch.object(sys, 'argv', [str(script), '--execute', '--exclusive-window']):
        try: g['main']()
        except AssertionError as exc:
            if str(exc) != 'listing rejected sentinel': raise
        else: raise RuntimeError('failed listing accepted')
    work = Path((base / 'latest-measurement.txt').read_text().strip())
    receipt = json.loads((work / 'receipt.json').read_text())
    if receipt['complete'] or receipt['error'] != 'listing rejected sentinel':
        raise RuntimeError('failure receipt changed')
    print('failed listing rejected; incomplete receipt preserved')
'''
                    result = subprocess.run([sys.executable, '-S', *(['-O'] if optimized else []), '-c', code,
                                             str(EVIDENCE / folder / script)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('failed listing rejected', result.stdout)

    def test_offline_continuation_rejects_invalid_install_receipt(self):
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                code = r'''
import json, runpy, sys, tempfile, types
from pathlib import Path
from unittest.mock import MagicMock, patch
script = sys.argv[1]
g = runpy.run_path(script)['main'].__globals__
with tempfile.TemporaryDirectory() as tmp:
    prior = Path(tmp)
    (prior / 'receipt.json').write_text(json.dumps({'installs': []}))
    nano = types.SimpleNamespace()
    with patch.object(g['importlib'].util, 'spec_from_file_location', return_value=types.SimpleNamespace(loader=MagicMock())), \
         patch.object(g['importlib'].util, 'module_from_spec', return_value=nano), \
         patch.object(g['tempfile'], 'mkdtemp', side_effect=RuntimeError('work created before validation')), \
         patch.object(sys, 'argv', [script, '--execute', '--exclusive-window', '--installed-trials', tmp]):
        g['main']()
'''
                result = subprocess.run([sys.executable, '-S', *(['-O'] if optimized else []), '-c', code,
                                         str(EVIDENCE / '2026-11-01-ironmate/measure_node_offline.py')], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1)
                self.assertIn('AssertionError', result.stderr)
                self.assertNotIn('work created before validation', result.stderr)
                self.assertEqual(result.stdout, '')

    def test_pptx_oracle_accepts_valid_and_rejects_invalid_zip(self):
        # Load the actual pure inspector without executing the historical Node workload.
        path = EVIDENCE / '2026-11-01-ironmate/run_pptx_fixture.py'
        tree = ast.parse(path.read_text())
        definitions = ast.unparse(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[]))
        for optimized in (False, True):
            for mutation in ('none', 'duplicate', 'traversal', 'missing', 'external', 'dangling'):
                with self.subTest(optimized=optimized, mutation=mutation):
                    code = r'''
import hashlib, json, posixpath, sys, tempfile, warnings
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import ZipFile
exec(compile(sys.argv[1], 'pptx-inspector', 'exec'))
mutation = sys.argv[2]
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'sample.pptx'
    with warnings.catch_warnings(), ZipFile(path, 'w') as z:
        warnings.simplefilter('ignore', UserWarning)
        z.writestr('[Content_Types].xml', '<Types><Default Extension="xml"/><Default Extension="rels"/></Types>')
        if mutation != 'missing': z.writestr('ppt/presentation.xml', '<presentation/>')
        if mutation == 'duplicate': z.writestr('ppt/presentation.xml', '<presentation/>')
        if mutation == 'traversal': z.writestr('../outside.xml', '<outside/>')
        if mutation in ('external', 'dangling'):
            z.writestr('_rels/.rels', '<Relationships><Relationship Id="one" Target="missing.xml"' + (' TargetMode="External"' if mutation == 'external' else '') + '/></Relationships>')
    result = inspect(path)
    if result['file_count'] != 2: raise RuntimeError('wrong valid package inventory')
    print('valid package accepted')
'''
                    result = subprocess.run([sys.executable, '-S', *(['-O'] if optimized else []), '-c', code, definitions, mutation],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0 if mutation == 'none' else 1, result.stderr)
                    self.assertEqual(result.stdout, 'valid package accepted\n' if mutation == 'none' else '')
                    if mutation != 'none': self.assertIn('AssertionError', result.stderr)


if __name__ == '__main__':
    unittest.main()

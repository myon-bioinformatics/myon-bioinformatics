import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('bench', HERE / 'benchmark_dra_html.py')
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


class HarnessTests(unittest.TestCase):
    def test_json_script_exact_structure(self):
        p = b.JsonScripts()
        p.feed('<script type="application/json" id="fig-data">{"data":[1,null,"日本"]}</script>')
        self.assertEqual(p.values, {'fig-data': {'data': [1, None, '日本']}})

    def test_duplicate_id_rejected(self):
        p = b.JsonScripts()
        with self.assertRaises(ValueError):
            p.feed('<script type="application/json" id="x">{}</script><script type="application/json" id="x">{}</script>')

    def test_no_figure_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for n in b.NAMES:
                (root / n).write_text('<html>missing</html>')
            with self.assertRaises(ValueError):
                b.output_receipt(root)

    def test_output_digest_sensitive_to_non_json_html(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'index.html').write_text('<script type="application/json" id="x">{}</script>')
            (root / 'propositions.html').write_text('<p>original</p>')
            first = b.output_receipt(root)
            (root / 'propositions.html').write_text('<p>changed</p>')
            self.assertNotEqual(first, b.output_receipt(root))

    def test_tree_excludes_symlink_target(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'real').write_bytes(b'abc')
            (root / 'alias').symlink_to(root / 'real')
            counts = b.tree_bytes(root)
            self.assertEqual(counts['apparent_regular_bytes'], 3)
            self.assertEqual(counts['symlinks'], 1)

    def test_identity_mutation_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'x').write_bytes(b'old')
            (root / 'source-receipt.json').write_text(json.dumps({'files': [dict(destination='x', bytes=3, sha256=b.sha(b'old'))]}))
            with patch.object(b, 'BASE', root):
                self.assertEqual(b.verify()['verified_files'], 1)
                (root / 'x').write_bytes(b'new')
                with self.assertRaises(ValueError):
                    b.verify()

    def test_partial_launch_failure_reaps_existing_worker(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Mock()
            fake.poll.return_value = None
            with patch.object(b, 'BASE', Path(d)), patch.object(b.subprocess, 'Popen', side_effect=[fake, OSError('injected launch failure')]):
                with self.assertRaises(OSError):
                    b.batch('default', 'native', 'c001', 2, 'fake')
            fake.kill.assert_called_once()
            fake.wait.assert_called_once_with(timeout=10)


if __name__ == '__main__':
    unittest.main()

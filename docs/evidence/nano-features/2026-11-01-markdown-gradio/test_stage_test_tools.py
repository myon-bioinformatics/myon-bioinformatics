"""Two stdlib staging checks, not package installation or workload tests."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from stage_test_tools import stage


class StageTests(unittest.TestCase):
    def test_only_receipt_listed_file_is_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'site';source.mkdir();(source/'selected.py').write_bytes(b'ok');(source/'unrelated.py').write_bytes(b'no')
            receipt=root/'receipt.json';receipt.write_text(json.dumps({'files':[{'path':'selected.py','bytes':2,'sha256':hashlib.sha256(b'ok').hexdigest()}]}))
            result=stage(source,root/'tools',receipt)
            self.assertEqual(result['files'],1);self.assertFalse((root/'tools/unrelated.py').exists())

    def test_hash_mismatch_stops_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'site';source.mkdir();(source/'selected.py').write_bytes(b'no')
            receipt=root/'receipt.json';receipt.write_text(json.dumps({'files':[{'path':'selected.py','bytes':2,'sha256':hashlib.sha256(b'ok').hexdigest()}]}))
            with self.assertRaises(ValueError):stage(source,root/'tools',receipt)
            self.assertFalse((root/'tools').exists())


if __name__=='__main__':unittest.main()

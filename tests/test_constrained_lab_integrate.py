import tempfile
import unittest
from pathlib import Path
from tools import constrained_lab_integrate as integrate

class IntegrationTests(unittest.TestCase):
    def test_rejects_missing_staged_source(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                integrate.run_pair(folder, Path(folder)/"missing.py", Path(folder)/"other.py")
    def test_rejects_weak_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            n = Path(folder)/"n.py"
            i = Path(folder)/"i.py"
            n.write_text("pass\\n".replace("\\n", "\n"))
            i.write_text("pass\\n".replace("\\n", "\n"))
            with self.assertRaises(ValueError):
                integrate.run_pair(folder, n, i, rounds=0)
    def test_staged_fixture_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            n = Path(folder)/"n.py"
            i = Path(folder)/"i.py"
            n.write_text("def split_shards(items, rounds, seed):\\n"
                         " import random\\n"
                         " a=list(items);random.Random(seed).shuffle(a)\\n"
                         " return [a[j::rounds] for j in range(rounds)]\\n".replace("\\n", "\n"))
            i.write_text("import struct,zlib\\n"
                         "def encode_png(w,h,rgba):\\n"
                         " def chunk(t,b):return struct.pack('>I',len(b))+t+b+struct.pack('>I',zlib.crc32(t+b))\\n"
                         " raw=b''.join(bytes([0])+rgba[y*w*4:(y+1)*w*4] for y in range(h))\\n"
                         " return bytes.fromhex('89504e470d0a1a0a')+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')\\n".replace("\\n", "\n"))
            result = integrate.run_pair(folder, n, i, rounds=1)
            self.assertTrue(result["equivalent"])
            self.assertEqual(result["runs"][0]["campaign"]["cases_completed"], 2)

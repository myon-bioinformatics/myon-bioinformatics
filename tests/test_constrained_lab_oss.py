import hashlib
from pathlib import Path
import tempfile
import unittest
from tools import constrained_lab_oss as oss

class StagedOSSTests(unittest.TestCase):
    def test_nagoyaction_real_function_contract(self):
        class Module:
            @staticmethod
            def split_shards(items, rounds, seed):
                import random
                a = list(items)
                random.Random(seed).shuffle(a)
                return [a[i::rounds] for i in range(rounds)]
        result = oss.nagoyaction(Module)
        self.assertTrue(result["pass"])
        self.assertEqual(sum(result["shard_sizes"]), 40)
    def test_nagoyaction_detects_duplicates(self):
        class Broken:
            @staticmethod
            def split_shards(items, rounds, seed):
                return [items[:1]] * rounds
        self.assertFalse(oss.nagoyaction(Broken)["pass"])
    def test_source_digest_and_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.py"
            path.write_text("value = 4\\n".replace("\\n", "\n"))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(oss.load_pinned(path, digest).value, 4)
            path.write_text("value = 5\\n".replace("\\n", "\n"))
            with self.assertRaisesRegex(ValueError, "mismatch"):
                oss.load_pinned(path, digest)
    def test_ironmate_encoder_contract(self):
        import struct
        import zlib
        class Module:
            @staticmethod
            def encode_png(width, height, rgba):
                def chunk(tag, data):
                    return struct.pack(">I", len(data))+tag+data+struct.pack(">I", zlib.crc32(tag+data))
                raw = b"".join(bytes([0]) + rgba[y*width*4:(y+1)*width*4] for y in range(height))
                return (bytes.fromhex("89504e470d0a1a0a") + chunk(b"IHDR", struct.pack(">IIBBBBB", width,height,8,6,0,0,0)) +
                        chunk(b"IDAT",zlib.compress(raw)) + chunk(b"IEND",b""))
        self.assertTrue(oss.ironmate(Module)["pass"])

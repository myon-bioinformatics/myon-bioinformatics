"""Independent Playwright/Stagehand evidence contract regression."""
import json
import struct
import zlib
from pathlib import Path
import tempfile
import unittest

from browser_evidence import record, main

PNG = b"\x89PNG\r\n\x1a\n" + b"minimal test fixture"
SHA = "a" * 40


class BrowserEvidenceTests(unittest.TestCase):
    def test_each_engine_is_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shot.png").write_bytes(PNG)
            for engine in ("playwright", "stagehand"):
                value = record(engine, root / "shot.png", root, run_id="123", head_sha=SHA)
                self.assertEqual(value["engine"], engine)
                self.assertEqual(value["screenshot"], "shot.png")
                self.assertEqual(value["size_bytes"], len(PNG))
                self.assertEqual(value["schema"], "browser-screenshot-evidence/1")

    def test_no_traversal_or_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            safe = root / "evidence"
            safe.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(PNG)
            (safe / "alias.png").symlink_to(outside)
            for path in (outside, safe / "alias.png"):
                with self.assertRaises(ValueError):
                    record("playwright", path, safe, run_id="1", head_sha=SHA)

    def test_invalid_engine_sha_and_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "shot.png"
            image.write_bytes(b"not png")
            for engine, sha in (("other", SHA), ("playwright", "bad"), ("playwright", SHA)):
                with self.assertRaises(ValueError):
                    record(engine, image, root, run_id="1", head_sha=sha)

    def test_png_structure_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "shot.png"
            invalid = (
                b"\\x89PNG\\r\\n\\x1a\\n" + b"not a real image",
                PNG[:-3],
                PNG + b"trailing",
                PNG[:32] + bytes([PNG[32] ^ 1]) + PNG[33:],
                PNG[:8] + PNG[33:],
            )
            for data in invalid:
                with self.subTest(size=len(data)):
                    image.write_bytes(data)
                    with self.assertRaises(ValueError):
                        record("playwright", image, root, run_id="1", head_sha=SHA)

    def test_cli_writes_one_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "shot.png"
            image.write_bytes(PNG)
            output = root / "manifest.json"
            self.assertEqual(main(["--engine", "playwright", "--root", str(root),
                "--screenshot", str(image), "--run-id", "7", "--head-sha", SHA,
                "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["run_id"], "7")


if __name__ == "__main__":
    unittest.main()

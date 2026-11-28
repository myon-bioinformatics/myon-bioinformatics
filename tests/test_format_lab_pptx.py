"""Use IRONMATE_CHECKOUT and tools on PATH to enable the real rendering test."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from vendor.cli_args import run_cli


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "format-lab" / "pptx_bench.py"
FIXTURE = ROOT / "tests" / "fixtures" / "format-lab" / "native-table.md"
SPEC = importlib.util.spec_from_file_location("pptx_bench", CLI)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)
CELLS = ("HeaderAlpha", "HeaderBeta", "RowOneAlpha", "RowOneBeta", "RowTwoAlpha", "RowTwoBeta")


class PptxBenchmarkTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.ironmate = Path(work.name)
        (self.ironmate / "pptx_pipeline.py").touch()
        self.output = self.ironmate / "result"

    def benchmark(self, *, generated=True, pipeline_exit=0, inspected=True, preview=True, pages=(1, 2),
                  render_error=None):
        argv = [str(FIXTURE), "--ironmate", str(self.ironmate), "--out", str(self.output)]
        if preview:
            argv.append("--preview")

        def run(command, **kwargs):
            if command[0] == sys.executable:
                deck = Path(command[command.index("-o") + 1])
                if generated:
                    deck.write_bytes(b"generated PPTX")
                inspection = {"inspection": {"slides": [{"slide": 1}, {"slide": 2}]}}
                return subprocess.CompletedProcess(command, pipeline_exit,
                                                   json.dumps(inspection) if inspected else "{}", "")
            if render_error:
                raise render_error
            if command[0] == "soffice":
                (Path(command[-2]) / "slides.pdf").write_bytes(b"generated PDF")
            else:
                for number in pages:
                    Path(f"{command[-1]}-{number}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            return subprocess.CompletedProcess(command, 0)

        with patch.object(bench.subprocess, "run", side_effect=run):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = bench.main(argv)
        report = json.loads((self.output / "benchmark.json").read_text())
        self.assertEqual(code, report["exit_code"])
        return report

    def stages(self, report):
        return tuple(report[stage]["status"] for stage in ("generation", "inspection", "rendering"))

    def test_generation_failure(self):
        report = self.benchmark(generated=False, pipeline_exit=2)
        self.assertEqual(self.stages(report), ("failed", "not_run", "not_run"))
        self.assertEqual(report["exit_code"], 2)
        self.assertFalse((self.output / "slides.pptx").exists())

    def test_pipeline_failure_after_emitting_a_deck(self):
        report = self.benchmark(pipeline_exit=2)
        self.assertEqual(self.stages(report), ("passed", "failed", "not_run"))
        self.assertEqual(report["exit_code"], 2)

    def test_missing_inspection(self):
        report = self.benchmark(inspected=False)
        self.assertEqual(self.stages(report), ("passed", "failed", "not_run"))
        self.assertEqual(report["exit_code"], 1)

    def test_optional_rendering_is_unverified(self):
        report = self.benchmark(preview=False)
        self.assertEqual(self.stages(report), ("passed", "passed", "not_run"))
        self.assertEqual(report["status"], "unverified")
        self.assertEqual(report["exit_code"], 0)
        self.assertNotIn("preview_pages", report)

    def test_failed_rendering_matches_cli_exit(self):
        report = self.benchmark(render_error=FileNotFoundError("soffice"))
        self.assertEqual(self.stages(report), ("passed", "passed", "failed"))
        self.assertIn("soffice", report["rendering"]["error"])
        self.assertEqual(report["exit_code"], 1)
        self.assertTrue((self.output / "slides.pptx").is_file())
        self.assertNotIn("preview_pages", report)

    def test_missing_page_is_not_published(self):
        report = self.benchmark(pages=(1,))
        self.assertEqual(self.stages(report), ("passed", "passed", "page_count_mismatch"))
        self.assertEqual(report["exit_code"], 1)
        self.assertNotIn("preview_pages", report)
        self.assertFalse((self.output / "slide-1.png").exists())

    def test_wrong_page_index_fails_even_when_count_matches(self):
        report = self.benchmark(pages=(1, 3))
        self.assertEqual(self.stages(report), ("passed", "passed", "page_count_mismatch"))
        self.assertEqual(report["exit_code"], 1)

    def test_complete_rendering_records_order_and_hashes(self):
        report = self.benchmark()
        self.assertEqual(self.stages(report), ("passed", "passed", "passed"))
        self.assertEqual(report["status"], "passed")
        self.assertEqual([entry["file"] for entry in report["preview_pages"]], ["slide-1.png", "slide-2.png"])
        for entry in report["preview_pages"]:
            self.assertEqual(entry["sha256"], bench.sha256(self.output / entry["file"]))


class NativeTableRenderingTests(unittest.TestCase):
    def test_markdown_to_visible_native_table(self):
        checkout = os.environ.get("IRONMATE_CHECKOUT")
        if not checkout:
            self.skipTest("Set IRONMATE_CHECKOUT to an Ironmate checkout with the #285 fix")
        required = ("marp", "soffice", "pdftoppm", "pdftotext")
        missing = [name for name in required if shutil.which(name) is None]
        if missing:
            self.fail("Missing rendering tools: " + ", ".join(missing))
        with tempfile.TemporaryDirectory() as work:
            output = Path(work) / "result"
            result = run_cli(CLI, str(FIXTURE), "--ironmate", checkout,
                             "--table-slide", "2", "--preview", "--out", str(output),
                             timeout=500)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "benchmark.json").read_text())
            self.assertEqual(report["rendering"]["status"], "passed")
            self.assertEqual(len(report["preview_pages"]), 2)
            for entry in report["preview_pages"]:
                self.assertEqual(entry["sha256"], bench.sha256(output / entry["file"]))
            with ZipFile(output / "slides.pptx") as package:
                slide = ElementTree.fromstring(package.read("ppt/slides/slide2.xml"))
                table = slide.find(".//{*}graphicData[@uri='http://schemas.openxmlformats.org/drawingml/2006/table']/{*}tbl")
                self.assertIsNotNone(table)
                self.assertEqual(tuple(node.text for node in table.findall(".//{*}t")), CELLS)
            # Only slide 2 has the native table. Marp renders the source table on slide 1.
            rendered = subprocess.check_output(["pdftotext", "-f", "2", "-l", "2",
                                                str(output / "slides.pdf"), "-"], text=True, timeout=20)
            for cell in CELLS:
                self.assertIn(cell, rendered.split())

"""Generate a PPTX with Ironmate and record the inspection of the actual output."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Markdown file; relative assets resolve beside it")
    parser.add_argument("--ironmate", required=True, type=Path, help="Ironmate checkout")
    parser.add_argument("--out", required=True, type=Path, help="New benchmark directory")
    parser.add_argument("--marp", default="marp", help="Marp CLI executable")
    native = parser.add_mutually_exclusive_group()
    native.add_argument("--table-slide", type=int, help="Place the first Markdown table on this reserved slide as a native PowerPoint table")
    native.add_argument("--enrich", type=Path, help="Optional Ironmate native OOXML pattern TOML")
    args = parser.parse_args()
    source = args.source.resolve()
    script = args.ironmate.resolve() / "pptx_pipeline.py"
    if not script.is_file() or not source.is_file():
        parser.error("provide an Ironmate checkout and an existing Markdown file")
    initial = hashlib.sha256(source.read_bytes()).hexdigest()
    args.out.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=args.out) as work:
        deck = Path(work) / "slides.pptx"
        cmd = [sys.executable, str(script), str(source), "-o", str(deck), "--marp", args.marp]
        if args.enrich:
            cmd.extend(["--enrich", str(args.enrich.resolve())])
        if args.table_slide is not None:
            cmd.extend(["--table", str(source), "--table-slide", str(args.table_slide)])
        start = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        report = {"schema": "format-lab-pptx/1", "source_sha256": initial,
                  "elapsed_seconds": round(time.perf_counter() - start, 3),
                  "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        if proc.returncode == 0:
            if hashlib.sha256(source.read_bytes()).hexdigest() != initial:
                raise ValueError("Markdown changed during conversion")
            result = json.loads(proc.stdout)
            report.update(slides=result["inspection"]["slides"], bytes=deck.stat().st_size,
                          pptx_sha256=hashlib.sha256(deck.read_bytes()).hexdigest())
            deck.rename(args.out / "slides.pptx")
        output = args.out / "benchmark.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output)
        if proc.returncode:
            print(proc.stderr, file=sys.stderr)
        return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())

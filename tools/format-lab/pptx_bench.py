"""Generate a PPTX with Ironmate, record its inspection, and optionally render it."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


class PageCountMismatch(ValueError):
    """The renderer did not produce every inspected slide in order."""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_final(deck, work, slide_count):
    """Rasterize the final PPTX, including native objects added after Marp."""
    work = Path(work).resolve()
    subprocess.run(["soffice", "-env:UserInstallation=" + (work / "profile").as_uri(),
                    "--headless", "--convert-to", "pdf", "--outdir", str(work), str(deck.resolve())],
                   check=True, timeout=180, capture_output=True, text=True, errors="replace")
    pdf = work / "slides.pdf"
    if not pdf.is_file() or not pdf.stat().st_size:
        raise ValueError("LibreOffice produced no PDF")
    subprocess.run(["pdftoppm", "-scale-to", "1200", "-png", str(pdf), str(work / "slide")],
                   check=True, timeout=180, capture_output=True, text=True, errors="replace")
    images = sorted((int(p.stem.rsplit("-", 1)[1]), p) for p in work.glob("slide-*.png"))
    if [number for number, _ in images] != list(range(1, slide_count + 1)):
        raise PageCountMismatch(f"Expected {slide_count} rendered pages, received {len(images)} with indices "
                                f"{[number for number, _ in images]}")
    entries = []
    for _, path in images:
        with path.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"{path.name} is not a PNG")
        entries.append({"file": path.name, "sha256": sha256(path)})
    for path in [pdf, *(path for _, path in images)]:
        path.rename(deck.parent / path.name)
    return entries


def build_deck(cmd, source, deck, report):
    start = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    except OSError as error:
        report.update(elapsed_seconds=round(time.perf_counter() - start, 3), stdout="", stderr="")
        report["generation"] = {"status": "failed", "error": str(error)}
        return 1
    report.update(elapsed_seconds=round(time.perf_counter() - start, 3),
                  stdout=proc.stdout, stderr=proc.stderr)
    if sha256(source) != report["source_sha256"]:
        report["generation"] = {"status": "failed", "error": "Markdown changed during conversion"}
        return 1
    if not deck.is_file() or not deck.stat().st_size:
        report["generation"] = {"status": "failed", "error": "Ironmate produced no final PPTX"}
        return proc.returncode or 1

    report.update(generation={"status": "passed"}, bytes=deck.stat().st_size, pptx_sha256=sha256(deck))
    if proc.returncode:
        report["inspection"] = {"status": "failed", "error": "Ironmate exited without a successful inspection"}
        return proc.returncode
    try:
        slides = json.loads(proc.stdout)["inspection"]["slides"]
        if not isinstance(slides, list) or not slides:
            raise ValueError("Inspection returned no slides")
    except (KeyError, TypeError, ValueError) as error:
        report["inspection"] = {"status": "failed", "error": f"Invalid Ironmate inspection: {error}"}
        return 1
    report.update(inspection={"status": "passed"}, slides=slides)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Markdown file; relative assets resolve beside it")
    parser.add_argument("--ironmate", required=True, type=Path, help="Ironmate checkout")
    parser.add_argument("--out", required=True, type=Path, help="New benchmark directory")
    parser.add_argument("--marp", default="marp", help="Marp CLI executable")
    parser.add_argument("--preview", action="store_true", help="Render the final PPTX to PDF and slide PNGs with LibreOffice and Poppler")
    native = parser.add_mutually_exclusive_group()
    native.add_argument("--table-slide", type=int, help="Place the first Markdown table on this reserved slide as a native PowerPoint table")
    native.add_argument("--enrich", type=Path, help="Optional Ironmate native OOXML pattern TOML")
    args = parser.parse_args(argv)
    source = args.source.resolve()
    script = args.ironmate.resolve() / "pptx_pipeline.py"
    if not script.is_file() or not source.is_file():
        parser.error("provide an Ironmate checkout and an existing Markdown file")
    report = {"schema": "format-lab-pptx/2", "source_sha256": sha256(source),
              "generation": {"status": "not_run"}, "inspection": {"status": "not_run"},
              "rendering": {"status": "not_run", "requested": args.preview}}
    args.out.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=args.out) as work:
        deck = Path(work).resolve() / "slides.pptx"
        cmd = [sys.executable, str(script), str(source), "-o", str(deck), "--marp", args.marp]
        if args.enrich:
            cmd.extend(["--enrich", str(args.enrich.resolve())])
        if args.table_slide is not None:
            cmd.extend(["--table", str(source), "--table-slide", str(args.table_slide)])

        exit_code = build_deck(cmd, source, deck, report)
        if not exit_code:
            deck = deck.rename(args.out / "slides.pptx")
            if args.preview:
                try:
                    report["preview_pages"] = render_final(deck, work, len(report["slides"]))
                    report["rendering"]["status"] = "passed"
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    status = "page_count_mismatch" if isinstance(error, PageCountMismatch) else "failed"
                    detail = (getattr(error, "stderr", None) or "").strip()
                    report["rendering"].update(status=status, error=detail or str(error))
                    exit_code = 1

    status = "unverified"
    if exit_code:
        status = "failed"
    elif report["rendering"]["status"] == "passed":
        status = "passed"
    report.update(exit_code=exit_code, status=status)
    output = args.out / "benchmark.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    if exit_code:
        for stage in ("generation", "inspection", "rendering"):
            if "error" in report[stage]:
                print(report[stage]["error"], file=sys.stderr)
        if report["stderr"]:
            print(report["stderr"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

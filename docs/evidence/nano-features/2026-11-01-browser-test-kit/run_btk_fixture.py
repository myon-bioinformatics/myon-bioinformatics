#!/usr/bin/env python3
"""Bounded browser-free BTK fixture caller; Python stdlib only.

Usage: python -B run_btk_fixture.py INSTALLED_PROJECT OUTPUT_DIR
OUTPUT_DIR must not exist. No installs, browser engines, or XLSX workload.
The installed project is read-only input; the private stage symlinks node_modules.
Staged package manifests are the unchanged original source manifests, even when
node_modules points to a projected dependency installation. Never run npm in the
stage. Only the two selected original tests run; this is not the full suite.
Canonical result.json['parity'] excludes machine paths, logs and timings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

PIN = "111f6d78befae28acfb198a45b72e5aa0e59dcde"
SOURCE = Path(__file__).resolve().parent / "btk-inputs"
SOURCE_HASHES = {
    "tools/mirror_facts.mjs": "f27642b0f859132ee45f76de2cd617ff9e23f283f64d21556d7736c59ea8028f",
    "tools/mirror_check.py": "f19e0681740f5e382c51fd17439132afc79dfbc6e00f5ac9adb54aca205346ff",
    "tests/browserless/mirror.spec.ts": "8ef7575c2a658044c043e1f3504044510c4410fb423831e915dfa29ab8d8ddda",
    "playwright.browserless.config.ts": "b442c42d6eaed780f8ff892cbbbe94c454c0925beb5e4c0c98825e2b22a48817",
    "fixtures/mirror.md": "147d63622fbbdf7da901f296753b022aff775621bf382d5a9059a087a40a2bf2",
    "tests/python/test_mirror_check.py": "4831b4130ddfaf30275ec3e83688adcc1902e6cedc957db2f03a4ba2cb56cf27",
    "package.json": "ba6497460d206111c72a735eb0c2ea2ed95eb0acf0f0b0f7ba056fdea0dbcd84",
    "package-lock.json": "cdca302fadb502f6ffd0887e47549e8e674f740752e2f2f62e24cd739bd64094",
    "LICENSE": "15f66204c4a6a1ce0c94f0ed4319ff9400f872e85fd32c95f21695c2f231af59",
}
VERSIONS = {"@playwright/test": "1.64.1", "markdown-it": "15.0.3", "marked": "18.1.1", "linkedom": "0.18.13"}
TITLES = ["markdown-it and marked agree on the CommonMark fixture",
          "HTML reader emits complete facts accepted by compare"]
READERS = ("markdown-it", "marked")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def facts(kind="markdown", reader="fixture-contract", headings=None, links=None, images=None, code=None):
    return {"schema": "mirror-facts/1", "kind": kind, "reader": reader,
            "headings": headings or [], "links": links or [], "images": images or [], "code": code or []}


def cases():
    """Explicit contracts, not a Markdown/HTML parser or a replacement comparator."""
    return [
        ("mirror", None, facts(headings=[[1, "Mirror fixture"], [2, "Section two"], [3, "Third level"]],
                               links=["https://example.org/a", "https://example.org/ref"],
                               images=[["img/logo.png", "logo"]],
                               code=[["python", '# not a heading\nprint("[not](a-link)")']])),
        ("unicode", "# Café 東京 😀\n\n## naïve &amp; Ελληνικά\n\n[世界](https://example.org/u)\n\n![猫 &amp; 犬](img/cat.png)\n",
         facts(headings=[[1, "Café 東京 😀"], [2, "naïve & Ελληνικά"]], links=["https://example.org/u"], images=[["img/cat.png", "猫 & 犬"]])),
        ("escaped-link", "# Escaped \\*literal\\*\n\n[escaped \\[label\\]](https://example.org/a\\(b\\)?x=1&y=2)\n\n\\[not a link\\](relative/not)\n",
         facts(headings=[[1, "Escaped *literal*"]], links=["https://example.org/a(b)?x=1&y=2"])),
        ("code", '# Code\n\n`[inline](nope)`\n\n```js\nconst x = "<h1>not</h1>";\n// [not](nope)\n```\n\n    indented & <tag>\n',
         facts(headings=[[1, "Code"]], code=[["js", 'const x = "<h1>not</h1>";\n// [not](nope)'], ["", "indented & <tag>"]])),
        ("raw-html", '# Raw HTML\n\n<h2>Raw <em>Ω</em> &amp; text</h2>\n\n<a href="/raw?a=1&amp;b=2">raw</a>\n\n<img src="raw.png" alt="A &amp; B">\n\n<pre><code class="language-txt">&lt;raw&gt;\n</code></pre>\n',
         facts(headings=[[1, "Raw HTML"], [2, "Raw Ω & text"]], links=["/raw?a=1&b=2"], images=[["raw.png", "A & B"]], code=[["txt", "<raw>"]])),
        ("bare-url", "# Bare URL\n\nhttps://example.org/bare\n\n<https://example.org/angle>\n",
         facts(headings=[[1, "Bare URL"]], links=["https://example.org/angle"])),
    ]


class FixtureError(RuntimeError):
    pass


def require_equal(actual, expected, label):
    if canonical(actual) != canonical(expected):
        raise FixtureError(f"{label}: expected {canonical(expected)}, got {canonical(actual)}")


class Runner:
    def __init__(self, stage, output, env):
        self.stage, self.output, self.env = stage, output, env
        self.records = []
        self.deadline = time.monotonic() + 150

    def run(self, label, argv, timeout=30):
        """File-backed capture; kill the complete subprocess group on timeout."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise FixtureError(f"{label}: total 150s subprocess deadline exhausted before launch")
        timeout = min(timeout, remaining)
        if timeout <= 0:
            raise FixtureError(f"{label}: subprocess timeout must be positive")
        prefix = self.output / "logs" / f"{len(self.records):02d}-{label}"
        started = time.monotonic()
        record = {"label": label, "argv": [str(x) for x in argv], "timeout_seconds": timeout,
                  "stdout_log": str(prefix) + ".stdout", "stderr_log": str(prefix) + ".stderr"}
        timed_out = False
        with open(record["stdout_log"], "wb") as out, open(record["stderr_log"], "wb") as err:
            proc = subprocess.Popen(record["argv"], cwd=self.stage, env=self.env,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
            finally:
                # Also clean any surviving descendants after a failed/finished runner.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        record.update(returncode=proc.returncode, timed_out=timed_out, seconds=time.monotonic() - started)
        self.records.append(record)
        if timed_out:
            raise FixtureError(f"{label}: exceeded {timeout}s; process group killed")
        for channel in ("stdout", "stderr"):
            if Path(record[channel + "_log"]).stat().st_size > 2 * 1024 * 1024:
                raise FixtureError(f"{label}: {channel} exceeded the 2 MiB capture-read limit")
        return {"returncode": proc.returncode,
                "stdout": Path(record["stdout_log"]).read_text(encoding="utf-8"),
                "stderr": Path(record["stderr_log"]).read_text(encoding="utf-8")}

    def expect(self, label, argv, code, stdout, stderr="", timeout=30):
        actual = self.run(label, argv, timeout)
        require_equal(actual, {"returncode": code, "stdout": stdout, "stderr": stderr}, label)
        return actual


def stage_inputs(project, output):
    modules = project / "node_modules"
    if not modules.is_dir():
        raise FixtureError("INSTALLED_PROJECT/node_modules is absent")
    for name, version in VERSIONS.items():
        data = json.loads((modules / name / "package.json").read_text(encoding="utf-8"))
        require_equal(data["version"], version, name + " installed version")
    for name, digest in SOURCE_HASHES.items():
        require_equal(hashlib.sha256((SOURCE / name).read_bytes()).hexdigest(), digest, name + " source hash")
    stage = output / "stage"
    stage.mkdir()
    for name in SOURCE_HASHES:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, target)
    # Keep original source package/lock files unchanged for provenance, not as an
    # install recipe: the symlink may target the projected 30-package closure.
    # Never invoke npm/npx inside this stage. No chmod of shared inputs.
    # The workload never modifies this link or its target.
    (stage / "node_modules").symlink_to(modules.resolve(), target_is_directory=True)
    for dirname in ("logs", "bin", "tmp", "home", "cache", "browsers"):
        (output / dirname).mkdir()
    (output / "bin" / "python").symlink_to(Path(sys.executable).resolve())
    # Do not inherit NODE_OPTIONS, NODE_PATH, PYTHONPATH, PW_* or reporter overrides.
    env = {"PATH": str(output / "bin") + os.pathsep + os.environ.get("PATH", os.defpath),
           "HOME": str(output / "home"), "TMPDIR": str(output / "tmp"),
           "XDG_CACHE_HOME": str(output / "cache"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
           "NODE_OPTIONS": "", "NODE_PATH": "", "CI": "1", "FORCE_COLOR": "0",
           "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1", "PLAYWRIGHT_BROWSERS_PATH": str(output / "browsers")}
    return stage, env


def parse_junit(path):
    root = ET.parse(path).getroot()
    tests = list(root.iter("testcase"))
    names = sorted(t.attrib.get("name", "") for t in tests)
    require_equal(names, sorted(TITLES), "exact selected original tests")
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for test in tests:
        if test.find("skipped") is not None:
            counts["skipped"] += 1
        elif test.find("failure") is not None or test.find("error") is not None:
            counts["failed"] += 1
        else:
            counts["passed"] += 1
    require_equal(counts, {"passed": 2, "failed": 0, "skipped": 0}, "original test counts")
    return {**counts, "selected": 2, "excluded_xlsx": 1, "full_suite_run": False, "titles": names}


def exercise(node, runner, stage):
    helper = [node, "tools/mirror_facts.mjs"]
    comparator = [sys.executable, "-B", "-S", "tools/mirror_check.py", "compare"]
    result = {"schema": "btk-fixture-parity/1", "source_pin": PIN, "versions": VERSIONS,
              "source_sha256": SOURCE_HASHES, "markdown": {}, "rejections": {}}
    # Existing TS test invokes 'python'; verify PATH resolves this precise interpreter.
    interpreter = runner.run("python-path", ["python", "-B", "-S", "-c", "import os,sys;print(os.path.realpath(sys.executable))"])
    require_equal(interpreter, {"returncode": 0, "stdout": str(Path(sys.executable).resolve()) + "\n", "stderr": ""}, "python PATH interpreter")
    for name, text, expected_base in cases():
        source_file = Path("fixtures/mirror.md") if text is None else Path("fixtures") / (name + ".md")
        if text is not None:
            (stage / source_file).write_text(text, encoding="utf-8")
        actuals, paths = {}, []
        for reader in READERS:
            raw = runner.run(name + "-" + reader, [*helper, "markdown", source_file, "--reader", reader])
            require_equal(raw["returncode"], 0, name + " " + reader + " exit")
            require_equal(raw["stderr"], "", name + " " + reader + " stderr")
            actual = json.loads(raw["stdout"])
            expected = {**expected_base, "reader": f"{reader}@{VERSIONS[reader]}+linkedom@{VERSIONS['linkedom']}"}
            if name == "bare-url" and reader == "marked":
                expected["links"] = ["https://example.org/bare", "https://example.org/angle"]
            require_equal(actual, expected, name + " " + reader + " full canonical facts")
            path = Path("fixtures") / f"{name}-{reader}.json"
            write_json(stage / path, actual)
            paths.append(path)
            actuals[reader] = actual
        if name == "bare-url":
            # CommonMark markdown-it does not linkify bare URLs; default marked uses GFM.
            expected_out = ("split links markdown-it@15.0.3+linkedom@0.18.13: 1 item(s), not shared by all: 0 []\n"
                            "split links marked@18.1.1+linkedom@0.18.13: 2 item(s), not shared by all: 1 ['\"https://example.org/bare\"']\n"
                            "mirror: 2 deviation(s) across 2 readers\n")
            comparison = runner.expect(name + "-compare", [*comparator, *paths], 1, expected_out)
        else:
            comparison = runner.expect(name + "-compare", [*comparator, *paths], 0, "mirror: all agree across 2 readers\n")
        result["markdown"][name] = {"facts": actuals, "comparison": comparison}
    html = '<!doctype html><html><body><h1>Title</h1><a href="https://example.org">link</a><img src="a.png" alt="A"><pre><code class="language-js">x\n</code></pre></body></html>'
    (stage / "fixtures/page.html").write_text(html, encoding="utf-8")
    raw = runner.run("html", [*helper, "html", "fixtures/page.html"])
    expected = facts("html", "linkedom@0.18.13", [[1, "Title"]], ["https://example.org"], [["a.png", "A"]], [["js", "x"]])
    require_equal(raw["returncode"], 0, "html exit")
    require_equal(raw["stderr"], "", "html stderr")
    actual = json.loads(raw["stdout"])
    require_equal(actual, expected, "html full canonical facts")
    write_json(stage / "fixtures/html.json", actual)
    write_json(stage / "fixtures/html-expected.json", {**expected, "reader": "fixture-contract"})
    result["html"] = {"facts": actual, "comparison": runner.expect("html-compare", [*comparator, "fixtures/html.json", "fixtures/html-expected.json"], 0, "mirror: all agree across 2 readers\n")}
    result["rejections"]["helper-unsupported-kind"] = runner.expect("unsupported", [*helper, "unsupported", "fixtures/mirror.md"], 2,
        "", "usage: mirror_facts.mjs markdown|html|xlsx FILE|- [--reader markdown:markdown-it/marked html:linkedom xlsx:exceljs/sheetjs]\n")
    for label, documents, error in [
        ("empty", [facts(), facts()], "no facts to compare in the required lists"),
        ("incomplete", [facts(links=["x"]), {k: v for k, v in facts(links=["x"]).items() if k != "code"}], "document 1: code must be present as a list"),
        ("unsupported-kind", [facts(links=["x"]), facts(kind="unsupported", links=["x"])], "document 1: unsupported kind 'unsupported'"),
    ]:
        paths = []
        for index, document in enumerate(documents):
            path = Path("fixtures") / f"reject-{label}-{index}.json"
            write_json(stage / path, document)
            paths.append(path)
        result["rejections"]["comparator-" + label] = runner.expect("reject-" + label, [*comparator, *paths], 2, "", "mirror_check: cannot compare: " + error + "\n")
    # Execute the two unchanged original tests, excluding the ExcelJS test at collection.
    grep = "(?:" + "|".join(re.escape(title) for title in TITLES) + ")$"
    pw = runner.run("original-two-tests", [node, "node_modules/@playwright/test/cli.js", "test", "tests/browserless/mirror.spec.ts",
        "--config=playwright.browserless.config.ts", "--grep", grep, "--workers=1", "--timeout=20000", "--global-timeout=90000"], timeout=105)
    require_equal(pw["returncode"], 0, "original Playwright two-test exit")
    result["original_tests"] = {"returncode": pw["returncode"], **parse_junit(stage / "test-results/junit/browserless-node.xml")}
    # Detect writes to the immutable staged source files; node_modules is never modified by this caller.
    for name, digest in SOURCE_HASHES.items():
        require_equal(hashlib.sha256((stage / name).read_bytes()).hexdigest(), digest, name + " unchanged after workload")
    require_equal(any((runner.output / "browsers").iterdir()), False, "private browser directory stays empty")
    result["browser_fixture_requested"] = False
    result["private_browser_directory_empty"] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installed_project", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    if os.name != "posix":
        parser.error("POSIX process groups are required for bounded descendant cleanup")
    project, output = args.installed_project.resolve(), args.output_dir.resolve()
    node = shutil.which("node")
    if not project.is_dir() or node is None:
        parser.error("INSTALLED_PROJECT must exist and node must already be on PATH")
    if output == project or project in output.parents or output in project.parents:
        parser.error("OUTPUT_DIR must be outside INSTALLED_PROJECT")
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("OUTPUT_DIR must not already exist (parallel-safe private output required)")
    report = {"schema": "btk-fixture-result/1", "status": "failed", "parity": None,
              "installed_project": str(project), "output_dir": str(output), "processes": []}
    start = time.monotonic()
    runner = None
    try:
        stage, env = stage_inputs(project, output)
        runner = Runner(stage, output, env)
        report["parity"] = exercise(node, runner, stage)
        report["status"] = "passed"
    except (OSError, ValueError, FixtureError, subprocess.SubprocessError, ET.ParseError) as error:
        report["error"] = str(error)
    finally:
        report["seconds"] = time.monotonic() - start
        report["processes"] = runner.records if runner else []
        write_json(output / "result.json", report)
    print(json.dumps({"status": report["status"], "result": str(output / "result.json")}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

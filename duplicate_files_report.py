"""Fail-closed GitHub Actions adapter for gh_identity duplicate-files reports.

Preserve the collector's raw JSON even when it exits nonzero, then render a
Markdown summary before propagating its status. Requires the #60 completeness
contract; older pinned collectors cannot establish a complete fleet scan.
"""

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


def run(identity, output, summary=None, owner="myon-bioinformatics", timeout=900):
    identity = Path(identity).resolve()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    problems = []
    collector_status = 1
    with output.open("w", encoding="utf-8") as destination, \
            output.with_suffix(".stderr.log").open("w", encoding="utf-8") as diagnostics:
        try:
            collector_status = subprocess.run(
                [sys.executable, "-S", str(identity), "--timeout", str(timeout),
                 "duplicate-files", "--owner", owner],
                stdout=destination, stderr=diagnostics, check=False, timeout=timeout,
            ).returncode
        except subprocess.TimeoutExpired:
            collector_status = 124
            problems.append(f"Collector exceeded wall-clock timeout ({timeout} seconds)")
        except OSError as exc:
            problems.append(f"Collector could not start: {exc}")

    report = None
    try:
        report = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        problems.append(f"Report JSON could not be parsed: {exc}")

    complete = None
    errors = []
    if not isinstance(report, dict):
        if report is not None:
            problems.append("Report JSON must be an object")
    else:
        complete = report.get("complete")
        errors = report.get("errors")
        if type(complete) is not bool:
            problems.append("Missing or invalid complete flag: pinned gh_identity lacks the #60 completeness contract")
        if not isinstance(errors, list):
            problems.append("Missing or invalid errors array")
            errors = []
        if complete is True and errors:
            problems.append("Inconsistent report: complete=true but errors are present")
        if complete is False and not errors:
            problems.append("Inconsistent report: complete=false without collection errors")
        if complete is False and collector_status == 0:
            problems.append("Collector returned exit 0 for complete=false")
        if complete is True and collector_status != 0:
            problems.append("Collector returned nonzero for complete=true")

    rendered = ""
    if isinstance(report, dict) and type(complete) is bool and isinstance(report.get("errors"), list):
        try:
            sys.path.insert(0, str(identity.parent))
            module = importlib.import_module(identity.stem)
            rendered = module.render_duplicates_markdown(report, top=30)
            if not isinstance(rendered, str):
                raise TypeError("Markdown renderer did not return text")
        except Exception as exc:
            problems.append(f"Markdown report generation failed: {type(exc).__name__}: {exc}")

    failed = collector_status != 0 or complete is not True or bool(problems)
    if failed:
        prefix = ["**INCOMPLETE — duplicate-files report is not a verified all-repository result.**", ""]
        if complete is False:
            prefix.append("Counts include complete repositories only; failed repositories are excluded.")
            prefix.append("")
        if errors:
            prefix.extend(["Collection errors:", ""])
            for error in errors:
                if isinstance(error, dict):
                    prefix.append(f"- `{error.get('repository', 'unknown')}`: `{error.get('detail', 'unknown')}`")
                else:
                    prefix.append(f"- `{error}`")
            prefix.append("")
        for problem in problems:
            prefix.append(f"- {problem}")
        if problems:
            prefix.append("")
        prefix.extend([f"Collector exit status: `{collector_status}`", ""])
        rendered = "\n".join(prefix) + rendered
    if not rendered:
        rendered = "**INCOMPLETE — no Markdown report was generated.**\n"
    markdown = rendered.rstrip() + "\n"

    # Keep local Markdown evidence even if GitHub's summary file is unavailable.
    output.with_suffix(".md").write_text(markdown, encoding="utf-8")
    if summary:
        try:
            with Path(summary).open("a", encoding="utf-8") as destination:
                destination.write(markdown + "\n")
        except OSError as exc:
            print(f"Unable to write GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)
            failed = True
    return collector_status if collector_status > 0 else (1 if failed else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--output", default="reports/duplicate-files.json")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    parser.add_argument("--owner", default="myon-bioinformatics")
    parser.add_argument("--timeout", type=int, default=900,
                        help="Collector operation budget and wall-clock limit in seconds (default: 900)")
    ns = parser.parse_args(argv)
    if ns.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    return run(ns.identity, ns.output, ns.summary, ns.owner, ns.timeout)


if __name__ == "__main__":
    sys.exit(main())

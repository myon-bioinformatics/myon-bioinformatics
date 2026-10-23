#!/usr/bin/env python3
"""Install only the missing test requirements, run pytest, print only what decides the result.

Canonical parent tool migrated from browser-test-kit ``scripts/pytest_brief.py``
(61872b2). Consumers vendor this repository directly; do not copy from btk.

Replaces the re-typed one-liner
``pip install -q --break-system-packages pytest-cov | tail -1; python -m pytest -q ... --cov... | tail -N``:

* requirements (``--requirements``, default ``tests/requirements.txt`` if present) are
  resolved by pip >=23.0 in the target interpreter, preserving versions, markers and extras;\n  installation is skipped when the dry-run report is empty, with
  ``--break-system-packages`` only when the interpreter is marked EXTERNALLY-MANAGED;
* pytest runs in this interpreter as ``python -m pytest -q -rfE -p no:cacheprovider [ARGS]``;
* printed: the installed names (if any), ``FAILED``/``ERROR`` lines, the coverage ``TOTAL``
  and ``Required test coverage`` lines, and pytest's final summary line. If pytest itself
  could not run (exit 2-5 with no summary), the last ``--tail`` lines instead.

Stdlib only; runs under ``python -S`` (the child pytest runs without ``-S``)::

    python -S pytest_brief.py -- --cov=xprobe --cov-branch --cov-report=term --cov-fail-under=90
    python -S pytest_brief.py --requirements tool/python/requirements.txt -- tool/python/tests

Exit code: pytest's own (0 passed, 1 failed, ...); 3 = installing a requirement failed.
``--json`` prints ``{ok, exit_code, installed, lines, hint}`` instead of text lines.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

KEEP = re.compile(r"^(FAILED|ERROR) |^TOTAL\s|Required test coverage|^=+ .*\b(passed|failed|error|errors|skipped|deselected|no tests ran)\b.* =+$|^\d+ (passed|failed)")


_MANAGED_PROBE = """import os, sys, sysconfig
print(int(sys.prefix == sys.base_prefix and os.path.isfile(
    os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED"))))
"""


def externally_managed(python: str = sys.executable) -> bool:
    """Ask the target interpreter; virtual environments are exempt (PEP 668)."""
    done = subprocess.run([python, "-c", _MANAGED_PROBE],
                          capture_output=True, text=True, check=True)
    value = done.stdout.strip()
    if value not in ("0", "1"):
        raise ValueError("invalid target interpreter environment probe")
    return value == "1"


def resolve(pip: list[str], requirements: list[str]) -> list[str]:
    """Return pip's proposed installs without changing the target environment.

    Pass files unchanged: pip owns version, extras, marker, include and constraint
    semantics. Requires pip >=23.0 (stable installation report version 1).
    """
    with tempfile.TemporaryDirectory(prefix="pytest-brief-") as directory:
        report = Path(directory) / "report.json"
        subprocess.run(pip + ["--dry-run", "--report", str(report), *requirements],
                       capture_output=True, text=True, check=True)
        data = json.loads(report.read_text(encoding="utf-8"))
    if data.get("version") != "1" or not isinstance(data.get("install"), list):
        raise ValueError("unsupported pip installation report")
    return [entry["metadata"]["name"] for entry in data["install"]]


def brief(output: str, returncode: int, tail: int) -> list[str]:
    lines = [line.rstrip() for line in output.splitlines()]
    kept = [line for line in lines if KEEP.search(line)]
    if not any(re.search(r"\b(passed|failed|no tests ran|error)\b", line) for line in kept) and returncode not in (0, 1):
        return [line for line in lines if line][-tail:]
    return kept


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pytest_args = argv[argv.index("--") + 1:] if "--" in argv else []
    own = argv[: argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--requirements", help="requirements file (default: tests/requirements.txt if present)")
    parser.add_argument("--tail", type=int, default=15, help="lines shown when pytest could not run")
    parser.add_argument("--python", default=sys.executable, help="interpreter for pip and pytest")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object instead of text lines")
    parser.epilog = ("Exit codes: pytest's own (0 passed, 1 failed), "
                     "3 a requirement failed to resolve or install so pytest did not run, "
                     "2 invalid arguments. --json prints {ok, exit_code, installed, lines, hint}.")
    args = parser.parse_args(own)
    req = args.requirements or ("tests/requirements.txt" if os.path.exists("tests/requirements.txt") else None)
    if args.tail < 1:
        parser.error("--tail must be at least 1")
    installed: list[str] = []
    if req:
        try:
            pip = [args.python, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
            if externally_managed(args.python):
                pip.append("--break-system-packages")
            requirements = ["-r", req]
            need = resolve(pip, requirements)
            installed = list(need)
            if need:
                subprocess.run(pip + requirements, capture_output=True, text=True, check=True)
                if not args.json:
                    print("installed: " + ", ".join(need))
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as exc:
            message = f"pytest_brief: dependency resolution/install failed: {exc}"
            print(message, file=sys.stderr)
            tail_lines: list[str] = []
            if isinstance(exc, subprocess.CalledProcessError):
                tail_lines = (exc.stdout + exc.stderr).strip().splitlines()[-args.tail:]
                print("\n".join(tail_lines), file=sys.stderr)
            if args.json:
                print(json.dumps({
                    "ok": False,
                    "exit_code": 3,
                    "error": message,
                    "hint": "A requirement failed to resolve or install, so pytest did not run. Read the pip tail on stderr.",
                    "installed": installed,
                    "lines": tail_lines,
                }, ensure_ascii=False))
            return 3
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([args.python, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", *pytest_args],
                          capture_output=True, text=True, env=env)
    lines = brief(done.stdout + done.stderr, done.returncode, args.tail)
    if args.json:
        payload = {"ok": done.returncode == 0, "exit_code": done.returncode,
                   "installed": installed, "lines": lines, "hint": ""}
        if done.returncode != 0:
            payload["hint"] = "pytest did not pass. lines keeps FAILED, ERROR, coverage, and the summary. The exit code is pytest's own."
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())

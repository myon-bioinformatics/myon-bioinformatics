#!/usr/bin/env python3
"""One-at-a-time operator, constant, condition, and return mutations for pr-retarget.

Rewrites gh_ops.py, runs tests/test_gh_ops_pr_retarget.py, then restores the file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "gh_ops.py"
RANGES = [(235, 333), (1004, 1154), (2011, 2014), (2260, 2264)]
# (needle, replacement) applied to a single occurrence on one line.
SWAPS = (
    (" == ", " != "),
    (" != ", " == "),
    (" and ", " or "),
    (" or ", " and "),
    (" > ", " < "),
    (" < ", " > "),
    (" >= ", " > "),
    (" <= ", " < "),
    (" not ", " "),
    ("True", "False"),
    ("False", "True"),
    ("return \"\"", "return \"MUT\""),
    ("return None", "return \"\""),
    ("return out", "return {**out, \"ok\": True}"),
    ("return empty", "return {**empty, \"matched\": True}"),
    ("404", "403"),
    ("[:5]", "[:]"),
    ("[:limit]", ""),
    (" < 1", " < 0"),
    (" > 0", " > 1"),
)


def in_range(line_no: int) -> bool:
    return any(start <= line_no <= end for start, end in RANGES)


def mutants(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines(keepends=True)
    found = []
    in_doc = False
    for index, line in enumerate(lines):
        toggles = line.count('"""') + line.count("'''")
        if in_doc:
            if toggles % 2 == 1:
                in_doc = False
            continue
        if toggles % 2 == 1:
            in_doc = True
            continue
        line_no = index + 1
        if not in_range(line_no):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or '"""' in line or "'''" in line:
            continue
        for needle, replacement in SWAPS:
            start = 0
            occurrence = 0
            while True:
                at = line.find(needle, start)
                if at < 0:
                    break
                occurrence += 1
                mutated_line = line[:at] + replacement + line[at + len(needle):]
                mutated = "".join(lines[:index] + [mutated_line] + lines[index + 1:])
                if mutated != text:
                    label = f"L{line_no}#{occurrence} {needle!r}->{replacement!r}"
                    found.append((label, mutated))
                start = at + len(needle)
    return found


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    cases = mutants(original)
    killed = survived = 0
    survivor_names = []
    try:
        for label, mutated in cases:
            SOURCE.write_text(mutated, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider",
                 "tests/test_gh_ops_pr_retarget.py"],
                cwd=ROOT, capture_output=True, text=True)
            if result.returncode == 0:
                survived += 1
                survivor_names.append(label)
                print(f"SURVIVED {label}", flush=True)
            else:
                killed += 1
                print(f"killed {label}", flush=True)
    finally:
        SOURCE.write_text(original, encoding="utf-8")
    print(f"TOTAL {len(cases)} killed {killed} survived {survived}")
    if survivor_names:
        print("SURVIVORS")
        for name in survivor_names:
            print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

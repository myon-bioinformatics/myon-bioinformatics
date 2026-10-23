"""Parent GitHub write calls stay inside the reviewed set.

See docs/gh-ops-write-overlap.md. A new script, module, or workflow that
pushes, commits, or sends a GitHub write method must be added there first.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WRITE_PATTERNS = (
    re.compile(r"""_github_api\(\s*["'](?:PUT|POST|PATCH|DELETE)["']"""),
    re.compile(r"""\.request\(\s*["'](?:PUT|POST|PATCH|DELETE)["']"""),
    re.compile(r"""method\s*=\s*["'](?:PUT|POST|PATCH|DELETE)["']"""),
    re.compile(r"\bgit commit\b"),
    re.compile(r"\bgit push\b"),
)

# Reviewed parent write surfaces. Child repositories are outside this scan.
ALLOW = (
    "gh_ops.py",
    "gh_workflow.py",
    "vendor_sync.py",
    "vendor/gh_identity.py",
    ".github/workflows/sync-readme.yml",
    ".github/workflows/update-readme-stats.yml",
)

SKIP_PREFIXES = ("tests/", "docs/", ".git/")
SCAN_SUFFIXES = {".py", ".yml", ".yaml", ".mjs", ".cjs", ".js"}


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _hits(text: str) -> bool:
    return any(pattern.search(text) for pattern in WRITE_PATTERNS)


def test_reviewed_write_files_still_contain_a_write():
    missing = [relative for relative in ALLOW if not _hits(_text(relative))]
    assert missing == []


def test_no_unlisted_parent_write_surface():
    extras = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(SKIP_PREFIXES) or relative in ALLOW:
            continue
        if _hits(path.read_text(encoding="utf-8", errors="replace")):
            extras.append(relative)
    assert extras == []

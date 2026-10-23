"""Parent GitHub write files stay on an explicit reviewed list."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW = (
    "gh_ops.py",
    "gh_workflow.py",
    "vendor_sync.py",
    "vendor/gh_identity.py",
    ".github/workflows/sync-readme.yml",
    ".github/workflows/update-readme-stats.yml",
)
SKIP_PREFIXES = ("tests/", "docs/", ".git/")
SUFFIXES = {".py", ".yml", ".yaml", ".mjs", ".cjs", ".js"}
WRITE_PATTERNS = (
    re.compile(r'_github_api\(\s*"(?:PUT|POST|PATCH|DELETE)"'),
    re.compile(r'\.request\(\s*"(?:PUT|POST|PATCH|DELETE)"'),
    re.compile(r'method\s*=\s*"(?:PUT|POST|PATCH|DELETE)"'),
    re.compile(r'\bgit commit\b'),
    re.compile(r'\bgit push\b'),
    re.compile(r'--method'),
)


def _tracked_files():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def _write_files():
    found = []
    for rel in _tracked_files():
        path = ROOT / rel
        if path.suffix not in SUFFIXES or rel.startswith(SKIP_PREFIXES) or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(text) for pattern in WRITE_PATTERNS):
            found.append(rel)
    return sorted(found)


def test_reviewed_write_files_still_contain_a_write():
    found = set(_write_files())
    missing = [path for path in ALLOW if path not in found]
    assert not missing, "reviewed write file no longer matches: " + ", ".join(missing)


def test_no_unlisted_parent_write_surface():
    extra = [path for path in _write_files() if path not in ALLOW]
    assert not extra, "unlisted GitHub write surface: " + ", ".join(extra)

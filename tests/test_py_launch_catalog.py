"""Catalog pin for py_launch.cjs after parent PR #145 merge."""
import hashlib
import json
import subprocess
from pathlib import Path

import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
PY_LAUNCH_MERGE_COMMIT = "2ea31034ca3caa60ed769806baf750d75e296552"
REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
SOURCE = "py_launch.cjs"


def _catalog_tool():
    catalog = json.loads((ROOT / "vendor-catalog.json").read_text(encoding="utf-8"))
    return next(tool for tool in catalog["tools"] if tool["source"] == SOURCE)


def test_py_launch_catalog_entry_uses_accepting_merge_commit():
    tool = _catalog_tool()
    assert tool["repository"] == REPOSITORY
    assert tool["commit"] == PY_LAUNCH_MERGE_COMMIT
    vendor_sync.public_commit_exists(tool["repository"], tool["commit"], entry_label=SOURCE)


def test_py_launch_catalog_pin_contains_matching_bytes():
    tool = _catalog_tool()
    at_pin = subprocess.check_output(["git", "show", f"{tool['commit']}:{SOURCE}"])
    inspected = vendor_sync.inspect_source(tool["repository"], tool["commit"], tool["source"])
    assert hashlib.sha256(at_pin).hexdigest() == inspected["sha256"]
    assert vendor_sync.git_blob(at_pin) == inspected["blob_sha"]

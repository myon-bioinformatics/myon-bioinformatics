"""Catalog pin for py_launch.cjs after parent PR #145 merge."""
import hashlib
import json
from pathlib import Path

import pytest

import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
PY_LAUNCH_MERGE_COMMIT = "2ea31034ca3caa60ed769806baf750d75e296552"
REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
SOURCE = "py_launch.cjs"


def _catalog_tool():
    catalog = json.loads((ROOT / "vendor-catalog.json").read_text(encoding="utf-8"))
    return next(tool for tool in catalog["tools"] if tool["source"] == SOURCE)


def test_py_launch_catalog_entry_metadata():
    tool = _catalog_tool()
    assert tool["repository"] == REPOSITORY
    assert tool["commit"] == PY_LAUNCH_MERGE_COMMIT
    assert tool["source"] == SOURCE


def test_py_launch_local_source_identity():
    data = (ROOT / SOURCE).read_bytes()
    blob_sha = vendor_sync.git_blob(data)
    sha256 = hashlib.sha256(data).hexdigest()
    assert len(blob_sha) == 40
    assert len(sha256) == 64


@pytest.mark.network
def test_py_launch_catalog_entry_uses_accepting_merge_commit():
    tool = _catalog_tool()
    vendor_sync.public_commit_exists(tool["repository"], tool["commit"], entry_label=SOURCE)


@pytest.mark.network
def test_py_launch_catalog_pin_contains_matching_bytes():
    tool = _catalog_tool()
    at_pin = vendor_sync.public_raw_bytes(tool["repository"], tool["commit"], tool["source"])
    assert at_pin is not None
    inspected = vendor_sync.inspect_source(tool["repository"], tool["commit"], tool["source"])
    assert hashlib.sha256(at_pin).hexdigest() == inspected["sha256"]
    assert vendor_sync.git_blob(at_pin) == inspected["blob_sha"]

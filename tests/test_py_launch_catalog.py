"""Catalog pin for py_launch.cjs after parent PR #149 merge."""
import hashlib
import json
from pathlib import Path

import pytest

import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
PY_LAUNCH_MERGE_COMMIT = "173124231fc171c251e35caef1fa67e76d0f8bae"
REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
SOURCE = "py_launch.cjs"
PY_LAUNCH_CATALOG_PIN_BLOB_SHA = "0b1d7cc5d7d1f71adbe49fe5c87d25415805153b"
PY_LAUNCH_CATALOG_PIN_SHA256 = "63035549d0bce8035bc594e12df68591d8e58970aaeaa4409882ea3c3f2e6eaa"
PY_LAUNCH_WORKTREE_BLOB_SHA = "0b1d7cc5d7d1f71adbe49fe5c87d25415805153b"
PY_LAUNCH_WORKTREE_SHA256 = "63035549d0bce8035bc594e12df68591d8e58970aaeaa4409882ea3c3f2e6eaa"
PY_LAUNCH_LOCK_FIXTURE = ROOT / "tests" / "fixtures" / "cli-args-pr26.lock.json"


def _catalog_tool():
    catalog = json.loads((ROOT / "vendor-catalog.json").read_text(encoding="utf-8"))
    return next(tool for tool in catalog["tools"] if tool["source"] == SOURCE)


def test_py_launch_catalog_entry_metadata():
    tool = _catalog_tool()
    assert tool["repository"] == REPOSITORY
    assert tool["commit"] == PY_LAUNCH_MERGE_COMMIT
    assert tool["source"] == SOURCE


def test_py_launch_catalog_pin_digests_match_fixture_record():
    lock = json.loads(PY_LAUNCH_LOCK_FIXTURE.read_text(encoding="utf-8"))
    entry = next(item for item in lock["files"] if item.get("source") == SOURCE)
    assert entry["commit"] == PY_LAUNCH_MERGE_COMMIT
    assert entry["blob_sha"] == PY_LAUNCH_CATALOG_PIN_BLOB_SHA
    assert entry["sha256"] == PY_LAUNCH_CATALOG_PIN_SHA256


def test_py_launch_worktree_source_identity():
    data = (ROOT / SOURCE).read_bytes()
    assert vendor_sync.git_blob(data) == PY_LAUNCH_WORKTREE_BLOB_SHA
    assert hashlib.sha256(data).hexdigest() == PY_LAUNCH_WORKTREE_SHA256


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

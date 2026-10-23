"""Catalog pin for node_launch.py after parent PR #158 merge."""
import hashlib
import json
from pathlib import Path

import pytest

import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
NODE_LAUNCH_MERGE_COMMIT = "fe79d3441c225b9d460b92041e06c166e686abdf"
REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
SOURCE = "node_launch.py"
NODE_LAUNCH_CATALOG_PIN_BLOB_SHA = "5284dc7f99cd5434a0c9db51aec541393ad2e8ae"
NODE_LAUNCH_CATALOG_PIN_SHA256 = "fc5c5ff8f751ef0bc6429546c928aed63494f45d9b7cfa8d5782b542ea90e029"
NODE_LAUNCH_WORKTREE_BLOB_SHA = "5284dc7f99cd5434a0c9db51aec541393ad2e8ae"
NODE_LAUNCH_WORKTREE_SHA256 = "fc5c5ff8f751ef0bc6429546c928aed63494f45d9b7cfa8d5782b542ea90e029"
NODE_LAUNCH_CATALOG_PIN_FIXTURE = ROOT / "tests" / "fixtures" / "node-launch-catalog-pin.lock.json"


def _catalog_tool():
    catalog = json.loads((ROOT / "vendor-catalog.json").read_text(encoding="utf-8"))
    return next(tool for tool in catalog["tools"] if tool["source"] == SOURCE)


def test_node_launch_catalog_entry_metadata():
    tool = _catalog_tool()
    assert tool["repository"] == REPOSITORY
    assert tool["commit"] == NODE_LAUNCH_MERGE_COMMIT
    assert tool["source"] == SOURCE


def test_node_launch_catalog_pin_digests_match_fixture_record():
    lock = json.loads(NODE_LAUNCH_CATALOG_PIN_FIXTURE.read_text(encoding="utf-8"))
    entry = next(item for item in lock["files"] if item.get("source") == SOURCE)
    assert entry["commit"] == NODE_LAUNCH_MERGE_COMMIT
    assert entry["blob_sha"] == NODE_LAUNCH_CATALOG_PIN_BLOB_SHA
    assert entry["sha256"] == NODE_LAUNCH_CATALOG_PIN_SHA256


def test_node_launch_worktree_source_identity():
    data = (ROOT / SOURCE).read_bytes()
    assert vendor_sync.git_blob(data) == NODE_LAUNCH_WORKTREE_BLOB_SHA
    assert hashlib.sha256(data).hexdigest() == NODE_LAUNCH_WORKTREE_SHA256


@pytest.mark.network
def test_node_launch_catalog_entry_uses_accepting_merge_commit():
    tool = _catalog_tool()
    vendor_sync.public_commit_exists(tool["repository"], tool["commit"], entry_label=SOURCE)


@pytest.mark.network
def test_node_launch_catalog_pin_contains_matching_bytes():
    tool = _catalog_tool()
    at_pin = vendor_sync.public_raw_bytes(tool["repository"], tool["commit"], tool["source"])
    assert at_pin is not None
    inspected = vendor_sync.inspect_source(tool["repository"], tool["commit"], tool["source"])
    assert hashlib.sha256(at_pin).hexdigest() == inspected["sha256"]
    assert vendor_sync.git_blob(at_pin) == inspected["blob_sha"]
    assert hashlib.sha256(at_pin).hexdigest() == NODE_LAUNCH_CATALOG_PIN_SHA256
    assert vendor_sync.git_blob(at_pin) == NODE_LAUNCH_CATALOG_PIN_BLOB_SHA

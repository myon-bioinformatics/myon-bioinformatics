"""Catalog pin for py_launch.cjs after parent PR #151 merge."""
import hashlib
import json
from pathlib import Path

import pytest

import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
PY_LAUNCH_MERGE_COMMIT = "c73821de8492b2394c718cb276cad9f683ff1910"
REPOSITORY = "myon-bioinformatics/myon-bioinformatics"
SOURCE = "py_launch.cjs"
PY_LAUNCH_CATALOG_PIN_BLOB_SHA = "5a90907aa0ed3e2afa5ef1519010136644af1658"
PY_LAUNCH_CATALOG_PIN_SHA256 = "b43c88e4e635d888995cd33f121850fe2cae753b66a55c628891d6795429ab88"
# Worktree bytes move with this checkout. The catalog pin above stays the
# accepted main merge until a reviewed promotion.
PY_LAUNCH_WORKTREE_BLOB_SHA = "006817bc813187fa45abc3e8954fde27a2b73caa"
PY_LAUNCH_WORKTREE_SHA256 = "a78fe656cde285a83501b9b19d97b9ebde6f9637919b3adb95c50eb254154536"
PY_LAUNCH_CATALOG_PIN_FIXTURE = ROOT / "tests" / "fixtures" / "py-launch-catalog-pin.lock.json"


def _catalog_tool():
    catalog = json.loads((ROOT / "vendor-catalog.json").read_text(encoding="utf-8"))
    return next(tool for tool in catalog["tools"] if tool["source"] == SOURCE)


def test_py_launch_catalog_entry_metadata():
    tool = _catalog_tool()
    assert tool["repository"] == REPOSITORY
    assert tool["commit"] == PY_LAUNCH_MERGE_COMMIT
    assert tool["source"] == SOURCE


def test_py_launch_catalog_pin_digests_match_fixture_record():
    lock = json.loads(PY_LAUNCH_CATALOG_PIN_FIXTURE.read_text(encoding="utf-8"))
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
    assert hashlib.sha256(at_pin).hexdigest() == PY_LAUNCH_CATALOG_PIN_SHA256
    assert vendor_sync.git_blob(at_pin) == PY_LAUNCH_CATALOG_PIN_BLOB_SHA

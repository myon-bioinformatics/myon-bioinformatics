"""Catalog pins of the two launchers: py_launch.cjs (parent PR #171 merge) and njs_launch.py (parent PR #266 merge)."""
import hashlib, json
from pathlib import Path

import pytest

import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
# One row per launcher. The catalog pin stays the accepted main merge until a reviewed promotion; the worktree bytes move
# with this checkout.
LAUNCHERS = pytest.mark.parametrize("pin", [
    {"source": "py_launch.cjs", "worktree": "py_launch.cjs", "commit": "1116d5c4f55d25af4d21cfe9b3c88f49d3a476fa",
     "fixture": "py-launch-catalog-pin.lock.json",
     "pin_blob": "006817bc813187fa45abc3e8954fde27a2b73caa", "pin_sha256": "a78fe656cde285a83501b9b19d97b9ebde6f9637919b3adb95c50eb254154536",
     "worktree_blob": "c858a1f71e1d75a1fbbefbd762fd7038287880aa", "worktree_sha256": "c0e1333d34bcb95aec94cc78b2ee2ad9d01abe4e556c81c99094e2c8faf0feaa"},
    {"source": "njs_launch.py", "worktree": "njs_launch.py", "commit": "0d20801e2e372a72426ce85cee7ac04d701e71b5",
     "fixture": "njs-launch-catalog-pin.lock.json",
     "pin_blob": "79521fdd4d77f4a0ae91ca4a1791f4ad46a0d574", "pin_sha256": "2499b0c407388c7977e8c8aab51467e882080566b0aab03019cb7bb8d263672f",
     "worktree_blob": "79521fdd4d77f4a0ae91ca4a1791f4ad46a0d574", "worktree_sha256": "2499b0c407388c7977e8c8aab51467e882080566b0aab03019cb7bb8d263672f"},
], ids=["py_launch", "njs_launch"])


def _catalog_tool(pin):
    catalog = json.loads((ROOT / "vendor-catalog.json").read_text(encoding="utf-8"))
    return next(tool for tool in catalog["tools"] if tool["source"] == pin["source"])


@LAUNCHERS
def test_catalog_entry_metadata(pin):
    tool = _catalog_tool(pin)
    assert tool["repository"] == "myon-bioinformatics/myon-bioinformatics"
    assert tool["commit"] == pin["commit"]
    assert tool["source"] == pin["source"]


@LAUNCHERS
def test_catalog_pin_digests_match_fixture_record(pin):
    lock = json.loads((ROOT / "tests" / "fixtures" / pin["fixture"]).read_text(encoding="utf-8"))
    entry = next(item for item in lock["files"] if item.get("source") == pin["source"])
    assert entry["commit"] == pin["commit"]
    assert entry["blob_sha"] == pin["pin_blob"]
    assert entry["sha256"] == pin["pin_sha256"]


@LAUNCHERS
def test_worktree_source_identity(pin):
    data = (ROOT / pin["worktree"]).read_bytes()
    assert vendor_sync.git_blob(data) == pin["worktree_blob"]
    assert hashlib.sha256(data).hexdigest() == pin["worktree_sha256"]


@pytest.mark.network
@LAUNCHERS
def test_catalog_entry_uses_accepting_merge_commit(pin):
    tool = _catalog_tool(pin)
    vendor_sync.public_commit_exists(tool["repository"], tool["commit"], entry_label=pin["source"])


@pytest.mark.network
@LAUNCHERS
def test_catalog_pin_contains_matching_bytes(pin):
    tool = _catalog_tool(pin)
    at_pin = vendor_sync.public_raw_bytes(tool["repository"], tool["commit"], tool["source"])
    assert at_pin is not None
    inspected = vendor_sync.inspect_source(tool["repository"], tool["commit"], tool["source"])
    assert hashlib.sha256(at_pin).hexdigest() == inspected["sha256"] == pin["pin_sha256"]
    assert vendor_sync.git_blob(at_pin) == inspected["blob_sha"] == pin["pin_blob"]

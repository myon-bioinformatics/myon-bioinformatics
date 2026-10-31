"""Keep the reviewed fleet observation baseline internally consistent.

These fixtures freeze claims for offline regression, not proof of upstream
provenance. Parent CI separately reads the immutable source and child blobs.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

import vendor_consumers
import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests/fixtures/fleet_vendor_locks_20261029.json").read_text())


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_observed_lock_topology_and_immutable_revision(name):
    registry = json.loads((ROOT / "vendor-consumers.json").read_text())
    heads = vendor_sync.validate_child_heads(json.loads((ROOT / "child-heads.json").read_text()))
    fixture = FIXTURES[name]
    lock = vendor_sync.validate(fixture["lock"])
    assert heads["heads"][name]["sha"] == fixture["sha"]
    assert vendor_consumers.compare(registry, lock, name)["matches"]
    if name != vendor_sync.PARENT_REPOSITORY:
        assert all(row["ref"] == row["commit"] for row in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, name)["matches"]
    changed = copy.deepcopy(lock)
    changed["files"][0]["destination"] += ".unreviewed"
    assert not vendor_consumers.compare(registry, changed, name)["matches"]


def test_flutter_full_workflow_digest_remains_explicit():
    registry = json.loads((ROOT / "vendor-consumers.json").read_text())
    row = vendor_consumers.get_consumer(registry, "myon-bioinformatics/flutter_navigation_basic")
    workflow = (ROOT / "tests/fixtures/flutter_non_dart_20261029.yml").read_bytes()
    assert hashlib.sha256(workflow).hexdigest() == row["locked_ci"]["sha256"]
    assert row["locked_ci"]["workflow"] == ".github/workflows/non-dart.yml"
    assert b"python-vendor-locked:" in workflow
    assert workflow.count(b"vendor_sync.py check --manifest python/vendor.lock.json") == 2
    assert b"vendor_sync.py materialize --manifest python/vendor.lock.json" in workflow


def test_observation_does_not_enroll_new_repositories():
    registry = vendor_consumers.validate(json.loads((ROOT / "vendor-consumers.json").read_text()))
    locked = {row["repository"] for row in registry["consumers"] if row["state"] == "locked"}
    assert set(FIXTURES) == locked

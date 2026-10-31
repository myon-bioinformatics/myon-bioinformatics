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
# Preserve the previous observation; only this reviewed child was re-observed.
REFRESHED_FIXTURES = {
    "myon-bioinformatics/Ironmate": json.loads(
        (ROOT / "tests/fixtures/ironmate_adopted_vendor_20261031.json").read_text()),
}
# Registry entries enrolled after the 2026-10-29 observation. The parent registry
# must land before the child lock (the child CI reads the live registry), so these
# are excluded from the frozen-baseline comparison until the baseline is re-observed.
POST_BASELINE_ENROLLMENTS = {
    "myon-bioinformatics/Ironmate": {"vendor/fetch_module.py", "vendor/Aoi-LICENSE"},
}


def baseline_registry(registry):
    registry = copy.deepcopy(registry)
    for consumer in registry["consumers"]:
        drop = (set() if consumer["repository"] in REFRESHED_FIXTURES
                else POST_BASELINE_ENROLLMENTS.get(consumer["repository"], set()))
        consumer["entries"] = [e for e in consumer["entries"]
                               if e["destination"] not in drop and e.get("license") not in drop]
    return registry


def test_post_baseline_enrollments_are_registered():
    registry = json.loads((ROOT / "vendor-consumers.json").read_text())
    for name, paths in POST_BASELINE_ENROLLMENTS.items():
        row = vendor_consumers.get_consumer(registry, name)
        seen = {e["destination"] for e in row["entries"]} | {e.get("license") for e in row["entries"]}
        assert paths <= seen


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_observed_lock_topology_and_immutable_revision(name):
    registry = baseline_registry(json.loads((ROOT / "vendor-consumers.json").read_text()))
    heads = vendor_sync.validate_child_heads(json.loads((ROOT / "child-heads.json").read_text()))
    fixture = REFRESHED_FIXTURES.get(name, FIXTURES[name])
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
    workflow = (ROOT / "tests/fixtures/flutter_non_dart_20261031.yml").read_bytes()
    assert hashlib.sha256(workflow).hexdigest() == row["locked_ci"]["sha256"]
    assert row["locked_ci"]["workflow"] == ".github/workflows/non-dart.yml"
    assert b"python-vendor-locked:" in workflow
    assert workflow.count(b"vendor_sync.py check --manifest python/vendor.lock.json") == 2
    assert b"vendor_sync.py materialize --manifest python/vendor.lock.json" in workflow


def test_observation_does_not_enroll_new_repositories():
    registry = vendor_consumers.validate(json.loads((ROOT / "vendor-consumers.json").read_text()))
    locked = {row["repository"] for row in registry["consumers"] if row["state"] == "locked"}
    assert set(FIXTURES) == locked


def test_ironmate_adoption_preserves_previous_snapshot_and_checks_new_entries():
    name = "myon-bioinformatics/Ironmate"
    old = FIXTURES[name]
    current = REFRESHED_FIXTURES[name]
    assert old["sha"] == "7adcb80de69a701a252a6a5abc7407be3f8132f5"
    assert current["sha"] == "8f4b9e3e2b12a2a32c47daa37da0a5bc3ee7dcd3"
    assert len(old["lock"]["files"]) == 59
    assert current["lock"]["files"][:59] == old["lock"]["files"]
    added = current["lock"]["files"][59:]
    assert {row["destination"] for row in added} == {
        "vendor/fetch_module.py", "vendor/Aoi-LICENSE"}
    assert all(row["repository"] == "myon-bioinformatics/Aoi" and
               row["ref"] == row["commit"] == "9cfa841818de56a22048c8c6196f10f698118122"
               for row in added)
    registry = baseline_registry(json.loads((ROOT / "vendor-consumers.json").read_text()))
    assert vendor_consumers.compare(registry, current["lock"], name)["matches"]
    assert not vendor_consumers.compare(registry, old["lock"], name)["matches"]

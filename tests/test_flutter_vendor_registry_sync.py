"""Regression: parent Flutter registry equals the audited 782babef consumer lock.

An offline pinned snapshot, not a claim about all future Flutter main commits.
"""
import copy
import json
from pathlib import Path

import vendor_catalog
import vendor_consumers
import vendor_fleet_plan

ROOT = Path(__file__).resolve().parents[1]
FLUTTER = "myon-bioinformatics/flutter_navigation_basic"


def load(filename):
    return json.loads((ROOT / filename).read_text(encoding="utf-8"))


def test_flutter_registry_matches_pinned_post_migration_lock():
    registry = load("vendor-consumers.json")
    lock = load("tests/fixtures/flutter_vendor_lock_782babef.json")
    result = vendor_consumers.compare(registry, lock, FLUTTER)
    assert result["matches"] is True
    assert result["missing"] == result["unexpected"] == []
    entries = vendor_consumers.get_consumer(registry, FLUTTER)["entries"]
    assert len(entries) == len(lock["files"]) == 14
    assert {x["destination"] for x in entries} == {x["destination"] for x in lock["files"]}


def test_retired_git_inspector_is_explicit_skip_not_new_registration():
    catalog = load("vendor-catalog.json")
    registry = load("vendor-consumers.json")
    result = vendor_fleet_plan.plan(catalog, registry, active_consumers=[FLUTTER])
    retired = [x for x in result["rows"]
               if x["tool_repository"] == "myon-bioinformatics/myon-bioinformatics"
               and x["source"] == "git_inspector.py"]
    assert len(retired) == 1
    assert retired[0]["action"] == "skipped"
    assert retired[0]["reason"] == "retired_after_gh_identity_migration"
    assert result["counts"]["needs_registration"] == 6
    assert result["counts"]["skipped"] == 20


def test_regression_detects_removed_or_reintroduced_vendor_source():
    registry = load("vendor-consumers.json")
    lock = load("tests/fixtures/flutter_vendor_lock_782babef.json")
    tampered = copy.deepcopy(lock)
    tampered["files"].pop()
    result = vendor_consumers.compare(registry, tampered, FLUTTER)
    assert result["matches"] is False
    assert len(result["missing"]) == 1
    assert result["unexpected"] == []
    assert len(lock["files"]) == 14


def test_existing_pins_remain_sole_consumer_authority():
    registry = load("vendor-consumers.json")
    catalog = load("vendor-catalog.json")
    lock = load("tests/fixtures/flutter_vendor_lock_782babef.json")
    pins = vendor_catalog.compare(catalog, lock, FLUTTER)["tools"]
    assert {r["source"] for r in pins if r["enrollment"] == "enrolled" and
            r["comparison"] == "different"} == {"gh_identity.py", "xprobe.py", "yourself.py"}
    # Catalog recommendations do not overwrite or retarget the lock.
    assert vendor_consumers.compare(registry, lock, FLUTTER)["matches"]

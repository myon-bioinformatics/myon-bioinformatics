"""Regression for the parent-approved Flutter vendor and workflow baseline.

The relocated lock snapshot from #189 is unchanged by merged #194. The reviewed
workflow snapshot comes from #194 squash merge 31f33f42 on Flutter main.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

import vendor_catalog
import vendor_consumers
import vendor_fleet_plan
import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
FLUTTER = "myon-bioinformatics/flutter_navigation_basic"


def load(filename):
    return json.loads((ROOT / filename).read_text(encoding="utf-8"))


def test_flutter_registry_matches_pinned_post_migration_lock():
    registry = load("vendor-consumers.json")
    lock = load("tests/fixtures/flutter_vendor_lock_5ade883c.json")
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
    assert result["counts"]["skipped"] == 27


def test_regression_detects_removed_or_reintroduced_vendor_source():
    registry = load("vendor-consumers.json")
    lock = load("tests/fixtures/flutter_vendor_lock_5ade883c.json")
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
    lock = load("tests/fixtures/flutter_vendor_lock_5ade883c.json")
    pins = vendor_catalog.compare(catalog, lock, FLUTTER)["tools"]
    assert {r["source"] for r in pins if r["enrollment"] == "enrolled" and
            r["comparison"] == "different"} == {"gh_identity.py", "xprobe.py", "yourself.py"}
    # Catalog recommendations do not overwrite or retarget the lock.
    assert vendor_consumers.compare(registry, lock, FLUTTER)["matches"]


def test_historical_layout_requires_deliberate_parent_baseline_update():
    registry = load("vendor-consumers.json")
    old_lock = load("tests/fixtures/flutter_vendor_lock_782babef.json")
    assert not vendor_consumers.compare(registry, old_lock, FLUTTER)["matches"]
    current = vendor_consumers.get_consumer(registry, FLUTTER)
    assert current["lock"] == "python/vendor.lock.json"
    assert all(row["destination"].startswith("python/vendor/") for row in current["entries"])


@pytest.mark.parametrize("fault", [None, "removed", "disabled", "ignored", "command",
                                  "trigger", "digest", "revision", "missing"])
def test_real_reviewed_workflow_acceptance_and_refresh_rejections(monkeypatch, fault):
    registry = load("vendor-consumers.json")
    heads = load("child-heads.json")
    consumer = vendor_consumers.get_consumer(registry, FLUTTER)
    contract = consumer["locked_ci"]
    approved = (ROOT / "tests/fixtures/flutter_non_dart_31f33f42.yml").read_bytes()
    assert hashlib.sha256(approved).hexdigest() == contract["sha256"]
    recorded = heads["heads"][FLUTTER]
    assert recorded["repository_id"] == 739348522
    data = approved
    if fault == "removed":
        start = data.index(b"  python-vendor-locked:\n")
        end = data.index(b"  python-pytest:\n", start)
        data = data[:start] + data[end:]
    elif fault == "disabled":
        start = data.index(b"  python-vendor-locked:\n")
        end = data.index(b"  python-pytest:\n", start)
        condition = b"if: needs.changes.outputs.python == 'true' || needs.changes.outputs.workflow == 'true'"
        data = data[:start] + data[start:end].replace(condition, b"if: false") + data[end:]
    elif fault == "ignored":
        data = data.replace(b"  python-vendor-locked:\n", b"  python-vendor-locked:\n    continue-on-error: true\n")
    elif fault == "command":
        data = data.replace(b"vendor_sync.py check --manifest", b"vendor_sync.py update --manifest")
    elif fault == "trigger":
        data = data.replace(b'branches: ["main"]', b'branches: ["disabled"]')
    elif fault == "digest":
        data += b"\n# unreviewed change\n"
    if fault not in (None, "revision", "missing"):
        assert data != approved

    def snapshot(repository, revision, paths):
        assert repository == FLUTTER
        assert paths == [contract["workflow"]]
        if fault == "missing":
            raise ValueError(vendor_sync.PUBLIC_GIT_FETCH_ERROR)
        resolved = "0" * 40 if fault == "revision" else revision
        return resolved, {contract["workflow"]: (vendor_sync.git_blob(data), data)}

    monkeypatch.setattr(vendor_sync, "_public_git_snapshot", snapshot)
    monkeypatch.setattr(vendor_sync, "_fetch_repo_text", lambda *a, **k: None)
    before = copy.deepcopy(registry)
    report = vendor_sync.audit_child_workflow_templates(
        registry, heads, "template", repository_names=[FLUTTER])
    assert bool(report["failures"]) == (fault is not None)
    # Model a future main merge with identical reviewed bytes and a different SHA.
    proposed = {FLUTTER: dict(recorded, sha="b" * 40)}
    plan = vendor_sync.plan_heads_refresh(registry, heads, proposed, "template")
    if fault is None:
        assert report["missing"] == []
        assert plan["skipped"] == []
        assert plan["heads"][FLUTTER] == proposed[FLUTTER]
    else:
        assert plan["skipped"] == [{"repository": FLUTTER, "reason": "locked_ci_mismatch"}]
        assert plan["heads"][FLUTTER] == recorded
    assert registry == before  # Refresh cannot approve a new digest itself.

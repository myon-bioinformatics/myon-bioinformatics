"""Regression for the parent-approved Flutter vendor and workflow baseline.

Lock snapshot comes from Flutter #207 bounded-candidate repair, adopting browser-test-kit #85
main merge 8d8512fe. This historical lock still checks catalog behavior.
The approved Non-Dart workflow at Flutter #210 adds the existing Ironmate handoff
portable case; its immutable head also records the fetch-guard vendor rollout.
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
LOCK_FIXTURE = "tests/fixtures/flutter_vendor_lock_207_bounded.json"
WORKFLOW_FIXTURE = "tests/fixtures/flutter_non_dart_build_companions.yml"
GH_OPS_CATALOG_COMMIT = "09831b992dc91d83b15c44861a788e03b1e654c4"


def load(filename):
    # These regressions exercise the historical PR's reviewed contract.
    # Current fleet acceptance is covered by test_vendor_baseline_rollout.py.
    historical = json.loads((ROOT / "tests/fixtures/vendor_baseline_before_20261029.json").read_text())
    requested = filename
    if requested == "vendor-consumers.json":
        return historical["registry"]
    if requested == "child-heads.json":
        return historical["heads"]
    return json.loads((ROOT / filename).read_text(encoding="utf-8"))


def test_flutter_registry_matches_pinned_post_migration_lock():
    registry = load("vendor-consumers.json")
    lock = load(LOCK_FIXTURE)
    result = vendor_consumers.compare(registry, lock, FLUTTER)
    assert result["matches"] is True
    assert result["missing"] == result["unexpected"] == []
    entries = vendor_consumers.get_consumer(registry, FLUTTER)["entries"]
    assert len(entries) == len(lock["files"]) == 17
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
    assert result["counts"]["skipped"] == 32


def test_regression_detects_removed_or_reintroduced_vendor_source():
    registry = load("vendor-consumers.json")
    lock = load(LOCK_FIXTURE)
    tampered = copy.deepcopy(lock)
    tampered["files"].pop()
    result = vendor_consumers.compare(registry, tampered, FLUTTER)
    assert result["matches"] is False
    assert len(result["missing"]) == 1
    assert result["unexpected"] == []
    assert len(lock["files"]) == 17


def test_gh_ops_matches_catalog_recommendation():
    catalog = load("vendor-catalog.json")
    lock = load(LOCK_FIXTURE)
    pins = {row["source"]: row for row in vendor_catalog.compare(catalog, lock, FLUTTER)["tools"]}
    gh_ops = pins["gh_ops.py"]
    assert gh_ops["enrollment"] == "enrolled"
    assert gh_ops["comparison"] == "at_recommended"
    license_row = next(
        row for row in lock["files"]
        if row["destination"] == "python/vendor/myon-bioinformatics-LICENSE"
    )
    assert license_row["commit"] == GH_OPS_CATALOG_COMMIT


def test_existing_pins_remain_sole_consumer_authority():
    registry = load("vendor-consumers.json")
    catalog = load("vendor-catalog.json")
    lock = load(LOCK_FIXTURE)
    pins = vendor_catalog.compare(catalog, lock, FLUTTER)["tools"]
    assert {r["source"] for r in pins if r["enrollment"] == "enrolled" and
            r["comparison"] == "different"} == {
                "gh_identity.py",
                "scripts/image_diff.py",
                "xprobe.py",
                "yourself.py",
            }
    assert vendor_consumers.compare(registry, lock, FLUTTER)["matches"]


def test_historical_layout_requires_deliberate_parent_baseline_update():
    registry = load("vendor-consumers.json")
    old_lock = load("tests/fixtures/flutter_vendor_lock_782babef.json")
    assert not vendor_consumers.compare(registry, old_lock, FLUTTER)["matches"]
    old_shared = load("tests/fixtures/flutter_vendor_lock_f73c72eb.json")
    # The parent registry compares file topology, while exact source revisions
    # belong to each lock. Both generations intentionally keep 17 entries.
    assert vendor_consumers.compare(registry, old_shared, FLUTTER)["matches"]
    current_lock = load(LOCK_FIXTURE)
    old_pin = next(row for row in old_shared["files"]
                   if row["source"] == "scripts/browser_execution.mjs")
    new_pin = next(row for row in current_lock["files"]
                   if row["source"] == "scripts/browser_execution.mjs")
    assert old_pin["commit"] == "4be0f0d20b4cb6418ae97f64f3ca6aea6a75292c"
    assert new_pin["commit"] != old_pin["commit"]
    before_handoff = load("tests/fixtures/flutter_vendor_lock_1dd33afc.json")
    assert not vendor_consumers.compare(registry, before_handoff, FLUTTER)["matches"]
    current = vendor_consumers.get_consumer(registry, FLUTTER)
    assert current["lock"] == "python/vendor.lock.json"
    assert all(
        row["destination"].startswith("python/vendor/") or row["destination"].startswith("e2e/")
        for row in current["entries"]
    )


@pytest.mark.parametrize("baseline", ["historical", "current"])
@pytest.mark.parametrize("fault", [None, "removed", "disabled", "ignored", "command",
                                  "trigger", "digest", "revision", "missing"])
def test_real_reviewed_workflow_acceptance_and_refresh_rejections(monkeypatch, fault, baseline):
    if baseline == "current":
        registry = json.loads((ROOT / "vendor-consumers.json").read_text())
        heads = json.loads((ROOT / "child-heads.json").read_text())
        workflow_fixture = "tests/fixtures/flutter_non_dart_ci_python_235.yml"
        expected_sha = "47371550489436a2c35b45b273cca06b6d4216be"
    else:
        registry = load("vendor-consumers.json")
        heads = load("child-heads.json")
        workflow_fixture = WORKFLOW_FIXTURE
        expected_sha = "4b0fbfbc14eb2d478af1c7bc8d5a1942a4dad454"
    consumer = vendor_consumers.get_consumer(registry, FLUTTER)
    contract = consumer["locked_ci"]
    approved = (ROOT / workflow_fixture).read_bytes()
    assert hashlib.sha256(approved).hexdigest() == contract["sha256"]
    recorded = heads["heads"][FLUTTER]
    assert recorded["repository_id"] == 739348522
    assert recorded["sha"] == expected_sha
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
    proposed = {FLUTTER: dict(recorded, sha="b" * 40)}
    plan = vendor_sync.plan_heads_refresh(registry, heads, proposed, "template")
    if fault is None:
        assert report["missing"] == []
        assert plan["skipped"] == []
        assert plan["heads"][FLUTTER] == proposed[FLUTTER]
    else:
        assert plan["skipped"] == [{"repository": FLUTTER, "reason": "locked_ci_mismatch"}]
        assert plan["heads"][FLUTTER] == recorded
    assert registry == before


def test_shared_execution_adopts_main_merge_with_existing_helper_and_license():
    lock = load(LOCK_FIXTURE)
    catalog = load("vendor-catalog.json")
    rows = {row["destination"]: row for row in lock["files"]}
    module = rows["e2e/utils/browser_execution.mjs"]
    assert module["repository"] == "myon-bioinformatics/browser-test-kit"
    assert module["ref"] == module["commit"] == "8d8512fefea38993e5b3695c0706075cf05071ad"
    assert module["source"] == "scripts/browser_execution.mjs"
    for path in ("e2e/utils/interaction.mjs", "python/vendor/browser-test-kit-LICENSE"):
        assert rows[path]["repository"] == module["repository"]
    compared = vendor_catalog.compare(catalog, lock, FLUTTER)["tools"]
    execution = next(row for row in compared if row["source"] == module["source"])
    assert execution["enrollment"] == "enrolled"
    assert execution["comparison"] == "at_recommended"

"""Keep the reviewed fleet observation baseline internally consistent.

These fixtures freeze claims for offline regression, not proof of upstream
provenance. Parent CI separately reads the immutable source and child blobs.
"""
import copy, hashlib, json
from pathlib import Path

import pytest

import vendor_consumers
import vendor_sync

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "tests/fixtures/fleet_vendor_locks_20261029.json").read_text())
# Preserve historical fixtures; reviewed consumer snapshots override them below.
REFRESHED_FIXTURES = {
    "myon-bioinformatics/flutter_navigation_basic": json.loads(
        (ROOT / "tests/fixtures/flutter_adopted_vendor_235.json").read_text()),
    "myon-bioinformatics/Ironmate": json.loads(
        (ROOT / "tests/fixtures/ironmate_adopted_vendor_20261031.json").read_text()),
}
REFRESHED_FIXTURES.update(json.loads(
    (ROOT / "tests/fixtures/reviewed_child_locks_20261103.json").read_text()))

# Reviewed, merged main snapshots for the repository-rename rollout. Historical
# fixtures and their historical-adoption tests below remain unchanged.
ROLLOUT_FIXTURES = json.loads(
    (ROOT / "tests/fixtures/reviewed_child_locks_20261106.json").read_text())
# Later reviewed repairs supersede only these two merged child snapshots.
ROLLOUT_FIXTURES.update(json.loads(
    (ROOT / "tests/fixtures/reviewed_pin_repairs_20261106.json").read_text()))

LATEST_FIXTURES = json.loads(
    (ROOT / 'tests/fixtures/reviewed_mcp_lock_20261109.json').read_text())
LATEST_FIXTURES.update(json.loads(
    (ROOT / 'tests/fixtures/reviewed_mcp_planner_lock_20261109.json').read_text()))
REFRESHED_FIXTURES.update(LATEST_FIXTURES)

# Registry entries enrolled after the 2026-10-29 observation. The parent registry
# must land before the child lock (the child CI reads the live registry), so these
# are excluded from the frozen-baseline comparison until the baseline is re-observed.
POST_BASELINE_ENROLLMENTS = {
    "myon-bioinformatics/Ironmate": {"vendor/fetch_module.py", "vendor/Aoi-LICENSE"},
    "myon-bioinformatics/myon-bioinformatics.github.io": {"tools/vendor/web-ui-generator/web_ui.py"},
    # The parent audit dashboard (ci_status.py --audit --html) renders through the vendored web_ui.py.
    "myon-bioinformatics/myon-bioinformatics": {"vendor/web_ui.py", "vendor/web_ui-LICENSE"},
    "myon-bioinformatics/mcp-toolcall-lab": {"vendor/web_ui.py", "vendor/web-ui-LICENSE"},
}


# Registry entries retired after the reviewed snapshots, and what replaced them. As with the enrollments above, the
# registry lands first, so a reviewed lock that holds none of the replacements is compared with the retired entries
# until its consumer is re-observed.
POST_BASELINE_REPLACEMENTS = {
    # web-ui handed its card-flow code to ascii_artist: Ironmate takes the module and the sample from there.
    "myon-bioinformatics/Ironmate": {
        "retired": [
            {"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "tool/flow_diagram.py", "destination": "vendor/web_ui_flow.py", "license": "vendor/web-ui-LICENSE"},
            {"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "examples/pr-flow.json", "destination": "examples/flow.json"},
            {"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "tool/flow_diagram.py", "destination": "vendor/web_ui/tool/flow_diagram.py"},
            {"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "tool/flow_patch.py", "destination": "vendor/web_ui/tool/flow_patch.py"},
            {"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "js/flow.cjs", "destination": "vendor/web_ui/js/flow.cjs"},
            {"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "js/flow-patch.cjs", "destination": "vendor/web_ui/js/flow-patch.cjs"},
        ],
        "added": [{"repository": "myon-bioinformatics/ascii_artist", "source": "examples/pr-flow.json", "destination": "examples/flow.json"}],
    },
}


def _key(entry):
    return entry["repository"].casefold(), entry["source"], entry["destination"].casefold()


def before_replacements(registry, name, lock):
    """``registry`` as ``name``'s reviewed ``lock`` last saw it: the retired entries back if the lock holds no replacement."""
    change = POST_BASELINE_REPLACEMENTS.get(name)
    if not change or {_key(e) for e in change["added"]} & {_key(e) for e in lock["files"]}:
        return registry
    registry = copy.deepcopy(registry)
    consumer = next(c for c in registry["consumers"] if c["repository"].casefold() == name.casefold())
    added = {_key(e) for e in change["added"]}
    consumer["entries"] = [e for e in consumer["entries"] if _key(e) not in added] + copy.deepcopy(change["retired"])
    return registry


def baseline_registry(registry):
    registry = copy.deepcopy(registry)
    for consumer in registry["consumers"]:
        name = consumer["repository"]
        fixture = REFRESHED_FIXTURES.get(name, FIXTURES.get(name))
        adopted = {e["destination"] for e in fixture["lock"]["files"]} if fixture else set()
        # A reviewed refresh may still predate a later, explicitly listed addition.
        # Once the refreshed fixture adopts that path, it is checked normally.
        drop = POST_BASELINE_ENROLLMENTS.get(name, set()) - adopted
        consumer["entries"] = [e for e in consumer["entries"]
                               if e["destination"] not in drop and e.get("license") not in drop]
    return registry


def test_post_baseline_enrollments_are_registered():
    registry = json.loads((ROOT / "vendor-consumers.json").read_text())
    for name, paths in POST_BASELINE_ENROLLMENTS.items():
        row = vendor_consumers.get_consumer(registry, name)
        seen = {e["destination"] for e in row["entries"]} | {e.get("license") for e in row["entries"]}
        assert paths <= seen
    for name, change in POST_BASELINE_REPLACEMENTS.items():
        keys = {_key(e) for e in vendor_consumers.get_consumer(registry, name)["entries"]}
        assert {_key(e) for e in change["added"]} <= keys and not {_key(e) for e in change["retired"]} & keys


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_observed_lock_topology_and_immutable_revision(name):
    registry = baseline_registry(json.loads((ROOT / "vendor-consumers.json").read_text()))
    heads = vendor_sync.validate_child_heads(json.loads((ROOT / "child-heads.json").read_text()))
    fixture = LATEST_FIXTURES.get(name, ROLLOUT_FIXTURES.get(name, REFRESHED_FIXTURES.get(name, FIXTURES[name])))
    lock = vendor_sync.validate(fixture["lock"])
    registry = before_replacements(registry, name, lock)
    observed = json.loads((ROOT / "tests/fixtures/parent_observation_20261102.json").read_text())
    expected_sha = fixture["sha"]
    if name in ROLLOUT_FIXTURES or name in LATEST_FIXTURES:
        digest = hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert digest == fixture["lock_sha256"]
        assert heads["heads"][name]["repository_id"] == fixture["repository_id"]
    elif name in observed:
        digest = hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert digest == observed[name]["lock_sha256"]
        expected_sha = observed[name]["sha"]
    assert heads["heads"][name]["sha"] == expected_sha
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
    workflow = (ROOT / "tests/fixtures/flutter_non_dart_ci_python_241.yml").read_bytes()
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
    registry = before_replacements(baseline_registry(json.loads((ROOT / "vendor-consumers.json").read_text())), name, current["lock"])
    assert vendor_consumers.compare(registry, current["lock"], name)["matches"]
    assert not vendor_consumers.compare(registry, old["lock"], name)["matches"]


def test_flutter_python_ci_adoption_preserves_the_adopted_vendor_lock():
    name = "myon-bioinformatics/flutter_navigation_basic"
    old = FIXTURES[name]
    current = REFRESHED_FIXTURES[name]
    assert old["sha"] == "075e08530e6c02b9f0b2194922f244ff047ab23f"
    assert current["lock"] == old["lock"]
    heads = json.loads((ROOT / "child-heads.json").read_text())
    assert heads["heads"][name]["pull_number"] == ROLLOUT_FIXTURES[name]["pull_number"] == 241


def test_rename_rollout_is_exact_without_identity_fallback():
    registry = json.loads((ROOT / "vendor-consumers.json").read_text())
    heads = vendor_sync.validate_child_heads(json.loads((ROOT / "child-heads.json").read_text()))
    assert set(ROLLOUT_FIXTURES) == {
        "myon-bioinformatics/" + name for name in (
            "Ironmate", "Aoi", "flutter_navigation_basic", "Err-o-RoadRunner",
            "myon-bioinformatics.github.io", "PySaiyanWeBoo", "BrowserTestKit")
    }
    for name, fixture in ROLLOUT_FIXTURES.items():
        assert fixture["sha"] == heads["heads"][name]["sha"]
        assert vendor_consumers._compare_exact(before_replacements(registry, name, fixture["lock"]), fixture["lock"], name)["matches"]
        assert all(row["ref"] == row["commit"] for row in fixture["lock"]["files"])

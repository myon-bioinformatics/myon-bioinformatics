"""Vendor registry contracts: enrollment, baselines, observation transitions, snapshots, packaging, renames and recreate failures."""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]
import vendor_catalog
import vendor_consumers
import vendor_consumers as c
import vendor_consumers as consumers
import vendor_sync
import vendor_sync as s
import vendor_sync as sync
from test_vendor_child_bootstrap import child_fixture
from test_vendor_sync import _staging_left, entry, lock


# ═══ · vendor adopted enrollment ═════════════════════════════════════════════════════════════════════════
# Advisory skips cannot revoke parent-registered adopted copies.

def test_ironmate_registered_contract_ignores_recommendation_skips():
    registry = json.loads((ROOT / 'vendor-consumers.json').read_text())
    catalog = json.loads((ROOT / 'vendor-catalog.json').read_text())
    lock = json.loads((ROOT / 'tests/fixtures/ironmate-flow-ascii-artist.lock.json').read_text())
    ironmate_rows = [t for t in catalog['tools'] if t['repository'].casefold() == 'myon-bioinformatics/ironmate']
    assert len(ironmate_rows) == 1 and ironmate_rows[0]['source'] == 'pillow_min.py'
    assert 'myon-bioinformatics/Ironmate' not in ironmate_rows[0].get('consumers', {})
    assert sync.verify_child_lock_against_parent(registry, catalog, lock, 'myon-bioinformatics/Ironmate') == sync.validate(lock)
    changed = copy.deepcopy(lock)
    changed['files'].pop()
    with pytest.raises(ValueError, match='topology mismatch'):
        sync.verify_child_lock_against_parent(registry, catalog, changed, 'myon-bioinformatics/Ironmate')


@pytest.mark.parametrize('fault', [None, 'file', 'file_and_lock', 'unregistered', 'source_failure'])
def test_skipped_adopted_copy_still_requires_source_and_child_integrity(tmp_path, monkeypatch, fault):
    parent, child, lock = child_fixture(tmp_path, monkeypatch)
    catalog_path = parent / 'vendor-catalog.json'
    catalog = json.loads(catalog_path.read_text())
    for row in catalog['tools']:
        row['consumers']['owner/child'] = {'enrollment': 'skipped', 'reason': 'parent_managed_tool'}
    catalog_path.write_text(json.dumps(catalog))
    if fault in ('file', 'file_and_lock'):
        altered = b'altered\n'
        (child / 'vendor/tool.py').write_bytes(altered)
        if fault == 'file_and_lock':
            lock['files'][0]['blob_sha'] = sync.git_blob(altered)
            lock['files'][0]['sha256'] = sync.hashlib.sha256(altered).hexdigest()
            (child / 'vendor.lock.json').write_text(json.dumps(lock))
    if fault == 'unregistered':
        lock['files'][0]['destination'] = 'vendor/unregistered.py'
        (child / 'vendor.lock.json').write_text(json.dumps(lock))
    if fault == 'source_failure':
        def fail(*args):
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR)
        monkeypatch.setattr(sync, '_public_git_snapshot', fail)
    if fault is None:
        sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)
    else:
        with pytest.raises(ValueError):
            sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)


# ═══ · vendor baseline rollout ═══════════════════════════════════════════════════════════════════════════
# Keep the reviewed fleet observation baseline internally consistent.
#
# These fixtures freeze claims for offline regression, not proof of upstream
# provenance. Parent CI separately reads the immutable source and child blobs.

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
# 2026-11-14: the four njs_launch children, audited at their merged main and re-observed. Their adopted locks equal
# the reviewed njs-<repo>-new.lock.json targets; the retired transitions stay in the same fixture (recorded_before_njs_adoption).
NJS_ADOPTION = json.loads((ROOT / 'tests/fixtures/njs_launch_adoption_20261114.json').read_text())
LATEST_FIXTURES.update({name: {**row, 'lock': json.loads((ROOT / row['lock_fixture']).read_text())}
                        for name, row in NJS_ADOPTION['adopted'].items()})
REFRESHED_FIXTURES.update(LATEST_FIXTURES)


def recorded_before_njs_adoption():
    """child-heads.json and the observation transitions as they stood before that re-observation."""
    heads = json.loads((ROOT / 'child-heads.json').read_text())
    heads['heads'].update(copy.deepcopy(NJS_ADOPTION['recorded']['heads']))
    return heads, copy.deepcopy(NJS_ADOPTION['recorded']['transitions'])

# Registry entries enrolled after the 2026-10-29 observation. The parent registry
# must land before the child lock (the child CI reads the live registry), so these
# are excluded from the frozen-baseline comparison until the baseline is re-observed.
POST_BASELINE_ENROLLMENTS = {
    "myon-bioinformatics/Ironmate": {"vendor/fetch_module.py", "vendor/Aoi-LICENSE"},
    # The parent audit dashboard (ci_status.py --audit --html) renders through the vendored pysaiyanweboo.py.
    "myon-bioinformatics/myon-bioinformatics": {"vendor/pysaiyanweboo.py", "vendor/pysaiyanweboo-LICENSE"},
    "myon-bioinformatics/mcp-toolcall-lab": {"vendor/pysaiyanweboo.py", "vendor/pysaiyanweboo-LICENSE"},
}


# Registry entries retired after the reviewed snapshots, and what replaced them. Unlike pure additions, PR validation
# compares retired entries strictly with the merged child, so the child lands first; until the parent re-observes it,
# a reviewed lock that holds none of the replacements is compared with the retired entries.
POST_BASELINE_REPLACEMENTS = {
    # Exact old/new topology; immutable historical observations stay unchanged.
    "myon-bioinformatics/mcp-toolcall-lab": {
        "retired": [{"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "web_ui.py", "destination": "vendor/web_ui.py", "license": "vendor/web-ui-LICENSE"}],
        "added": [{"repository": "myon-bioinformatics/PySaiyanWeBoo", "source": "pysaiyanweboo.py", "destination": "vendor/pysaiyanweboo.py", "license": "vendor/pysaiyanweboo-LICENSE"}],
    },
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
    # Current desired topology can retire copies while frozen observations remain
    # bound to their reviewed old contract. Never rewrite historical fixtures.
    migrations = json.loads((ROOT / 'vendor-observation-transitions.json').read_text())
    for transition in migrations['transitions']:
        if transition['consumer'] == name and name in {
            'myon-bioinformatics/Aoi', 'myon-bioinformatics/myon-bioinformatics.github.io',
            'myon-bioinformatics/mcp-toolcall-lab', 'myon-bioinformatics/PySaiyanWeBoo',
            'myon-bioinformatics/markdown', 'myon-bioinformatics/xprobe'
        }:
            registry = copy.deepcopy(registry)
            row = next(c for c in registry['consumers'] if c['repository'] == name)
            row['entries'] = copy.deepcopy(transition['old_entries'])
            assert vendor_consumers.compare(registry, lock, name)['matches']
            return registry
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
        if name in NJS_ADOPTION['adopted']:
            continue  # re-observed later; test_observed_lock_topology_and_immutable_revision checks the newer snapshot
        assert fixture["sha"] == heads["heads"][name]["sha"]
        assert vendor_consumers._compare_exact(before_replacements(registry, name, fixture["lock"]), fixture["lock"], name)["matches"]
        assert all(row["ref"] == row["commit"] for row in fixture["lock"]["files"])


# ═══ · vendor dispatch packaging ═════════════════════════════════════════════════════════════════════════
# Explicit module packaging must not make unrelated source adapters importable.

SPEC = importlib.util.spec_from_file_location("vendor_dispatch", ROOT / "pilot/vendor_dispatch.py")
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)
PROJECT = """[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"
[project]
name = "sample"
requires-python = ">=3.11"
dependencies = []
[tool.setuptools]
py-modules = ["xprobe"]
"""
ENV = dispatch.Env((3, 11, 0), False, False, True, 26)

class ExplicitModules(unittest.TestCase):
    def test_installed_module_uses_uv(self):
        self.assertEqual(dispatch.classify("xprobe.py", ENV, dispatch.Probe(PROJECT, None)).action, "uv")

    def test_adapter_uses_verified_fetch(self):
        self.assertEqual(dispatch.classify("scripts/xprobe_pytest.py", ENV, dispatch.Probe(PROJECT, None)).action, "fetch")

    def test_missing_explicit_metadata_keeps_existing_import_check(self):
        project = PROJECT.split("[tool.setuptools]")[0]
        self.assertEqual(dispatch.classify("adapter.py", ENV, dispatch.Probe(project, None)).action, "uv")

    def test_offline_and_no_site_do_not_fetch(self):
        for env in (ENV._replace(offline=True), ENV._replace(no_site=True)):
            self.assertEqual(dispatch.classify("scripts/xprobe_pytest.py", env, dispatch.Probe(PROJECT, None)).action, "skip")

    def test_discovery_root_retains_original_source(self):
        project = PROJECT.split("[tool.setuptools]")[0] + '[tool.setuptools]\npackage-dir = { "" = "tools" }\n'
        probe = dispatch.Probe(project, None)
        self.assertEqual(dispatch.classify("scripts/check_png.py", ENV, probe).action, "fetch")
        self.assertEqual(dispatch.classify("tools/btk.py", ENV, probe).action, "uv")
        self.assertEqual(dispatch.classify("toolsmith/btk.py", ENV, probe).action, "fetch")

    def test_multiple_package_roots_do_not_guess(self):
        project = PROJECT.split("[tool.setuptools]")[0] + '[tool.setuptools]\npackage-dir = { "" = "tools", "other" = "scripts" }\n'
        self.assertEqual(dispatch.classify("scripts/other.py", ENV, dispatch.Probe(project, None)).action, "uv")

    def test_fetch_not_install_and_failure_not_hidden(self):
        catalog = {"schema": "vendor-catalog/1", "tools": [{"repository": "example/xprobe", "source": "scripts/xprobe_pytest.py", "commit": "a"*40}]}
        with patch.object(dispatch, "load_probe", return_value=dispatch.Probe(PROJECT, None)), patch.object(dispatch, "fetch_source", return_value=("a"*40, "b"*64)) as fetch:
            rows = dispatch.dispatch(catalog, ENV, None, None, lambda *a: self.fail("must not install"))
        self.assertEqual(rows[0]["action"], "fetch")
        self.assertEqual(rows[0]["sha256"], "b"*64)
        fetch.assert_called_once()
        with patch.object(dispatch, "load_probe", return_value=dispatch.Probe(PROJECT, None)), patch.object(dispatch, "fetch_source", side_effect=RuntimeError("bad source")):
            rows = dispatch.dispatch(catalog, ENV, None, None, lambda *a: self.fail("must not install"))
        self.assertEqual(rows[0]["action"], "error")


# ═══ · vendor integration baselines ══════════════════════════════════════════════════════════════════════
# Keep the Aoi and Flutter source/LICENSE topology together across integration.

@pytest.mark.parametrize("consumer,fixture,repository,source,destination,license_path,commit", [
    ("Aoi", "aoi_vendor_lock_85243d6d.json", "web-ui", "web_ui.py", "vendor/web_ui.py",
     "vendor/web-ui-LICENSE", "573b4ca07a916c4a44695dbd5739da36b902d8f2"),
    ("flutter_navigation_basic", "flutter_vendor_lock_207_bounded.json", "myon-bioinformatics",
     "gh_ops.py", "python/vendor/gh_ops.py", "python/vendor/myon-bioinformatics-LICENSE",
     "09831b992dc91d83b15c44861a788e03b1e654c4"),
])
def test_adopted_sources_keep_their_license_and_exact_pin(
        consumer, fixture, repository, source, destination, license_path, commit):
    # Preserve the historical source/license assertions against their reviewed topology.
    # The current full fleet is checked by test_vendor_contracts.py.
    registry = json.loads((ROOT / "tests/fixtures/vendor_baseline_before_20261029.json").read_text())["registry"]
    lock = json.loads((ROOT / "tests/fixtures" / fixture).read_text())
    name = "myon-bioinformatics/" + consumer
    assert vendor_consumers.compare(registry, lock, name)["matches"]
    rows = {row["destination"]: row for row in lock["files"]}
    for path, expected_source in ((destination, source), (license_path, "LICENSE")):
        row = rows[path]
        assert row["repository"] == "myon-bioinformatics/" + repository
        assert row["source"] == expected_source
        assert row["ref"] == row["commit"] == commit
    without_license = copy.deepcopy(lock)
    without_license["files"] = [r for r in lock["files"] if r["destination"] != license_path]
    assert not vendor_consumers.compare(registry, without_license, name)["matches"]


def test_explicit_source_validation_keeps_network_pin_verification(monkeypatch):
    document = {"schema": "vendor-catalog/1", "tools": [{
        "repository": "owner/tool", "source": "tool.py", "commit": "a" * 40,
        "reason": "reviewed fixture",
    }]}
    checked = []
    monkeypatch.setattr(
        vendor_sync,
        "public_commit_exists",
        lambda repo, commit, **kwargs: checked.append((repo, commit, kwargs.get("entry_label"))),
    )
    vendor_catalog.validate(document, verify_pins=True, allow_legacy_implicit_source=False)
    assert checked == [("owner/tool", "a" * 40, "tool.py@" + ("a" * 40)[:7])]
    document["tools"][0].pop("source")
    with pytest.raises(ValueError, match="source required"):
        vendor_catalog.validate(document, verify_pins=True)
    assert len(checked) == 1


# ═══ · vendor observation transitions ════════════════════════════════════════════════════════════════════
# Frozen observations retain strict byte audits during an explicit contract replacement.

NAME = 'myon-bioinformatics/Ironmate'

def inputs():
    load = lambda p: json.loads((ROOT / p).read_text())
    return (load('vendor-consumers.json'), load('child-heads.json'),
            load('vendor-observation-transitions.json'), load('tests/fixtures/ironmate-observation-transition-old.lock.json'))

def resolve(registry, heads, manifest, lock, selected=None):
    with patch.object(sync, '_load_recorded_child_lock', return_value=lock):
        return sync._registry_for_recorded_transitions(registry, heads, manifest,
                                                       [NAME] if selected is None else selected)

def test_exact_old_observation_uses_old_contract_without_mutating_target():
    registry, heads, manifest, lock = inputs(); original = copy.deepcopy(registry)
    result, reports = resolve(registry, heads, manifest, lock)
    assert registry == original
    assert consumers.compare(result, lock, NAME)['matches']
    assert not consumers.compare(registry, lock, NAME)['matches']
    assert reports[0]['recorded_sha'] == heads['heads'][NAME]['sha']

@pytest.mark.parametrize('change', ['digest','source','missing','mixed'])
def test_changed_old_lock_fails_closed(change):
    registry, heads, manifest, lock = inputs()
    if change == 'digest': lock['files'][0]['sha256'] = '0' * 64
    if change == 'source': lock['files'][0]['source'] = 'other.py'
    if change == 'missing': lock['files'].pop()
    if change == 'mixed': lock['files'].append(copy.deepcopy(lock['files'][0]))
    with pytest.raises(ValueError, match='lock changed'): resolve(registry, heads, manifest, lock)

def test_unknown_observation_cannot_use_old_contract():
    registry, heads, manifest, lock = inputs(); heads['heads'][NAME]['sha'] = '1' * 40
    result, reports = resolve(registry, heads, manifest, lock)
    assert not reports and not consumers.compare(result, lock, NAME)['matches']

def test_identity_change_is_rejected():
    registry, heads, manifest, lock = inputs(); heads['heads'][NAME]['repository_id'] += 1
    with pytest.raises(ValueError, match='repository id'): resolve(registry, heads, manifest, lock)

def test_changed_desired_contract_is_rejected():
    registry, heads, manifest, lock = inputs()
    next(c for c in registry['consumers'] if c['repository'] == NAME)['entries'][0]['source'] = 'other.py'
    with pytest.raises(ValueError, match='target contract'): resolve(registry, heads, manifest, lock)

def test_unmonitored_repo_is_never_read():
    registry, heads, manifest, lock = inputs()
    with patch.object(sync, '_load_recorded_child_lock', side_effect=AssertionError('unexpected fetch')):
        _, reports = sync._registry_for_recorded_transitions(registry, heads, manifest, [])
    assert not reports

def test_failed_read_is_not_converted_to_permission():
    registry, heads, manifest, lock = inputs()
    with patch.object(sync, '_load_recorded_child_lock', side_effect=ValueError('unavailable')):
        with pytest.raises(ValueError, match='unavailable'):
            sync._registry_for_recorded_transitions(registry, heads, manifest, [NAME])

@pytest.mark.parametrize('bad', ['source', 'child', 'clean', 'unmerged'])
def test_selected_old_contract_still_rejects_tampered_bytes(bad):
    registry, heads, manifest, lock = inputs()
    registry, _ = resolve(registry, heads, manifest, lock)
    # Use one exact entry to exercise the unchanged source and placed-byte audit.
    item = copy.deepcopy(lock['files'][0]); data = b'reviewed source bytes\n'
    item['sha256'] = hashlib.sha256(data).hexdigest(); item['blob_sha'] = sync.git_blob(data)
    lock = {'schema': 'vendor-lock/1', 'files': [item]}
    consumer = consumers.get_consumer(registry, NAME)
    consumer['entries'] = [{k:item[k] for k in ('repository','source','destination')}]
    registry = {'schema':'vendor-consumers/1', 'consumers':[consumer]}
    def snapshot(repo, revision, paths):
        source = repo == item['repository']; blobdata = data
        if (source and bad == 'source') or (not source and bad == 'child'): blobdata += b'tamper'
        return revision, {p:(sync.git_blob(blobdata), blobdata) for p in paths}
    with patch.object(sync,'_load_recorded_child_lock',return_value=lock), \
         patch.object(sync,'_ensure_public_source_repository'), \
         patch.object(sync,'commit_merged_into_default', side_effect=ValueError('source not merged') if bad == 'unmerged' else None), \
         patch.object(sync,'_public_git_snapshot',side_effect=snapshot):
        report = sync.audit_adopted_child_files(registry, heads, [NAME])
    assert report['failure_count'] == (0 if bad == 'clean' else 1)

def test_new_observation_uses_desired_contract_without_transition():
    registry, heads, manifest, _ = inputs()
    heads['heads'][NAME]['sha'] = '2' * 40
    lock = json.loads((ROOT/'tests/fixtures/ironmate-flow-ascii-artist.lock.json').read_text())
    result, reports = resolve(registry, heads, manifest, lock)
    assert not reports and consumers.compare(result, lock, NAME)['matches']

def test_parent_run_exposes_transition_and_still_invokes_old_contract_audit(monkeypatch, tmp_path):
    old = _registry(); desired = _registry()
    desired['consumers'][0]['entries'][0]['source'] = 'replacement.py'
    observed = {'schema':'vendor-lock/1','files': [{**old['consumers'][0]['entries'][0], 'ref':'3'*40, 'commit':'3'*40, 'blob_sha':'4'*40, 'sha256':'5'*64}]}
    original_resolver = sync._registry_for_recorded_transitions
    def resolver(*args, **kwargs):
        with patch.object(sync, '_load_recorded_child_lock', return_value=observed):
            return original_resolver(*args, **kwargs)
    monkeypatch.setattr(sync, '_registry_for_recorded_transitions', resolver)
    manifest = {'schema':'vendor-observation-transitions/1','transitions':[{
        'consumer':CHILD,'repository_id':5,'recorded_sha':'1'*40,
        'lock_sha256':sync._transition_digest(observed),
        'target_consumer_sha256':sync._transition_digest(consumers.get_consumer(desired,CHILD)),
        'old_entries':old['consumers'][0]['entries'], 'reason':'test reviewed replacement',
    }]}
    (tmp_path/'vendor-observation-transitions.json').write_text(json.dumps(manifest))
    report, seen = _run(monkeypatch,tmp_path,desired,adopted=old)
    assert report['failure_count'] == 0
    assert report['observation_transitions'][0]['consumer'] == CHILD
    assert seen['registry'] == old


# ═══ · vendor pending enrollment ═════════════════════════════════════════════════════════════════════════
# PR-added registry entries are pending enrollment until the child adopts them.

CHILD = "owner/child"


def _registry(*extra):
    entries = [{"repository": "owner/tool", "source": "tool.py", "destination": "vendor/tool.py"}]
    entries += list(extra)
    return {"schema": "vendor-consumers/1", "consumers": [
        {"repository": CHILD, "state": "locked", "lock": "vendor.lock.json", "entries": entries},
        {"repository": sync.PARENT_REPOSITORY, "state": "locked", "lock": "vendor.lock.json",
         "entries": [{"repository": "owner/tool", "source": "tool.py", "destination": "vendor/tool.py"}]},
    ]}


NEW = {"repository": "owner/other", "source": "x.py", "destination": "vendor/x.py"}


def test_pure_addition_is_pending():
    pending = sync.pending_enrollments(_registry(NEW), _registry())
    assert pending == [{"consumer": CHILD, **NEW}]
    stripped = sync._registry_without_pending(_registry(NEW), pending)
    assert stripped == _registry()


def test_no_base_change_has_no_pending():
    assert sync.pending_enrollments(_registry(), _registry()) == []


def test_license_only_addition_is_removed_from_normalized_audit_registry():
    base = _registry()
    head = copy.deepcopy(base)
    head["consumers"][0]["entries"][0]["license"] = "vendor/tool-LICENSE"
    original = copy.deepcopy(head)
    pending = sync.pending_enrollments(head, base)
    assert pending == [{"consumer": CHILD, "repository": "owner/tool",
                        "source": "LICENSE", "destination": "vendor/tool-LICENSE"}]
    assert sync._registry_without_pending(head, pending) == base
    assert head == original


def test_modified_entry_is_not_pending():
    head = _registry(NEW)
    head["consumers"][0]["entries"][0]["destination"] = "vendor/renamed.py"
    assert sync.pending_enrollments(head, _registry()) == []


def test_removed_entry_is_not_pending():
    base = _registry(NEW)
    assert sync.pending_enrollments(_registry(), base) == []


def test_new_consumer_and_parent_entries_are_not_pending():
    base = _registry()
    head = copy.deepcopy(base)
    head["consumers"][1]["entries"].append(NEW)
    head["consumers"].append({"repository": "owner/new", "state": "locked",
                              "lock": "vendor.lock.json", "entries": [NEW]})
    assert sync.pending_enrollments(head, base) == []


def _run(monkeypatch, tmp_path, registry, *, adopted=None, **kwargs):
    seen = {}
    adopted = _registry() if adopted is None else adopted
    def load_recorded(registry, document, name, **kwargs):
        assert name == CHILD
        assert document["heads"][CHILD]["sha"] == "1" * 40
        return {"files": adopted["consumers"][0]["entries"]}
    monkeypatch.setattr(sync, "_load_recorded_child_lock", load_recorded)
    sha = "1" * 40
    head = {"sha": sha, "repository_id": 5}
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "vendor-child-ci.workflow.yml").write_text("name: stub\n")
    monkeypatch.setattr(sync, "monitored_repositories", lambda *a, **k: [CHILD])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *a, **k: dict(head))
    monkeypatch.setattr(sync, "validate_child_heads", lambda d: d)
    monkeypatch.setattr(sync, "verify_repository_id_identity", lambda **k: None)
    monkeypatch.setattr(sync, "_split_extra_heads", lambda *a, **k: ([], 0))
    monkeypatch.setattr(sync, "_copy_parent_vendor_baseline", lambda *a: tmp_path)
    monkeypatch.setattr(sync, "_offline_parent_vendor_check", lambda *a: {})

    def audit(audit_registry, *a, **k):
        seen["registry"] = audit_registry
        normalized = vendor_consumers.validate(audit_registry)
        child = [c for c in normalized["consumers"] if c["repository"] == CHILD][0]
        ok = child["entries"] == vendor_consumers.validate(adopted)["consumers"][0]["entries"]
        failures = [] if ok else [{"repository": CHILD, "message": "consumer topology mismatch against parent registry"}]
        return [], {"failures": [], "missing": []}, {"checked": [], "failures": failures,
                                                     "failure_count": len(failures)}
    monkeypatch.setattr(sync, "_baseline_audit_blocked", audit)
    document = {"schema": sync.CHILD_HEADS_SCHEMA, "ref": "main", "heads": {CHILD: dict(head)}}
    return sync.run_parent_child_ci(registry, document, parent_root=tmp_path,
                                    skip_source_diff=True, **kwargs), seen


def test_pr_validation_with_base_reports_pending_and_passes(monkeypatch, tmp_path):
    report, _ = _run(monkeypatch, tmp_path, _registry(NEW), pr_validation=True,
                     base_registry=_registry())
    assert report["pending_enrollment_count"] == 1
    assert report["failure_count"] == 0


def test_pr_validation_without_base_stays_strict(monkeypatch, tmp_path):
    report, _ = _run(monkeypatch, tmp_path, _registry(NEW), pr_validation=True)
    assert report["pending_enrollment_count"] == 0
    assert report["failure_count"] == 1


def test_license_only_pr_addition_is_report_only(monkeypatch, tmp_path):
    head = _registry()
    head["consumers"][0]["entries"][0]["license"] = "vendor/tool-LICENSE"
    report, seen = _run(monkeypatch, tmp_path, head, pr_validation=True,
                        base_registry=_registry())
    assert report["pending_enrollment_count"] == 1
    assert report["failure_count"] == 0
    assert seen["registry"] == _registry()


def test_license_only_addition_without_pr_base_stays_strict(monkeypatch, tmp_path):
    head = _registry()
    head["consumers"][0]["entries"][0]["license"] = "vendor/tool-LICENSE"
    report, _ = _run(monkeypatch, tmp_path, head)
    assert report["pending_enrollment_count"] == 0
    assert report["failure_count"] == 1


def test_modified_entry_stays_strict_with_base(monkeypatch, tmp_path):
    head = _registry(NEW)
    head["consumers"][0]["entries"][0]["destination"] = "vendor/renamed.py"
    report, _ = _run(monkeypatch, tmp_path, head, pr_validation=True, base_registry=_registry(NEW))
    assert report["failure_count"] == 1


def test_base_registry_requires_pr_validation(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        _run(monkeypatch, tmp_path, _registry(NEW), base_registry=_registry())


def test_workflow_passes_base_registry_only_for_pull_requests():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/vendor-parent-child-ci.yml").read_text()
    block = text.split('if [[ "$GITHUB_EVENT_NAME" == "pull_request" ]]; then', 1)[1].split("fi\n", 1)[0]
    assert "--base-registry" in block
    assert 'git show "$PR_BASE_SHA:vendor-consumers.json"' in block
    assert text.count("--base-registry") == 1


def test_pr_addition_already_in_recorded_lock_remains_audited(monkeypatch, tmp_path):
    report, seen = _run(monkeypatch, tmp_path, _registry(NEW), adopted=_registry(NEW),
                        pr_validation=True, base_registry=_registry())
    assert report["pending_enrollment_count"] == 0
    assert report["failure_count"] == 0
    assert seen["registry"] == _registry(NEW)


def test_partial_adoption_only_defers_absent_entries(monkeypatch, tmp_path):
    other = {"repository": "owner/other", "source": "y.py", "destination": "vendor/y.py"}
    report, seen = _run(monkeypatch, tmp_path, _registry(NEW, other), adopted=_registry(NEW),
                        pr_validation=True, base_registry=_registry())
    assert report["pending_enrollment_count"] == 1
    assert report["failure_count"] == 0
    assert seen["registry"] == _registry(NEW)


def test_failed_recorded_lock_read_does_not_defer_validation(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("child repository id mismatch")
    monkeypatch.setattr(sync, "_load_recorded_child_lock", fail)
    pending = sync.pending_enrollments(_registry(NEW), _registry())
    assert sync._unadopted_pending_enrollments(_registry(NEW), {}, pending, [CHILD]) == []


def test_unmonitored_pending_consumer_is_not_fetched_or_reported(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("unmonitored repository fetched")
    monkeypatch.setattr(sync, "_load_recorded_child_lock", fail)
    pending = sync.pending_enrollments(_registry(NEW), _registry())
    assert sync._unadopted_pending_enrollments(_registry(NEW), {}, pending, []) == []


def test_present_but_changed_source_is_not_mistaken_for_adoption(monkeypatch, tmp_path):
    wrong = {**NEW, "source": "wrong.py"}
    report, _ = _run(monkeypatch, tmp_path, _registry(NEW), adopted=_registry(wrong),
                     pr_validation=True, base_registry=_registry())
    assert report["failure_count"] == 1


# ═══ · vendor snapshot cache ═════════════════════════════════════════════════════════════════════════════
# Count actual snapshot requests while retaining immutable revision boundaries.

SHA = 'a' * 40


def test_overlapping_subsets_share_blobs_but_not_across_audits(monkeypatch):
    calls = []
    def read(repo, ref, paths, *, remote=None):
        calls.append((repo, ref, tuple(paths), remote))
        return ref, {p: ('b' * 40, p.encode()) for p in paths}
    monkeypatch.setattr(sync, '_read_public_git_snapshot', read)
    @sync._parent_fetch_scope
    def audit():
        _, first = sync._public_git_snapshot('owner/repo', SHA, ['a', 'LICENSE'])
        first.clear()  # Caller changes cannot alter the cache.
        _, second = sync._public_git_snapshot('owner/repo', SHA, ['a', 'b'])
        assert second['a'][1] == b'a'
        assert sync._public_git_snapshot('owner/repo', SHA, ['LICENSE'])[1]['LICENSE'][1] == b'LICENSE'
    audit()
    audit()
    assert [c[2] for c in calls] == [('LICENSE', 'a'), ('b',), ('LICENSE', 'a'), ('b',)]
    assert sync._PARENT_FETCH_CACHE.get() is None


@pytest.mark.parametrize('failure', ['exception', 'revision', 'missing'])
def test_incomplete_snapshot_is_not_cached(monkeypatch, failure):
    calls = []
    def read(repo, ref, paths, *, remote=None):
        calls.append(1)
        if len(calls) == 1:
            if failure == 'exception':
                raise ValueError('fetch failed')
            if failure == 'revision':
                return 'c' * 40, {'a': ('b' * 40, b'wrong')}
            return ref, {}
        return ref, {'a': ('b' * 40, b'right')}
    monkeypatch.setattr(sync, '_read_public_git_snapshot', read)
    @sync._parent_fetch_scope
    def audit():
        with pytest.raises((ValueError, KeyError)):
            sync._public_git_snapshot('owner/repo', SHA, ['a'])
        assert sync._public_git_snapshot('owner/repo', SHA, ['a'])[1]['a'][1] == b'right'
    audit()
    assert len(calls) == 2


def test_cache_key_separates_repository_revision_and_remote(monkeypatch):
    calls = []
    def read(repo, ref, paths, *, remote=None):
        calls.append((repo, ref, remote))
        return ref, {'a': ('b' * 40, str(calls[-1]).encode())}
    monkeypatch.setattr(sync, '_read_public_git_snapshot', read)
    @sync._parent_fetch_scope
    def audit():
        for repo, ref, remote in [('o/a', SHA, None), ('o/b', SHA, None),
                                  ('o/a', 'c' * 40, None), ('o/a', SHA, '/local')]:
            sync._public_git_snapshot(repo, ref, ['a'], remote=remote)
    audit()
    assert len(calls) == 4


def test_moving_refs_and_unscoped_calls_are_never_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, '_read_public_git_snapshot',
                        lambda *a, **kw: calls.append(a) or (SHA, {}))
    for _ in range(2):
        sync._public_git_snapshot('o/r', SHA, [])
    @sync._parent_fetch_scope
    def audit():
        for _ in range(2):
            sync._public_git_snapshot('o/r', 'main', [])
    audit()
    assert len(calls) == 4


def test_real_git_batch_tree_preserves_literal_paths_modes_sizes_and_bytes(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / 'source'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    paths = ['a.py', 'nested/日本語.py', 'literal[1].py', 'with space.py']
    for name in paths:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('data: ' + name + '\n').encode())
    git('add', '.')
    git('update-index', '--chmod=+x', 'a.py')
    git('commit', '-m', 'fixture')
    commit = git('rev-parse', 'HEAD')
    calls = []
    original = subprocess.run
    def run(argv, **kwargs):
        calls.append(argv)
        return original(argv, **kwargs)
    monkeypatch.setattr(sync.subprocess, 'run', run)
    resolved, files = sync._read_public_git_snapshot('public/fixture', commit, paths, remote=repo.as_uri())
    assert resolved == commit and set(files) == set(paths)
    for name in paths:
        data = (repo / name).read_bytes()
        assert files[name] == (sync.git_blob(data), data)
    assert sum('ls-tree' in c for c in calls) == 1
    assert sum('cat-file' in c for c in calls) == len(paths)
    assert not any('cat-file' in c and '-s' in c for c in calls)
    assert next(c for c in calls if 'ls-tree' in c).count('--literal-pathspecs') == 1


@pytest.mark.parametrize('oversized', [False, True])
def test_real_promisor_snapshot_sizes_missing_blobs(tmp_path, monkeypatch, oversized):
    import subprocess
    repo = tmp_path / 'source'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'uploadpack.allowFilter', 'true')
    (repo / 'a.py').write_bytes(b'content\n')
    (repo / 'LICENSE').write_bytes(b'license\n')
    git('add', '.')
    git('commit', '-m', 'fixture')
    commit = git('rev-parse', 'HEAD')
    missing_before, content_reads = [], []
    original = subprocess.run
    def run(argv, **kwargs):
        if 'cat-file' in argv and 'blob' in argv:
            content_reads.append(argv)
        if 'ls-tree' in argv:
            prefix = argv[:argv.index('--literal-pathspecs')]
            result = original(prefix + ['rev-list', '--objects', '--missing=print', 'FETCH_HEAD'],
                              env=kwargs['env'], capture_output=True, timeout=10)
            assert result.returncode == 0
            missing_before.extend(line for line in result.stdout.splitlines() if line.startswith(b'?'))
        return original(argv, **kwargs)
    monkeypatch.setattr(sync.subprocess, 'run', run)
    if oversized:
        monkeypatch.setattr(sync, 'MAX_BYTES', 3)
        with pytest.raises(ValueError, match='byte limit'):
            sync._read_public_git_snapshot('public/fixture', commit, ['a.py', 'LICENSE'], remote=repo.as_uri())
    else:
        resolved, files = sync._read_public_git_snapshot('public/fixture', commit,
                                                        ['a.py', 'LICENSE'], remote=repo.as_uri())
        assert resolved == commit
        assert files['a.py'] == (sync.git_blob(b'content\n'), b'content\n')
        assert files['LICENSE'] == (sync.git_blob(b'license\n'), b'license\n')
    assert len(content_reads) == (0 if oversized else 2)
    assert len(missing_before) == 2  # Proves the server actually honored blob:none.


# ═══ · vendor sync ghi wrappers ══════════════════════════════════════════════════════════════════════════
# vendor_sync identity wrappers delegate to the vendored gh_identity.

def test_vendored_gh_identity_loads_under_python_S():
    code = ("import sys; sys.path.insert(0, %r); import vendor_sync as s; g = s._gh_identity(); "
            "print(all(hasattr(g, n) for n in ('list_owner_repositories', 'commit_pulls', 'request_stub', 'operation')))"
            % str(ROOT))
    out = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True"


def test_wrapper_is_read_only():
    g = sync._gh_identity()
    with pytest.raises(ValueError, match="read-only"):
        sync._ghi_call(g.request, "POST", "repos/x/y", fetch=lambda url: pytest.fail("network"))


def test_lookup_child_main_pull_number_uses_commit_pulls(monkeypatch):
    seen = []
    def fake_json(url, token=None):
        seen.append(url)
        return [{"number": 12, "merged_at": "t", "base": {"ref": "main"}, "head": {"sha": "a" * 40}}]
    monkeypatch.setattr(sync, "_github_json", fake_json)
    assert sync._lookup_child_main_pull_number("owner/tool", "a" * 40, "token") == 12
    assert seen and "/repos/owner/tool/commits/" + "a" * 40 + "/pulls" in seen[0]


def test_lookup_child_main_pull_number_http_error_is_none(monkeypatch):
    def fake_json(url, token=None):
        raise HTTPError(url, 404, "nf", None, None)
    monkeypatch.setattr(sync, "_github_json", fake_json)
    assert sync._lookup_child_main_pull_number("owner/tool", "a" * 40, "token") is None


def test_list_account_repositories_keeps_forks_and_rejects_truncation(monkeypatch):
    rows = [{"full_name": "acct/fork", "visibility": "public", "private": False, "archived": False, "fork": True},
            {"full_name": "other/x", "visibility": "public", "private": False, "archived": False}]
    monkeypatch.setattr(sync, "_github_api", lambda m, url, token=None, body=None: rows)
    assert sync.list_org_repositories("acct", token="token") == ["acct/fork"]
    full = [{"full_name": "acct/r%d" % i, "visibility": "public", "private": False, "archived": False}
            for i in range(100)]
    monkeypatch.setattr(sync, "_github_api", lambda m, url, token=None, body=None: full)
    with pytest.raises(ValueError, match="truncated"):
        sync.list_org_repositories("acct", token="token")


def test_bare_fetch_first_parent_delegates_to_gh_identity(tmp_path, monkeypatch):
    up = tmp_path / "up"
    up.mkdir()
    def run(*a):
        return subprocess.run(["git", "-C", str(up), "-c", "user.email=t@e", "-c", "user.name=t", *a],
                              check=True, capture_output=True, text=True).stdout.strip()
    run("init", "-q", "-b", "main"); (up / "f").write_text("1"); run("add", "f"); run("commit", "-qm", "1")
    run("switch", "-q", "-c", "pr"); (up / "g").write_text("2"); run("add", "g"); run("commit", "-qm", "2")
    inter = run("rev-parse", "HEAD")
    run("switch", "-q", "main"); run("merge", "-q", "--no-ff", "-m", "m", "pr")
    merge = run("rev-parse", "HEAD")
    g = sync._gh_identity()
    seen = []
    real = g.first_parent_check
    def spy(*a, **k):
        seen.append(k)
        return real(*a, **k)
    monkeypatch.setattr(g, "first_parent_check", spy)
    remotes = {"owner/tool": str(up)}
    assert sync._commit_merged_via_git_bare_fetch("owner/tool", merge, remotes=remotes) is True
    with pytest.raises(ValueError, match="not merged"):
        sync._commit_merged_via_git_bare_fetch("owner/tool", inter, remotes=remotes)
    with pytest.raises(ValueError) as error:
        sync._commit_merged_via_git_bare_fetch("owner/tool", "1" * 40, remotes=remotes)
    assert str(error.value) == sync.PUBLIC_GIT_FETCH_ERROR
    assert seen and all(k["git_runner"] is sync._parent_git_runner and k["exact"] for k in seen)


# ═══ · mcp pswb observation transition ═══════════════════════════════════════════════════════════════════
# Frozen observations retain strict byte audits during an explicit contract replacement.

MCP_PSWB_OBSERVATION_TRANSITION_NAME = 'myon-bioinformatics/mcp-toolcall-lab'

def inputs_mcp_pswb_observation_transition():
    load = lambda p: json.loads((ROOT / p).read_text())
    heads, transitions = recorded_before_njs_adoption()   # the transition itself is retired; its mechanism still is tested
    return (load('vendor-consumers.json'), heads, transitions, load('tests/fixtures/mcp-pswb-observation-old.lock.json'))

def resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock, selected=None):
    with patch.object(sync, '_load_recorded_child_lock', return_value=lock):
        return sync._registry_for_recorded_transitions(registry, heads, manifest,
                                                       [MCP_PSWB_OBSERVATION_TRANSITION_NAME] if selected is None else selected)

def test_exact_old_observation_uses_old_contract_without_mutating_target_mcp_pswb_observation_transition():
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition(); original = copy.deepcopy(registry)
    result, reports = resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock)
    assert registry == original
    assert consumers.compare(result, lock, MCP_PSWB_OBSERVATION_TRANSITION_NAME)['matches']
    assert not consumers.compare(registry, lock, MCP_PSWB_OBSERVATION_TRANSITION_NAME)['matches']
    assert reports[0]['recorded_sha'] == heads['heads'][MCP_PSWB_OBSERVATION_TRANSITION_NAME]['sha']

@pytest.mark.parametrize('change', ['digest','source','missing','mixed'])
def test_changed_old_lock_fails_closed_mcp_pswb_observation_transition(change):
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition()
    if change == 'digest': lock['files'][0]['sha256'] = '0' * 64
    if change == 'source': lock['files'][0]['source'] = 'other.py'
    if change == 'missing': lock['files'].pop()
    if change == 'mixed': lock['files'].append(copy.deepcopy(lock['files'][0]))
    with pytest.raises(ValueError, match='lock changed'): resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock)

def test_unknown_observation_cannot_use_old_contract_mcp_pswb_observation_transition():
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition(); heads['heads'][MCP_PSWB_OBSERVATION_TRANSITION_NAME]['sha'] = '1' * 40
    result, reports = resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock)
    assert not reports and not consumers.compare(result, lock, MCP_PSWB_OBSERVATION_TRANSITION_NAME)['matches']

def test_identity_change_is_rejected_mcp_pswb_observation_transition():
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition(); heads['heads'][MCP_PSWB_OBSERVATION_TRANSITION_NAME]['repository_id'] += 1
    with pytest.raises(ValueError, match='repository id'): resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock)

def test_changed_desired_contract_is_rejected_mcp_pswb_observation_transition():
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition()
    next(c for c in registry['consumers'] if c['repository'] == MCP_PSWB_OBSERVATION_TRANSITION_NAME)['entries'][0]['source'] = 'other.py'
    with pytest.raises(ValueError, match='target contract'): resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock)

def test_unmonitored_repo_is_never_read_mcp_pswb_observation_transition():
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition()
    with patch.object(sync, '_load_recorded_child_lock', side_effect=AssertionError('unexpected fetch')):
        _, reports = sync._registry_for_recorded_transitions(registry, heads, manifest, [])
    assert not reports

def test_failed_read_is_not_converted_to_permission_mcp_pswb_observation_transition():
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition()
    with patch.object(sync, '_load_recorded_child_lock', side_effect=ValueError('unavailable')):
        with pytest.raises(ValueError, match='unavailable'):
            sync._registry_for_recorded_transitions(registry, heads, manifest, [MCP_PSWB_OBSERVATION_TRANSITION_NAME])

@pytest.mark.parametrize('bad', ['source', 'child', 'clean', 'unmerged'])
def test_selected_old_contract_still_rejects_tampered_bytes_mcp_pswb_observation_transition(bad):
    registry, heads, manifest, lock = inputs_mcp_pswb_observation_transition()
    registry, _ = resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock)
    # Use one exact entry to exercise the unchanged source and placed-byte audit.
    item = copy.deepcopy(lock['files'][0]); data = b'reviewed source bytes\n'
    item['sha256'] = hashlib.sha256(data).hexdigest(); item['blob_sha'] = sync.git_blob(data)
    lock = {'schema': 'vendor-lock/1', 'files': [item]}
    consumer = consumers.get_consumer(registry, MCP_PSWB_OBSERVATION_TRANSITION_NAME)
    consumer['entries'] = [{k:item[k] for k in ('repository','source','destination')}]
    registry = {'schema':'vendor-consumers/1', 'consumers':[consumer]}
    def snapshot(repo, revision, paths):
        source = repo == item['repository']; blobdata = data
        if (source and bad == 'source') or (not source and bad == 'child'): blobdata += b'tamper'
        return revision, {p:(sync.git_blob(blobdata), blobdata) for p in paths}
    with patch.object(sync,'_load_recorded_child_lock',return_value=lock), \
         patch.object(sync,'_ensure_public_source_repository'), \
         patch.object(sync,'commit_merged_into_default', side_effect=ValueError('source not merged') if bad == 'unmerged' else None), \
         patch.object(sync,'_public_git_snapshot',side_effect=snapshot):
        report = sync.audit_adopted_child_files(registry, heads, [MCP_PSWB_OBSERVATION_TRANSITION_NAME])
    assert report['failure_count'] == (0 if bad == 'clean' else 1)

def test_new_observation_uses_desired_contract_without_transition_mcp_pswb_observation_transition():
    registry, heads, manifest, _ = inputs_mcp_pswb_observation_transition()
    heads['heads'][MCP_PSWB_OBSERVATION_TRANSITION_NAME]['sha'] = '2' * 40
    # The PSWB-only stage remains historical; desired topology now includes njs_launch.
    historical = json.loads((ROOT/'tests/fixtures/mcp-pswb-observation-new.lock.json').read_text())
    assert not consumers.compare(registry, historical, MCP_PSWB_OBSERVATION_TRANSITION_NAME)['matches']
    lock = json.loads((ROOT/'tests/fixtures/njs-mcp-toolcall-lab-new.lock.json').read_text())
    result, reports = resolve_mcp_pswb_observation_transition(registry, heads, manifest, lock)
    assert not reports and consumers.compare(result, lock, MCP_PSWB_OBSERVATION_TRANSITION_NAME)['matches']


# ═══ · njs observation transitions ═══════════════════════════════════════════════════════════════════════
# Retired on 2026-11-14 once the merged children were re-observed: these keep testing the exact old observations.

REPOS=['mcp-toolcall-lab','PySaiyanWeBoo','markdown','xprobe']
def load(p): return json.loads((ROOT/p).read_text())
@pytest.mark.parametrize('repo',REPOS)
def test_exact_old_and_desired_new_contracts(repo):
 name='myon-bioinformatics/'+repo
 r=load('vendor-consumers.json'); h,t=recorded_before_njs_adoption()
 old=load('tests/fixtures/njs-'+repo+'-old.lock.json');new=load('tests/fixtures/njs-'+repo+'-new.lock.json');before=copy.deepcopy(r)
 assert c.compare(r,new,name)['matches']
 assert not c.compare(r,old,name)['matches']
 with patch.object(s,'_load_recorded_child_lock',return_value=old):
  restored,reports=s._registry_for_recorded_transitions(r,h,t,[name])
 assert r==before and len(reports)==1 and c.compare(restored,old,name)['matches']
 changed=copy.deepcopy(old); changed['files'][0]['sha256']='0'*64
 with patch.object(s,'_load_recorded_child_lock',return_value=changed),pytest.raises(ValueError,match='lock changed'):
  s._registry_for_recorded_transitions(r,h,t,[name])
 h['heads'][name]['sha']='1'*40
 with patch.object(s,'_load_recorded_child_lock',side_effect=AssertionError('new observation must not use transition')):
  actual,reports=s._registry_for_recorded_transitions(r,h,t,[name])
 assert not reports and c.compare(actual,new,name)['matches'] and not c.compare(actual,old,name)['matches']

def test_pswb_cleanup_predates_launcher_migration():
    base=load('tests/fixtures/njs-PySaiyanWeBoo-adopted-base.lock.json')
    new=load('tests/fixtures/njs-PySaiyanWeBoo-new.lock.json')
    old=load('tests/fixtures/njs-PySaiyanWeBoo-old.lock.json')
    assert {e['destination'] for e in base['files']} == {'tools/vendor/LICENSE','vendor/node_launch.py','vendor/py_launch.cjs'}
    license=next(e for e in base['files'] if e['source']=='LICENSE')
    assert license['commit']=='73157cb7fed236a4a941722a6dcddd69a33ab95a'
    assert license['blob_sha']=='4ec4989b801bf1a3df6184d21f79e6e7ed5931f6'
    assert license==next(e for e in new['files'] if e['source']=='LICENSE')
    assert next(e for e in old['files'] if e['destination']=='tool/vendor/LICENSE')['blob_sha']==license['blob_sha']
    assert [e for e in base['files'] if e['source']!='node_launch.py']==[e for e in new['files'] if e['source']!='njs_launch.py']

def test_mcp_prior_pswb_transition_keeps_same_frozen_lock():
    row=next(t for t in NJS_ADOPTION['recorded']['transitions']['transitions'] if t['consumer'].endswith('/mcp-toolcall-lab'))
    assert row['recorded_sha']=='cd04472040f0db991c5138144c01f56a2b79e44b'
    assert row['lock_sha256']=='40496c0e700ff3c4894541bc87dca28c6755b0ed0065cb5a740ec0c0e1583135'
    old=load('tests/fixtures/njs-mcp-toolcall-lab-old.lock.json')
    assert s._transition_digest(old)==row['lock_sha256']
    assert {'vendor/web_ui.py','vendor/node_launch.py'} <= {e['destination'] for e in old['files']}


# ═══ · pswb consumer migration ═══════════════════════════════════════════════════════════════════════════
# Canonical PSWB consumers keep old observations strict and new locks exact.

load_pswb_consumer_migration = lambda path: json.loads((ROOT / path).read_text())
PSWB_CONSUMER_MIGRATION_NEW = load_pswb_consumer_migration('tests/fixtures/pswb_consumer_migration_20261113.json')
OLD = load_pswb_consumer_migration('tests/fixtures/pswb_consumer_old_locks_20261113.json')


@pytest.mark.parametrize('name', PSWB_CONSUMER_MIGRATION_NEW)
def test_merged_lock_matches_its_historical_topology(name):
    snapshot = PSWB_CONSUMER_MIGRATION_NEW[name]
    assert sync._transition_digest(snapshot['lock']) == snapshot['lock_sha256']
    registry = load_pswb_consumer_migration('vendor-consumers.json')
    if name == 'myon-bioinformatics/mcp-toolcall-lab':
        # This frozen snapshot records the PSWB migration before the separate
        # launcher rename. Preserve its evidence, not a claim of current adoption.
        assert not consumers.compare(registry, snapshot['lock'], name)['matches']
        target = load_pswb_consumer_migration('tests/fixtures/njs-mcp-toolcall-lab-new.lock.json')
        assert consumers.compare(registry, target, name)['matches']
        entry = next(row for row in registry['consumers'] if row['repository'] == name)
        launcher = next(row for row in entry['entries'] if row['source'] == 'njs_launch.py')
        assert launcher['destination'] == 'vendor/njs_launch.py'
        launcher['source'] = 'node_launch.py'
        launcher['destination'] = 'vendor/node_launch.py'
    assert consumers.compare(registry, snapshot['lock'], name)['matches']
    assert all(row['ref'] == row['commit'] for row in snapshot['lock']['files'])
    assert not any(row['source'] == 'web_ui.py' for row in snapshot['lock']['files'])
    changed = copy.deepcopy(snapshot['lock'])
    changed['files'].pop()
    assert not consumers.compare(registry, changed, name)['matches']


@pytest.mark.parametrize('name', OLD)
def test_only_exact_historical_observation_uses_old_contract(name):
    registry = load_pswb_consumer_migration('vendor-consumers.json')
    heads = load_pswb_consumer_migration('child-heads.json')
    transitions = load_pswb_consumer_migration('vendor-observation-transitions.json')
    with patch.object(sync, '_load_recorded_child_lock', return_value=OLD[name]):
        old_registry, reports = sync._registry_for_recorded_transitions(registry, heads, transitions, [name])
    assert len(reports) == 1
    assert consumers.compare(old_registry, OLD[name], name)['matches']
    assert not consumers.compare(registry, OLD[name], name)['matches']
    tampered = copy.deepcopy(OLD[name])
    tampered['files'][0]['sha256'] = '0' * 64
    with patch.object(sync, '_load_recorded_child_lock', return_value=tampered):
        with pytest.raises(ValueError, match='lock changed'):
            sync._registry_for_recorded_transitions(registry, heads, transitions, [name])
    heads['heads'][name]['sha'] = PSWB_CONSUMER_MIGRATION_NEW[name]['sha']
    with patch.object(sync, '_load_recorded_child_lock', side_effect=AssertionError('old lock read')):
        new_registry, reports = sync._registry_for_recorded_transitions(registry, heads, transitions, [name])
    assert reports == []
    assert consumers.compare(new_registry, PSWB_CONSUMER_MIGRATION_NEW[name]['lock'], name)['matches']


# ═══ · repository rename bridge ══════════════════════════════════════════════════════════════════════════
# Retired aliases fail closed; current source provenance checks stay strict.

PAIRS = [
    ('myon-bioinformatics/web-ui', ('myon-bioinformatics/PySaiyanWeBoo', 1382466695)),
    ('myon-bioinformatics/browser-test-kit', ('myon-bioinformatics/BrowserTestKit', 1384313653)),
]
DATA = b'approved source\n'


def documents(source):
    row = {'repository': source, 'source': 'tool.py', 'destination': 'vendor/tool.py'}
    registry = {'schema': consumers.SCHEMA, 'consumers': [{
        'repository': 'owner/app', 'state': 'locked', 'lock': 'vendor.lock.json',
        'entries': [dict(row)],
    }]}
    lock = {'schema': sync.SCHEMA, 'files': [dict(row, ref='1'*40, commit='1'*40,
        blob_sha=sync.git_blob(DATA), sha256=hashlib.sha256(DATA).hexdigest())]}
    return registry, lock


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('registry_new,lock_new', [(False,False),(False,True),(True,False),(True,True)])
def test_names_compare_exactly_without_network(monkeypatch, old, pair, registry_new, lock_new):
    new, _ = pair
    registry, _ = documents(new if registry_new else old)
    _, lock = documents(new if lock_new else old)
    before = copy.deepcopy((registry, lock))
    def forbidden(*args, **kwargs):
        pytest.fail('comparison must not resolve repository aliases')
    monkeypatch.setattr(sync, 'fetch_repository_record', forbidden)
    assert consumers.compare(registry, lock, 'owner/app')['matches'] == (registry_new == lock_new)
    assert (registry, lock) == before


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('wrong_id', [False, True])
def test_redirect_is_rejected_even_with_expected_id(monkeypatch, old, pair, wrong_id):
    canonical, rid = pair
    html = (f'<meta name="octolytics-dimension-repository_id" content="{rid + int(wrong_id)}">'
            f'<meta name="octolytics-dimension-repository_nwo" content="{canonical}">'
            '<meta name="octolytics-dimension-repository_public" content="true">').encode()
    monkeypatch.setattr(sync, '_public_http_get', lambda url: html)
    with pytest.raises(ValueError, match='name mismatch'):
        sync.public_repository_page_record(old)
    assert sync.public_repository_page_record(canonical)['full_name'] == canonical


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('use_new', [False, True])
def test_exact_self_vendoring_remains_rejected(old, pair, use_new):
    name = pair[0] if use_new else old
    registry, lock = documents(name)
    registry['consumers'][0]['repository'] = name
    with pytest.raises(ValueError, match='own canonical artifact'):
        consumers.compare(registry, lock, name)


def test_current_registration_has_no_retired_identities():
    """A retired name never identifies anything: not a consumer, a source, a head key or a catalog tool.

    The one place it may appear is ``optional.previous_names`` of a child head, which is history
    attached to the current name (the repository_id is the identity) so ``vendor_fleet_plan.py
    query who OLD-NAME`` can answer; nothing fetches or matches by it.
    """
    root = Path(__file__).resolve().parents[1]
    old = {pair[0].casefold() for pair in PAIRS}
    registry = json.loads((root / 'vendor-consumers.json').read_text())
    heads = json.loads((root / 'child-heads.json').read_text())
    catalog = json.loads((root / 'vendor-catalog.json').read_text())
    def check(value, history=False):
        if isinstance(value, dict):
            for k, v in value.items():
                assert k.casefold() not in old
                check(v, history=(k == 'previous_names'))
        elif isinstance(value, list):
            for v in value:
                check(v, history=history)
        elif isinstance(value, str) and not history:
            assert value.casefold() not in old
    for doc in (registry, heads, catalog):
        check(doc)
    recorded = {name.casefold() for entry in heads['heads'].values()
                for name in (entry.get('optional') or {}).get('previous_names', [])}
    assert recorded == old  # the two known renames are recorded as history, under their current names


@pytest.mark.parametrize('fault', [None,'unmerged','source_blob','source_bytes','child_bytes'])
def test_adopted_audit_retains_provenance_and_bytes(monkeypatch, fault):
    old, (new, _) = PAIRS[0]
    registry, _ = documents(new)
    _, lock = documents(new)
    monkeypatch.setattr(sync, '_load_recorded_child_lock', lambda *a, **k: copy.deepcopy(lock))
    # This fixture tests provenance/byte auditing with fake commits, not live repository metadata.
    monkeypatch.setattr(sync, '_ensure_public_source_repository', lambda *a, **k: None)
    seen = []
    def merged(repository, revision, **kwargs):
        seen.append((repository,revision))
        if fault == 'unmerged':
            raise ValueError(sync.PIN_NOT_MERGED_ERROR)
    monkeypatch.setattr(sync, 'commit_merged_into_default', merged)
    def snapshot(repository, revision, paths):
        child = repository == 'owner/app'
        data = b'tampered' if fault == ('child_bytes' if child else 'source_bytes') else DATA
        blob = '0'*40 if fault == 'source_blob' and not child else sync.git_blob(DATA)
        return revision, {path:(blob,data) for path in paths}
    monkeypatch.setattr(sync, '_public_git_snapshot', snapshot)
    report = sync.audit_adopted_child_files(registry, {'heads':{'owner/app':{'sha':'2'*40}}}, ['owner/app'])
    assert seen == [(new,'1'*40)]
    assert report['failure_count'] == (0 if fault is None else 1)
    assert lock['files'][0]['ref'] == lock['files'][0]['commit'] == '1'*40


# ═══ · recreate failure review ═══════════════════════════════════════════════════════════════════════════
# Failure-injection characterization of recreate; every filesystem is temporary.

def setup(root):
    path, value = lock(root, [entry(), entry(b'two\n', source='scripts/two.py', destination='vendor/two.py')])
    (root / 'vendor').mkdir()
    (root / 'vendor/adapter.py').write_bytes(b'old\n')
    (root / 'vendor/two.py').write_bytes(b'two\n')
    return path, value


def get(url):
    return b'two\n' if url.endswith('/two.py') else b'old\n'


def test_middle_replace_failure_is_partial_but_rerunnable(tmp_path, monkeypatch):
    path, _ = setup(tmp_path)
    first, second = tmp_path/'vendor/adapter.py', tmp_path/'vendor/two.py'
    before = first.stat().st_ino, second.stat().st_ino
    real = sync.os.replace
    def replace(src, dst):
        if Path(dst) == second:
            raise OSError('injected second replacement failure')
        return real(src, dst)
    with monkeypatch.context() as m:
        m.setattr(sync.os, 'replace', replace)
        assert sync.main(['recreate', '--root', str(tmp_path), '--manifest', path.name], get=get) == 2
    assert first.stat().st_ino != before[0] and second.stat().st_ino == before[1]
    assert first.read_bytes() == b'old\n' and second.read_bytes() == b'two\n'
    assert _staging_left(tmp_path) == []
    assert sync.recreate(path.name, tmp_path, get=get)['recreated'] == ['vendor/adapter.py', 'vendor/two.py']


def test_final_check_failure_does_not_rollback(tmp_path, monkeypatch):
    path, _ = setup(tmp_path)
    original = sync.synchronize
    root_checks = 0
    def synchronize(manifest, root, mode, **kwargs):
        nonlocal root_checks
        if Path(root) == tmp_path and mode == 'check':
            root_checks += 1
            if root_checks == 3:
                (tmp_path/'vendor/adapter.py').write_bytes(b'concurrent final edit\n')
        return original(manifest, root, mode, **kwargs)
    monkeypatch.setattr(sync, 'synchronize', synchronize)
    with pytest.raises(ValueError, match='digest mismatch'):
        sync.recreate(path.name, tmp_path, get=get)
    assert (tmp_path/'vendor/adapter.py').read_bytes() == b'concurrent final edit\n'
    assert _staging_left(tmp_path) == []


def test_edit_during_download_is_preserved(tmp_path):
    path, _ = setup(tmp_path)
    def download(url):
        (tmp_path/'vendor/adapter.py').write_bytes(b'user edit\n')
        return get(url)
    with pytest.raises(ValueError, match='digest mismatch'):
        sync.recreate(path.name, tmp_path, get=download)
    assert (tmp_path/'vendor/adapter.py').read_bytes() == b'user edit\n'


def test_manifest_edit_during_download_is_rejected(tmp_path):
    path, value = setup(tmp_path)
    def download(url):
        path.write_text(json.dumps(value, indent=2)+'\n')
        return get(url)
    with pytest.raises(ValueError, match='manifest changed'):
        sync.recreate(path.name, tmp_path, get=download)
    assert path.read_text() == json.dumps(value, indent=2)+'\n'


def test_edit_to_later_target_after_first_replace_is_preserved(tmp_path, monkeypatch):
    path, _ = setup(tmp_path)
    real = sync.os.replace
    def replace(src, dst):
        result = real(src, dst)
        if Path(dst) == tmp_path/'vendor/adapter.py':
            (tmp_path/'vendor/two.py').write_bytes(b'concurrent second edit\n')
        return result
    monkeypatch.setattr(sync.os, 'replace', replace)
    with pytest.raises(ValueError, match='digest mismatch'):
        sync.recreate(path.name, tmp_path, get=get)
    assert (tmp_path/'vendor/two.py').read_bytes() == b'concurrent second edit\n'
    assert _staging_left(tmp_path) == []


def test_write_inside_replace_window_remains_unsupported(tmp_path, monkeypatch):
    """No compare-and-swap guarantee: the last check cannot close this race."""
    path, _ = setup(tmp_path)
    real = sync.os.replace
    def replace(src, dst):
        if Path(dst) == tmp_path/'vendor/adapter.py':
            Path(dst).write_bytes(b'edit after last verification\n')
        return real(src, dst)
    monkeypatch.setattr(sync.os, 'replace', replace)
    sync.recreate(path.name, tmp_path, get=get)
    assert (tmp_path/'vendor/adapter.py').read_bytes() == b'old\n'


if __name__ == "__main__":
    unittest.main()

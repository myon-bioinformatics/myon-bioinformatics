"""📒 vendor-consumers.json against each child's reviewed lock: one section per consumer (what is registered, what is deliberately not proposed)."""

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


# ═══ ⌨️ cli_args ═════════════════════════════════════════════════════════════════════════════════════════
# Parent topology follows cli_args PR #28 vendor.lock.json snapshot.

CLI_ARGS = "myon-bioinformatics/cli_args"


def load(path):
    return json.loads((ROOT / path).read_text())


def test_cli_args_registry_matches_reviewed_pr28_lock():
    registry = load("vendor-consumers.json")
    lock = load("tests/fixtures/cli-args-pr28.lock.json")
    assert len(lock["files"]) == 2
    assert vendor_consumers.compare(registry, lock, CLI_ARGS)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, CLI_ARGS)["matches"]


def test_py_launch_is_registered_for_cli_args_not_auto_proposed():
    result = vendor_fleet_plan.plan(
        load("vendor-catalog.json"),
        load("vendor-consumers.json"),
        active_consumers=[CLI_ARGS],
    )
    py_launch = [x for x in result["rows"] if x["source"] == "py_launch.cjs"]
    assert len(py_launch) == 1
    assert py_launch[0]["action"] == "registered"
    assert py_launch[0]["destination"] == "vendor/py_launch.cjs"
    assert result["counts"]["registered"] == 1


# ═══ 🪡 Ironmate ══════════════════════════════════════════════════════════════════════════════════════════
# Parent topology follows Ironmate's locked flow snapshot: the flow code and its sample now come from ascii_artist.

IRONMATE = 'myon-bioinformatics/Ironmate'


def test_ironmate_registry_matches_reviewed_flow_lock():
    registry = load('vendor-consumers.json')
    lock = load('tests/fixtures/ironmate-flow-ascii-artist.lock.json')
    assert len(lock['files']) == 55
    assert not [x for x in lock['files'] if x['repository'].endswith('/PySaiyanWeBoo')]   # web-ui has no flow code now
    assert vendor_consumers.compare(registry, lock, IRONMATE)['matches']
    # All adopted source digests remain owned by the child's immutable lock.
    assert all(x['ref'] == x['commit'] for x in lock['files'])
    changed = copy.deepcopy(lock)
    changed['files'].pop()
    assert not vendor_consumers.compare(registry, changed, IRONMATE)['matches']


def test_retired_sources_are_not_proposed_for_reenrollment():
    result = vendor_fleet_plan.plan(load('vendor-catalog.json'), load('vendor-consumers.json'),
                                   active_consumers=[IRONMATE])
    for source in ('git_inspector.py', 'vendor_sync.py'):
        rows = [x for x in result['rows'] if x['tool_repository'] ==
                'myon-bioinformatics/myon-bioinformatics' and x['source'] == source]
        assert len(rows) == 1 and rows[0]['action'] == 'skipped'
    assert result['counts']['registered'] == 29
    assert result['counts']['skipped'] == 20
    py_launch = [x for x in result['rows'] if x['source'] == 'py_launch.cjs']
    assert len(py_launch) == 1
    assert py_launch[0]['action'] == 'registered'
    assert py_launch[0]['destination'] == 'vendor/py_launch.cjs'


def test_old_flow_export_lock_is_not_silently_accepted_after_patch_enrollment():
    old = load('tests/fixtures/ironmate-flow-b5ae001.lock.json')
    result = vendor_consumers.compare(load('vendor-consumers.json'), old, IRONMATE)
    assert not result['matches']
    assert len(old['files']) == 54


def test_web_ui_flow_workspace_lock_is_not_accepted_after_the_move_to_ascii_artist():
    old = load('tests/fixtures/ironmate-flow-workspace.lock.json')
    result = vendor_consumers.compare(load('vendor-consumers.json'), old, IRONMATE)
    assert not result['matches'] and len(old['files']) == 61
    assert {x['repository'] for x in result['unexpected']} == {'myon-bioinformatics/PySaiyanWeBoo'}
    assert [(x['repository'], x['source']) for x in result['missing']] == [('myon-bioinformatics/ascii_artist', 'examples/pr-flow.json')]


NJS_RECORDED = load('tests/fixtures/njs_launch_adoption_20261114.json')['recorded']   # before the 2026-11-14 re-observation


def recorded_heads():
    heads = load('child-heads.json')
    heads['heads'].update(copy.deepcopy(NJS_RECORDED['heads']))
    return heads


def recorded_registry(name):
    registry = copy.deepcopy(load('vendor-consumers.json'))
    transition = next(t for t in NJS_RECORDED['transitions']['transitions'] if t['consumer'] == name)
    next(c for c in registry['consumers'] if c['repository'] == name)['entries'] = copy.deepcopy(transition['old_entries'])
    return registry


# ═══ 📝 markdown ══════════════════════════════════════════════════════════════════════════════════════════
# Parent topology follows markdown PR #125 vendor.lock.json snapshot.

MARKDOWN = "myon-bioinformatics/markdown"


def test_markdown_registry_matches_reviewed_pr125_lock():
    registry = recorded_registry(MARKDOWN)
    lock = load("tests/fixtures/markdown-pr125.lock.json")
    assert len(lock["files"]) == 7
    assert vendor_consumers.compare(registry, lock, MARKDOWN)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, MARKDOWN)["matches"]


def test_njs_launch_is_registered_for_markdown_not_auto_proposed():
    result = vendor_fleet_plan.plan(
        load("vendor-catalog.json"),
        load("vendor-consumers.json"),
        active_consumers=[MARKDOWN],
    )
    njs_launch = [x for x in result["rows"] if x["source"] == "njs_launch.py"]
    assert len(njs_launch) == 1
    assert njs_launch[0]["action"] == "registered"
    assert njs_launch[0]["destination"] == "vendor/njs_launch.py"
    assert result["counts"]["registered"] == 3
    assert result["counts"]["skipped"] == 37


# ═══ 🧪 mcp-toolcall-lab ══════════════════════════════════════════════════════════════════════════════════
# Parent topology includes the reviewed mcp-toolcall-lab lock plus web_ui enrollment.

MCP_TOOLCALL_LAB = "myon-bioinformatics/mcp-toolcall-lab"


def reviewed_pr159_registry():
    """Reconstruct PR #159 topology; web UI and planner were adopted later."""
    registry = recorded_registry(MCP_TOOLCALL_LAB)
    for row in registry["consumers"]:
        if row["repository"] == MCP_TOOLCALL_LAB:
            row["entries"] = [e for e in row["entries"] if e["destination"] not in {"vendor/web_ui.py", "vendor/vendor_fleet_plan.py"}]
    return registry


def test_mcp_toolcall_lab_registry_matches_reviewed_pr159_lock():
    registry = reviewed_pr159_registry()
    lock = load("tests/fixtures/mcp-toolcall-lab-pr159.lock.json")
    assert len(lock["files"]) == 9
    assert vendor_consumers.compare(registry, lock, MCP_TOOLCALL_LAB)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, MCP_TOOLCALL_LAB)["matches"]


def test_njs_launch_is_registered_for_mcp_toolcall_lab_not_auto_proposed():
    result = vendor_fleet_plan.plan(
        load("vendor-catalog.json"),
        load("vendor-consumers.json"),
        active_consumers=[MCP_TOOLCALL_LAB],
    )
    njs_launch = [x for x in result["rows"] if x["source"] == "njs_launch.py"]
    assert len(njs_launch) == 1
    assert njs_launch[0]["action"] == "registered"
    assert njs_launch[0]["destination"] == "vendor/njs_launch.py"
    web_ui = [x for x in result["rows"] if x["source"] == "web_ui.py"]
    assert len(web_ui) == 1
    assert web_ui[0]["action"] == "needs_registration"
    # The old catalog recommendation is not the desired canonical-module contract.
    assert web_ui[0]["destination"] is None
    assert result["counts"]["registered"] == 6
    assert result["counts"]["skipped"] == 35


def test_current_planner_registration_matches_immutable_child_lock():
    import hashlib
    import vendor_sync

    snapshot = load("tests/fixtures/reviewed_mcp_planner_lock_20261109.json")[MCP_TOOLCALL_LAB]
    old = load("tests/fixtures/reviewed_mcp_lock_20261109.json")[MCP_TOOLCALL_LAB]
    assert old["sha"] == "31ff99fbe4c13b3feb570ae3ba1fc7cf972f8a16"
    assert snapshot["sha"] == "cd04472040f0db991c5138144c01f56a2b79e44b"
    heads = recorded_heads()["heads"][MCP_TOOLCALL_LAB]
    assert (heads["sha"], heads["repository_id"]) == (snapshot["sha"], snapshot["repository_id"])
    lock = vendor_sync.validate(snapshot["lock"])
    assert hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == snapshot["lock_sha256"]
    registry = load("vendor-consumers.json")
    assert not vendor_consumers._compare_exact(registry, lock, MCP_TOOLCALL_LAB)["matches"]
    from unittest.mock import patch
    with patch.object(vendor_sync, '_load_recorded_child_lock', return_value=lock):
        registry, reports = vendor_sync._registry_for_recorded_transitions(
            registry, recorded_heads(), NJS_RECORDED['transitions'], [MCP_TOOLCALL_LAB])
    assert len(reports) == 1 and reports[0]['recorded_sha'] == snapshot['sha']
    assert vendor_consumers._compare_exact(registry, lock, MCP_TOOLCALL_LAB)["matches"]
    planner = [r for r in lock["files"] if r["destination"] == "vendor/vendor_fleet_plan.py"]
    assert len(planner) == 1
    assert planner[0]["source"] == "vendor_fleet_plan.py"
    assert planner[0]["repository"] == "myon-bioinformatics/myon-bioinformatics"
    assert all(r["ref"] == r["commit"] for r in lock["files"])
    result = vendor_fleet_plan.plan(load("vendor-catalog.json"), registry, active_consumers=[MCP_TOOLCALL_LAB])
    registered = {(r["tool_repository"], r["source"], r["destination"]) for r in result["rows"] if r["action"] == "registered"}
    locked = {(r["repository"], r["source"], r["destination"]) for r in lock["files"]}
    # 6 of the 7 frozen entries: vendor/node_launch.py's source left the catalog on 2026-11-14.
    assert len(registered) == result["counts"]["registered"] == 6
    assert registered <= locked
    for destination in ("vendor/vendor_fleet_plan.py", "vendor/web_ui.py"):
        changed = copy.deepcopy(lock)
        changed["files"] = [r for r in changed["files"] if r["destination"] != destination]
        assert not vendor_consumers._compare_exact(registry, changed, MCP_TOOLCALL_LAB)["matches"]


# ═══ 🐉 PySaiyanWeBoo (web-ui) ════════════════════════════════════════════════════════════════════════════
# Parent topology follows web-ui PR #90 vendor.lock.json snapshot.

WEB_UI = "myon-bioinformatics/web-ui"


def load_web_ui(path):
    # These regressions exercise the historical PR's reviewed contract.
    # Current fleet acceptance is covered by test_vendor_contracts.py.
    historical = json.loads((ROOT / "tests/fixtures/vendor_baseline_before_20261029.json").read_text())
    requested = path
    if requested == "vendor-consumers.json":
        return historical["registry"]
    if requested == "child-heads.json":
        return historical["heads"]
    if requested == "vendor-catalog.json":
        return historical["catalog"]
    return json.loads((ROOT / path).read_text())


def test_web_ui_registry_matches_reviewed_pr90_lock():
    registry = load_web_ui("vendor-consumers.json")
    lock = load_web_ui("tests/fixtures/web-ui-pr90.lock.json")
    assert len(lock["files"]) == 4
    assert vendor_consumers.compare(registry, lock, WEB_UI)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, WEB_UI)["matches"]


def test_node_launch_is_registered_for_web_ui_not_auto_proposed():
    result = vendor_fleet_plan.plan(
        load_web_ui("vendor-catalog.json"),
        load_web_ui("vendor-consumers.json"),
        active_consumers=[WEB_UI],
    )
    node_launch = [x for x in result["rows"] if x["source"] == "node_launch.py"]
    assert len(node_launch) == 1
    assert node_launch[0]["action"] == "registered"
    assert node_launch[0]["destination"] == "vendor/node_launch.py"
    assert result["counts"]["registered"] == 2
    assert result["counts"]["skipped"] == 36


# ═══ 🔬 xprobe ════════════════════════════════════════════════════════════════════════════════════════════
# Parent topology follows xprobe PR #33 vendor.lock.json snapshot.

XPROBE = "myon-bioinformatics/xprobe"


def load_xprobe(path):
    # These regressions exercise the historical PR's reviewed contract.
    # Current fleet acceptance is covered by test_vendor_contracts.py.
    historical = json.loads((ROOT / "tests/fixtures/vendor_baseline_before_20261029.json").read_text())
    requested = path
    if requested == "vendor-consumers.json":
        return historical["registry"]
    if requested == "child-heads.json":
        return historical["heads"]
    return json.loads((ROOT / path).read_text())


def test_xprobe_registry_matches_reviewed_pr33_lock():
    registry = load_xprobe("vendor-consumers.json")
    lock = load_xprobe("tests/fixtures/xprobe-pr33.lock.json")
    assert len(lock["files"]) == 2
    assert vendor_consumers.compare(registry, lock, XPROBE)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, XPROBE)["matches"]


def test_njs_launch_is_registered_for_xprobe_not_auto_proposed():
    # The current contract: node_launch.py left the catalog once xprobe adopted njs_launch.py (#56).
    result = vendor_fleet_plan.plan(load("vendor-catalog.json"), load("vendor-consumers.json"), active_consumers=[XPROBE])
    njs_launch = [x for x in result["rows"] if x["source"] == "njs_launch.py"]
    assert len(njs_launch) == 1
    assert njs_launch[0]["action"] == "registered"
    assert njs_launch[0]["destination"] == "vendor/njs_launch.py"
    assert not [x for x in result["rows"] if x["source"] == "node_launch.py"]


# ═══ 🦋 flutter_navigation_basic ══════════════════════════════════════════════════════════════════════════
# Regression for the parent-approved Flutter vendor and workflow baseline.
#
# Lock snapshot comes from Flutter #207 bounded-candidate repair, adopting browser-test-kit #85
# main merge 8d8512fe. This historical lock still checks catalog behavior.
# The approved Non-Dart workflow at Flutter #210 adds the existing Ironmate handoff
# portable case; its immutable head also records the fetch-guard vendor rollout.

FLUTTER = "myon-bioinformatics/flutter_navigation_basic"
LOCK_FIXTURE = "tests/fixtures/flutter_vendor_lock_207_bounded.json"
WORKFLOW_FIXTURE = "tests/fixtures/flutter_non_dart_build_companions.yml"
GH_OPS_CATALOG_COMMIT = "09831b992dc91d83b15c44861a788e03b1e654c4"


def load_flutter(filename):
    # These regressions exercise the historical PR's reviewed contract.
    # Current fleet acceptance is covered by test_vendor_contracts.py.
    historical = json.loads((ROOT / "tests/fixtures/vendor_baseline_before_20261029.json").read_text())
    requested = filename
    if requested == "vendor-consumers.json":
        return historical["registry"]
    if requested == "child-heads.json":
        return historical["heads"]
    if requested == "vendor-catalog.json":
        return historical["catalog"]
    return json.loads((ROOT / filename).read_text(encoding="utf-8"))


def test_flutter_registry_matches_pinned_post_migration_lock():
    registry = load_flutter("vendor-consumers.json")
    lock = load_flutter(LOCK_FIXTURE)
    result = vendor_consumers.compare(registry, lock, FLUTTER)
    assert result["matches"] is True
    assert result["missing"] == result["unexpected"] == []
    entries = vendor_consumers.get_consumer(registry, FLUTTER)["entries"]
    assert len(entries) == len(lock["files"]) == 17
    assert {x["destination"] for x in entries} == {x["destination"] for x in lock["files"]}


def test_retired_git_inspector_is_explicit_skip_not_new_registration():
    catalog = load_flutter("vendor-catalog.json")
    registry = load_flutter("vendor-consumers.json")
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
    registry = load_flutter("vendor-consumers.json")
    lock = load_flutter(LOCK_FIXTURE)
    tampered = copy.deepcopy(lock)
    tampered["files"].pop()
    result = vendor_consumers.compare(registry, tampered, FLUTTER)
    assert result["matches"] is False
    assert len(result["missing"]) == 1
    assert result["unexpected"] == []
    assert len(lock["files"]) == 17


def test_gh_ops_matches_catalog_recommendation():
    catalog = load_flutter("vendor-catalog.json")
    lock = load_flutter(LOCK_FIXTURE)
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
    registry = load_flutter("vendor-consumers.json")
    catalog = load_flutter("vendor-catalog.json")
    lock = load_flutter(LOCK_FIXTURE)
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
    registry = load_flutter("vendor-consumers.json")
    old_lock = load_flutter("tests/fixtures/flutter_vendor_lock_782babef.json")
    assert not vendor_consumers.compare(registry, old_lock, FLUTTER)["matches"]
    old_shared = load_flutter("tests/fixtures/flutter_vendor_lock_f73c72eb.json")
    # The parent registry compares file topology, while exact source revisions
    # belong to each lock. Both generations intentionally keep 17 entries.
    assert vendor_consumers.compare(registry, old_shared, FLUTTER)["matches"]
    current_lock = load_flutter(LOCK_FIXTURE)
    old_pin = next(row for row in old_shared["files"]
                   if row["source"] == "scripts/browser_execution.mjs")
    new_pin = next(row for row in current_lock["files"]
                   if row["source"] == "scripts/browser_execution.mjs")
    assert old_pin["commit"] == "4be0f0d20b4cb6418ae97f64f3ca6aea6a75292c"
    assert new_pin["commit"] != old_pin["commit"]
    before_handoff = load_flutter("tests/fixtures/flutter_vendor_lock_1dd33afc.json")
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
        workflow_fixture = "tests/fixtures/flutter_non_dart_ci_python_241.yml"
        expected_sha = "2797b004499b14c9d530857566e80c0a1dadfd8e"
    else:
        registry = load_flutter("vendor-consumers.json")
        heads = load_flutter("child-heads.json")
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
    lock = load_flutter(LOCK_FIXTURE)
    catalog = load_flutter("vendor-catalog.json")
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

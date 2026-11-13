"""Parent topology includes the reviewed mcp-toolcall-lab lock plus web_ui enrollment."""
import copy, json
from pathlib import Path

import vendor_consumers
import vendor_fleet_plan

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = "myon-bioinformatics/mcp-toolcall-lab"


def load(path):
    return json.loads((ROOT / path).read_text())


def reviewed_pr159_registry():
    """Reconstruct PR #159 topology; web UI and planner were adopted later."""
    registry = copy.deepcopy(load("vendor-consumers.json"))
    for row in registry["consumers"]:
        if row["repository"] == CONSUMER:
            row["entries"] = [e for e in row["entries"] if e["destination"] not in {"vendor/pysaiyanweboo.py", "vendor/vendor_fleet_plan.py"}]
    return registry


def test_mcp_toolcall_lab_registry_matches_reviewed_pr159_lock():
    registry = reviewed_pr159_registry()
    lock = load("tests/fixtures/mcp-toolcall-lab-pr159.lock.json")
    assert len(lock["files"]) == 9
    assert vendor_consumers.compare(registry, lock, CONSUMER)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, CONSUMER)["matches"]


def test_node_launch_is_registered_for_mcp_toolcall_lab_not_auto_proposed():
    result = vendor_fleet_plan.plan(
        load("vendor-catalog.json"),
        load("vendor-consumers.json"),
        active_consumers=[CONSUMER],
    )
    node_launch = [x for x in result["rows"] if x["source"] == "node_launch.py"]
    assert len(node_launch) == 1
    assert node_launch[0]["action"] == "registered"
    assert node_launch[0]["destination"] == "vendor/node_launch.py"
    web_ui = [x for x in result["rows"] if x["source"] == "web_ui.py"]
    assert len(web_ui) == 1
    assert web_ui[0]["action"] == "needs_registration"
    # The old catalog recommendation is not the desired canonical-module contract.
    assert web_ui[0]["destination"] is None
    assert result["counts"]["registered"] == 6
    assert result["counts"]["skipped"] == 34


def test_current_planner_registration_matches_immutable_child_lock():
    import hashlib
    import vendor_sync

    snapshot = load("tests/fixtures/reviewed_mcp_planner_lock_20261109.json")[CONSUMER]
    old = load("tests/fixtures/reviewed_mcp_lock_20261109.json")[CONSUMER]
    assert old["sha"] == "31ff99fbe4c13b3feb570ae3ba1fc7cf972f8a16"
    assert snapshot["sha"] == "cd04472040f0db991c5138144c01f56a2b79e44b"
    heads = load("child-heads.json")["heads"][CONSUMER]
    assert (heads["sha"], heads["repository_id"]) == (snapshot["sha"], snapshot["repository_id"])
    lock = vendor_sync.validate(snapshot["lock"])
    assert hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == snapshot["lock_sha256"]
    registry = load("vendor-consumers.json")
    assert not vendor_consumers._compare_exact(registry, lock, CONSUMER)["matches"]
    from unittest.mock import patch
    with patch.object(vendor_sync, '_load_recorded_child_lock', return_value=lock):
        registry, reports = vendor_sync._registry_for_recorded_transitions(
            registry, load('child-heads.json'), load('vendor-observation-transitions.json'), [CONSUMER])
    assert len(reports) == 1 and reports[0]['recorded_sha'] == snapshot['sha']
    assert vendor_consumers._compare_exact(registry, lock, CONSUMER)["matches"]
    planner = [r for r in lock["files"] if r["destination"] == "vendor/vendor_fleet_plan.py"]
    assert len(planner) == 1
    assert planner[0]["source"] == "vendor_fleet_plan.py"
    assert planner[0]["repository"] == "myon-bioinformatics/myon-bioinformatics"
    assert all(r["ref"] == r["commit"] for r in lock["files"])
    result = vendor_fleet_plan.plan(load("vendor-catalog.json"), registry, active_consumers=[CONSUMER])
    registered = {(r["tool_repository"], r["source"], r["destination"]) for r in result["rows"] if r["action"] == "registered"}
    locked = {(r["repository"], r["source"], r["destination"]) for r in lock["files"]}
    assert len(registered) == result["counts"]["registered"] == 7
    assert registered <= locked
    for destination in ("vendor/vendor_fleet_plan.py", "vendor/web_ui.py"):
        changed = copy.deepcopy(lock)
        changed["files"] = [r for r in changed["files"] if r["destination"] != destination]
        assert not vendor_consumers._compare_exact(registry, changed, CONSUMER)["matches"]

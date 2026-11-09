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
    """The registry as PR #159 adopted it: later enrollments (web_ui.py) wait for their own lock."""
    registry = copy.deepcopy(load("vendor-consumers.json"))
    for row in registry["consumers"]:
        if row["repository"] == CONSUMER:
            row["entries"] = [e for e in row["entries"] if e["destination"] != "vendor/web_ui.py"]
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
    assert web_ui[0]["action"] == "registered"
    assert web_ui[0]["destination"] == "vendor/web_ui.py"
    assert result["counts"]["registered"] == 6
    assert result["counts"]["skipped"] == 35

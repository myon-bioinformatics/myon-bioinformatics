"""Parent topology follows markdown PR #125 vendor.lock.json snapshot."""
import copy
import json
from pathlib import Path

import vendor_consumers
import vendor_fleet_plan

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = "myon-bioinformatics/markdown"


def load(path):
    return json.loads((ROOT / path).read_text())


def test_markdown_registry_matches_reviewed_pr125_lock():
    registry = load("vendor-consumers.json")
    lock = load("tests/fixtures/markdown-pr125.lock.json")
    assert len(lock["files"]) == 7
    assert vendor_consumers.compare(registry, lock, CONSUMER)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, CONSUMER)["matches"]


def test_node_launch_is_registered_for_markdown_not_auto_proposed():
    result = vendor_fleet_plan.plan(
        load("vendor-catalog.json"),
        load("vendor-consumers.json"),
        active_consumers=[CONSUMER],
    )
    node_launch = [x for x in result["rows"] if x["source"] == "node_launch.py"]
    assert len(node_launch) == 1
    assert node_launch[0]["action"] == "registered"
    assert node_launch[0]["destination"] == "vendor/node_launch.py"
    assert result["counts"]["registered"] == 3
    assert result["counts"]["skipped"] == 36

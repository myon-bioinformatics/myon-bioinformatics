"""Parent topology follows cli_args PR #26 vendor.lock.json snapshot."""
import copy
import json
from pathlib import Path

import vendor_consumers
import vendor_fleet_plan

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = "myon-bioinformatics/cli_args"


def load(path):
    return json.loads((ROOT / path).read_text())


def test_cli_args_registry_matches_reviewed_pr26_lock():
    registry = load("vendor-consumers.json")
    lock = load("tests/fixtures/cli-args-pr26.lock.json")
    assert len(lock["files"]) == 2
    assert vendor_consumers.compare(registry, lock, CONSUMER)["matches"]
    assert all(x["ref"] == x["commit"] for x in lock["files"])
    changed = copy.deepcopy(lock)
    changed["files"].pop()
    assert not vendor_consumers.compare(registry, changed, CONSUMER)["matches"]


def test_py_launch_is_registered_for_cli_args_not_auto_proposed():
    result = vendor_fleet_plan.plan(
        load("vendor-catalog.json"),
        load("vendor-consumers.json"),
        active_consumers=[CONSUMER],
    )
    py_launch = [x for x in result["rows"] if x["source"] == "py_launch.cjs"]
    assert len(py_launch) == 1
    assert py_launch[0]["action"] == "registered"
    assert py_launch[0]["destination"] == "vendor/py_launch.cjs"
    assert result["counts"]["registered"] == 1

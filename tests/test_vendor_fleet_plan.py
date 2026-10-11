"""Read-only fleet plan tests with no external GitHub or network access."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import vendor_fleet_plan


def fixtures():
    tool = {"schema": "vendor-catalog/1", "tools": [{
        "repository": "owner/tool", "source": "tool.py",
        "commit": "a" * 40, "reason": "reviewed"}]}
    registry = {"schema": "vendor-consumers/1", "consumers": [
        {"repository": "owner/app", "state": "locked", "lock": "vendor.lock.json",
         "entries": [{"repository": "owner/tool", "source": "tool.py", "destination": "vendor/tool.py"}]},
        {"repository": "owner/other", "state": "locked", "lock": "vendor.lock.json", "entries": [{"repository": "owner/other-tool", "source": "other.py", "destination": "vendor/other.py"}]},
    ]}
    return tool, registry


def test_registered_and_missing_are_distinct_and_inputs_unchanged():
    cat, reg = fixtures()
    before = copy.deepcopy((cat, reg))
    result = vendor_fleet_plan.plan(cat, reg)
    assert result["schema"] == "vendor-fleet-plan/1"
    assert result["read_only"] is True
    assert result["candidate_compatibility"] == "unverified"
    assert result["counts"]["registered"] == 1
    assert result["counts"]["needs_registration"] == 1
    assert result["rows"][0]["destination"] == "vendor/tool.py"
    assert result["rows"][1]["destination"] is None
    assert (cat, reg) == before


def test_skip_override_and_explicit_selection():
    cat, reg = fixtures()
    cat["tools"][0]["default_enrollment"] = {
        "enrollment": "skipped", "reason": "requires_external_package"}
    result = vendor_fleet_plan.plan(cat, reg, active_consumers=["OWNER/other"])
    assert len(result["rows"]) == 1
    assert result["rows"][0]["action"] == "skipped"
    cat["tools"][0]["consumers"] = {
        "owner/other": {"enrollment": "enrolled", "reason": "verified_safe_copy"}}
    result = vendor_fleet_plan.plan(cat, reg, active_consumers=["owner/other"])
    assert result["rows"][0]["action"] == "needs_registration"


def test_legacy_is_not_silently_treated_as_enrolled():
    cat, reg = fixtures()
    reg["consumers"][1] = {
        "repository": "owner/legacy", "state": "legacy", "lock": None,
        "reason": "legacy_no_lock", "legacy_provenance": ["vendor/tool.provenance.json"],
        "entries": [{"repository": "owner/tool", "source": "tool.py",
                     "destination": "vendor/tool.py"}]}
    result = vendor_fleet_plan.plan(cat, reg)
    assert result["rows"][-1]["action"] == "legacy_consumer_review"
    assert result["rows"][-1]["destination"] == "vendor/tool.py"


def test_unknown_consumer_is_error():
    cat, reg = fixtures()
    with pytest.raises(ValueError, match="unknown consumers"):
        vendor_fleet_plan.plan(cat, reg, active_consumers=["owner/missing"])


def test_checked_in_fleet_and_cli_read_only(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cat = json.loads((root / "vendor-catalog.json").read_text(encoding="utf-8"))
    reg = json.loads((root / "vendor-consumers.json").read_text(encoding="utf-8"))
    result = vendor_fleet_plan.plan(cat, reg, active_consumers=["myon-bioinformatics/Ironmate"])
    assert result["rows"]
    assert result["candidate_compatibility"] == "unverified"
    assert all(r["consumer"] == "myon-bioinformatics/Ironmate" for r in result["rows"])
    out = subprocess.run(
        [sys.executable, "-S", str(root / "vendor_fleet_plan.py"),
         "--catalog", str(root / "vendor-catalog.json"),
         "--registry", str(root / "vendor-consumers.json"),
         "--consumer", "myon-bioinformatics/Ironmate"],
        cwd=tmp_path, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["read_only"]
    assert not list(tmp_path.iterdir())

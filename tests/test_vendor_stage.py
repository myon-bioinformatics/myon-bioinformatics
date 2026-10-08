import json
from pathlib import Path
import pytest
import vendor_stage

def fixture(root):
    (root / "vendor").mkdir()
    (root / "vendor/one.py").write_text("one")
    (root / "vendor.lock.json").write_text("{}")
    (root / "legacy.json").write_text("legacy")

def evidence(manifest, root, runtime=()):
    return {"schema": "vendor-evidence/1", "locked": ["vendor.lock.json", "vendor/one.py"],
            "candidate": ["vendor.lock.json", "vendor/one.py"], "runtime": list(runtime)}

@pytest.mark.parametrize("kind,runtime", [("locked", ()), ("candidate", ()), ("candidate", ("receipt.json",))])
def test_stage_members(tmp_path, monkeypatch, kind, runtime):
    fixture(tmp_path)
    if runtime:
        (tmp_path / "receipt.json").write_text("receipt")
    monkeypatch.setattr(vendor_stage.vendor_sync, "evidence", evidence)
    result = vendor_stage.stage(tmp_path, "vendor.lock.json", "build/out", kind=kind,
                                runtime=runtime, legacy=["legacy.json"])
    assert result["schema"] == "vendor-stage/1"
    assert (tmp_path / "build/out/vendor/one.py").read_text() == "one"
    assert (tmp_path / "build/out/legacy.json").read_text() == "legacy"
    assert (tmp_path / "build/out/vendor-evidence.json").exists()

def test_existing_output_rejected(tmp_path, monkeypatch):
    fixture(tmp_path)
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(vendor_stage.vendor_sync, "evidence", evidence)
    with pytest.raises(ValueError, match="existing"):
        vendor_stage.stage(tmp_path, "vendor.lock.json", "out")

def test_unsafe_member_rejected_before_placement(tmp_path, monkeypatch):
    fixture(tmp_path)
    def unsafe(*args, **kwargs):
        return {"schema": "vendor-evidence/1", "locked": ["../escape"],
                "candidate": [], "runtime": []}
    monkeypatch.setattr(vendor_stage.vendor_sync, "evidence", unsafe)
    with pytest.raises(ValueError):
        vendor_stage.stage(tmp_path, "vendor.lock.json", "out")
    assert not (tmp_path / "out").exists()

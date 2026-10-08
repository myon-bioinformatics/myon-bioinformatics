"""Real lock/byte staging regression: no mocked evidence derivation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import vendor_stage
import vendor_sync


def fixture(root, destinations=("vendor/one.py",)):
    entries = []
    for destination in destinations:
        path = root / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"# staged source\n"
        path.write_bytes(data)
        entries.append(dict(repository="owner/repo", ref="refs/heads/main",
                            commit="a" * 40, source=destination, destination=destination,
                            blob_sha=vendor_sync.git_blob(data), sha256=hashlib.sha256(data).hexdigest()))
    (root / "vendor.lock.json").write_text(json.dumps(dict(schema="vendor-lock/1", files=entries)), encoding="utf-8")
    (root / "legacy.json").write_text("{}", encoding="utf-8")


def stage(root, **kwargs):
    return vendor_stage.stage(root, "vendor.lock.json", "build/out", **kwargs)


@pytest.mark.parametrize("kind,receipt", [("locked", False), ("locked", True), ("candidate", False), ("candidate", True)])
def test_membership_and_digests(tmp_path, kind, receipt):
    fixture(tmp_path, ("vendor/one.py", "vendor/LICENSE", "vendor/追加.py"))
    if receipt:
        (tmp_path / "receipt.json").write_text('{"schema":"vendor-promotion/1"}', encoding="utf-8")
    result = stage(tmp_path, kind=kind, legacy=["legacy.json"], promotion_receipt="receipt.json")
    output = tmp_path / "build/out"
    evidence = json.loads((output / vendor_stage.METADATA).read_text(encoding="utf-8"))
    derived = vendor_sync.evidence("vendor.lock.json", tmp_path)
    assert evidence["locked"] == evidence["candidate"] == derived["locked"]
    assert evidence["runtime"] == (["receipt.json"] if kind == "candidate" and receipt else [])
    assert evidence["legacy"] == ["legacy.json"]
    assert evidence["kind"] == kind
    files = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    assert files == set(result["members"]) | {vendor_stage.METADATA}
    assert set(evidence["sha256"]) == set(result["members"])
    for name, digest in evidence["sha256"].items():
        assert (output / name).read_bytes() == (tmp_path / name).read_bytes()
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("path", ["../outside", "/tmp/absolute", ".", "", "build/../out", "build\\out", "C:out", ".git/out", "build//out"])
def test_invalid_output(tmp_path, path):
    fixture(tmp_path)
    with pytest.raises(ValueError):
        vendor_stage.stage(tmp_path, "vendor.lock.json", path)
    assert not (tmp_path / "build/out").exists()


@pytest.mark.parametrize("supplement", ["../outside", "missing.json", "vendor/one.py", "VENDOR/ONE.PY", "vendor", "vendor-evidence.json", "Vendor-Evidence.json/child"])
def test_invalid_legacy_before_placement(tmp_path, supplement):
    fixture(tmp_path)
    with pytest.raises((ValueError, OSError)):
        stage(tmp_path, legacy=[supplement])
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize("change", ["bytes", "missing", "lock", "manifest-collision", "metadata-collision"])
def test_invalid_locked_inputs_before_placement(tmp_path, change):
    fixture(tmp_path)
    if change == "bytes":
        (tmp_path / "vendor/one.py").write_text("edited")
    elif change == "missing":
        (tmp_path / "vendor/one.py").unlink()
    elif change == "lock":
        (tmp_path / "vendor.lock.json").write_text("{}")
    else:
        lock = json.loads((tmp_path / "vendor.lock.json").read_text())
        lock["files"][0]["destination"] = "vendor.lock.json" if change == "manifest-collision" else vendor_stage.METADATA
        (tmp_path / "vendor.lock.json").write_text(json.dumps(lock))
    with pytest.raises((ValueError, OSError)):
        stage(tmp_path)
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize("which", ["manifest", "member", "member-parent", "output-parent", "dangling-output", "receipt"])
def test_symlinks_rejected(tmp_path, which):
    fixture(tmp_path)
    try:
        if which == "manifest":
            (tmp_path / "vendor.lock.json").rename(tmp_path / "real.lock.json")
            (tmp_path / "vendor.lock.json").symlink_to("real.lock.json")
        elif which == "member":
            (tmp_path / "vendor/one.py").rename(tmp_path / "one.py")
            (tmp_path / "vendor/one.py").symlink_to("../one.py")
        elif which == "member-parent":
            (tmp_path / "vendor").rename(tmp_path / "real-vendor")
            (tmp_path / "vendor").symlink_to("real-vendor", target_is_directory=True)
        elif which == "output-parent":
            (tmp_path / "real-build").mkdir()
            (tmp_path / "build").symlink_to("real-build", target_is_directory=True)
        elif which == "dangling-output":
            (tmp_path / "build").mkdir()
            (tmp_path / "build/out").symlink_to("missing")
        else:
            (tmp_path / "receipt.json").symlink_to("missing")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="symlink"):
        stage(tmp_path, kind="candidate", promotion_receipt="receipt.json")


def test_existing_output_and_source_overlap(tmp_path):
    fixture(tmp_path)
    (tmp_path / "build/out").mkdir(parents=True)
    (tmp_path / "build/out/keep").write_text("keep")
    with pytest.raises(ValueError, match="existing"):
        stage(tmp_path)
    assert (tmp_path / "build/out/keep").read_text() == "keep"
    with pytest.raises(ValueError, match="overlaps"):
        vendor_stage.stage(tmp_path, "vendor.lock.json", "VENDOR")


def test_runtime_required_and_separate(tmp_path):
    fixture(tmp_path)
    with pytest.raises(ValueError):
        stage(tmp_path, runtime=["missing.json"])
    with pytest.raises(ValueError, match="collides"):
        stage(tmp_path, runtime=["vendor.lock.json"])
    with pytest.raises(ValueError, match="duplicate"):
        stage(tmp_path, legacy=["legacy.json", "legacy.json"])


def test_cli_stdlib_only(tmp_path):
    fixture(tmp_path)
    script = Path(vendor_stage.__file__)
    command = [sys.executable, "-S", str(script), "--root", str(tmp_path),
               "--output", "build/out", "--kind", "candidate", "--promotion-receipt", "receipt.json",
               "--legacy-evidence", "legacy.json"]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema"] == "vendor-stage/1"
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert "existing" in result.stderr

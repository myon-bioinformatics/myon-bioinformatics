"""Pinned old/new vendor consumer compatibility matrix (offline, stdlib CLI).

The dedicated workflow checks out both historical parent and Ironmate at
immutable commit SHAs. Ordinary Python test jobs skip these tests because
their shallow checkouts do not contain those external snapshots.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import vendor_consumers
import vendor_sync

PARENT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_SHA = "1eda992aa6fa31a902e2c06428e53ab17e8fe3c1"
IRONMATE_SHA = "77cd700f961f2a97051da96b9c13a6c7a64258df"
IRONMATE_NAME = "myon-bioinformatics/Ironmate"
PARENT_NAME = "myon-bioinformatics/myon-bioinformatics"
RETIRED = {"git_inspector.py", "vendor_sync.py"}


@pytest.fixture(scope="module")
def snapshots():
    old = os.environ.get("VENDOR_LEGACY_ROOT")
    iron = os.environ.get("IRONMATE_COMPAT_ROOT")
    if not old and not iron:
        pytest.skip("pinned compatibility snapshots exercised in dedicated workflow")
    assert old and iron, "both immutable snapshot roots must be configured"
    old, iron = Path(old).resolve(), Path(iron).resolve()
    for path in (old / "vendor_consumers.py", old / "vendor_sync.py",
                 old / "vendor-consumers.json", iron / "vendor.lock.json",
                 iron / ".github/workflows/vendor-catalog-audit.yml"):
        assert path.is_file(), f"missing pinned snapshot: {path}"
    for path, expected in ((old, LEGACY_SHA), (iron, IRONMATE_SHA)):
        actual = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert actual == expected, f"{path} expected {expected}, got {actual}"
    return old, iron


def _project_retired_parent_tools(doc):
    """Apply exactly the projection in Ironmate's catalog-audit workflow."""
    doc = json.loads(json.dumps(doc))
    for consumer in doc["consumers"]:
        if consumer["repository"] == IRONMATE_NAME:
            consumer["entries"] = [
                entry for entry in consumer["entries"]
                if not (entry["repository"] == PARENT_NAME and entry["source"] in RETIRED)
            ]
    return doc


def _invoke(tool_root, mode, registry_path, lock_path=None):
    args = [sys.executable, "-S", str(tool_root / "vendor_consumers.py"), mode,
            "--registry", str(registry_path)]
    if lock_path:
        args += ["--consumer", IRONMATE_NAME, "--lock", str(lock_path)]
    return subprocess.run(args, capture_output=True, text=True, cwd=tool_root)


@pytest.fixture
def documents(tmp_path, snapshots):
    old_root, iron_root = snapshots
    old = json.loads((old_root / "vendor-consumers.json").read_text(encoding="utf-8"))
    new = json.loads((PARENT_ROOT / "vendor-consumers.json").read_text(encoding="utf-8"))
    assert not any("license" in entry for c in old["consumers"] for entry in c["entries"])
    assert any("license" in entry for c in new["consumers"] for entry in c["entries"])
    paths = {}
    for label, data in (("old", old), ("new", new)):
        path = tmp_path / (label + ".json")
        path.write_text(json.dumps(_project_retired_parent_tools(data)), encoding="utf-8")
        paths[label] = path
    return paths, iron_root / "vendor.lock.json"


@pytest.mark.parametrize(("tool_version", "registry_version", "expected_code"), [
    ("old", "old", 0),
    ("old", "new", 2),
    ("new", "old", 0),
    ("new", "new", 0),
])
@pytest.mark.parametrize("mode", ["validate", "compare"])
def test_actual_historical_tool_registry_matrix(snapshots, documents, mode,
                                                tool_version, registry_version, expected_code):
    old_root, _ = snapshots
    registry_paths, lock_path = documents
    tool_root = old_root if tool_version == "old" else PARENT_ROOT
    proc = _invoke(tool_root, mode, registry_paths[registry_version],
                   lock_path if mode == "compare" else None)
    assert proc.returncode == expected_code, (tool_version, registry_version, mode,
                                               proc.stdout[:1000], proc.stderr)
    assert "Traceback" not in proc.stderr
    if expected_code == 2:
        assert "invalid consumer entry fields" in proc.stderr
        assert not proc.stdout
    else:
        assert not proc.stderr
        output = json.loads(proc.stdout)
        if mode == "compare":
            assert output["matches"] is True
            assert output["missing"] == []
            assert output["unexpected"] == []
            if tool_version == "new":
                assert output["bundle_mismatch"] == []
            else:
                assert "bundle_mismatch" not in output  # historical output contract


def test_old_and_new_registry_ironmate_topology_and_lock_metadata(snapshots, documents):
    old_root, _ = snapshots
    registry_paths, lock_path = documents
    expanded = {}
    for version in ("old", "new"):
        proc = _invoke(PARENT_ROOT, "validate", registry_paths[version])
        assert proc.returncode == 0, proc.stderr
        doc = json.loads(proc.stdout)
        entries = next(c["entries"] for c in doc["consumers"] if c["repository"] == IRONMATE_NAME)
        expanded[version] = {
            (x["repository"].casefold(), x["source"], x["destination"].casefold())
            for x in entries
        }
    assert expanded["old"] == expanded["new"]
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    assert lock["schema"] == "vendor-lock/1"
    vendor_sync.validate(lock)
    lock_keys = {(x["repository"].casefold(), x["source"], x["destination"].casefold())
                 for x in lock["files"]}
    assert expanded["new"] == lock_keys
    # A registry shorthand must never replace the canonical, content-verified lock rows.
    for row in lock["files"]:
        assert len(row["commit"]) == 40
        assert len(row["blob_sha"]) == 40
        assert len(row["sha256"]) == 64
        assert row["ref"]
    assert lock_path.read_bytes() == lock_bytes


def test_ironmate_current_workflow_fetches_tool_and_registry_from_same_pin(snapshots):
    _, iron_root = snapshots
    workflow = (iron_root / ".github/workflows/vendor-catalog-audit.yml").read_text(
        encoding="utf-8")
    assert "ref: " + LEGACY_SHA in workflow
    assert "vendor_consumers.py" in workflow
    assert "vendor-consumers.json" in workflow
    assert "sparse-checkout:" in workflow
    assert "entry[\"repository\"]" in workflow
    assert "entry[\"source\"]" in workflow

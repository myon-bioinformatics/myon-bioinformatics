"""Offline, deterministic coverage for read-only consumer lock reconciliation."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vendor_lock_reconcile as mod


def fixture():
    source = "myon-bioinformatics/markdown"
    consumer = "myon-bioinformatics/example"
    sha = "a" * 40
    catalog = {"schema": "vendor-catalog/1", "tools": [
        {"repository": source, "source": "markdown.py", "commit": sha, "reason": "test"}]}
    registry = {"schema": "vendor-consumers/1", "consumers": [
        {"repository": consumer, "state": "locked", "lock": "vendor.lock.json",
         "entries": [{"repository": source, "source": "markdown.py", "destination": "vendor/markdown.py"}]}]}
    lock = {"schema": "vendor-lock/1", "files": [
        {"repository": source, "source": "markdown.py", "destination": "vendor/markdown.py",
         "ref": "refs/heads/main", "commit": sha, "blob_sha": "b"*40, "sha256": "c"*64}]}
    return catalog, registry, consumer, lock


def test_matching_is_reproducible_and_read_only():
    catalog, registry, consumer, lock = fixture()
    snapshot = copy.deepcopy((catalog, registry, lock))
    a = mod.reconcile(catalog, registry, {consumer: lock}, consumers=[consumer])
    b = mod.reconcile(catalog, registry, {consumer: lock}, consumers=[consumer])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert (catalog, registry, lock) == snapshot
    row = a["results"][0]
    assert row["topology"]["matches"]
    assert row["baseline_mismatches"] == []
    assert row["unregistered_candidates"] == []
    assert a["read_only"] and a["compatibility"] == "unverified"


def test_drift_and_missing_candidates_are_separate():
    catalog, registry, consumer, lock = fixture()
    catalog["tools"].append({"repository": "myon-bioinformatics/ascii_artist",
                             "source": "ascii_artist.py", "commit": "d"*40, "reason": "test"})
    lock["files"][0]["commit"] = "e"*40
    lock["files"][0]["destination"] = "vendor/wrong.py"
    row = mod.reconcile(catalog, registry, {consumer: lock}, consumers=[consumer])["results"][0]
    assert len(row["topology"]["missing"]) == 1
    assert len(row["topology"]["unexpected"]) == 1
    assert len(row["baseline_mismatches"]) == 1
    assert len(row["unregistered_candidates"]) == 1
    assert len(row["baseline_absent"]) == 1


def test_skip_is_not_registration():
    catalog, registry, consumer, lock = fixture()
    catalog["tools"].append({"repository": "myon-bioinformatics/ascii_artist",
                             "commit": "d"*40, "source": "ascii_artist.py", "reason": "test",
                             "default_enrollment": {"enrollment": "skipped", "reason": "requires_external_package"}})
    row = mod.reconcile(catalog, registry, {consumer: lock}, consumers=[consumer])["results"][0]
    assert len(row["explicit_skips"]) == 1
    assert row["unregistered_candidates"] == []


@pytest.mark.parametrize("bad", [None, {}, {"schema": "vendor-lock/1", "files": []}])
def test_invalid_lock_fails_closed(bad):
    catalog, registry, consumer, _ = fixture()
    with pytest.raises(ValueError):
        mod.reconcile(catalog, registry, {consumer: bad}, consumers=[consumer])


def test_missing_lock_fails_closed():
    catalog, registry, consumer, lock = fixture()
    with pytest.raises(ValueError, match="missing actual lock"):
        mod.reconcile(catalog, registry, {}, consumers=[consumer])


def test_legacy_requires_no_lock():
    catalog, registry, consumer, lock = fixture()
    registry["consumers"][0].update(state="legacy", lock=None, reason="migration_pending",
                                    legacy_provenance=["vendor/README.md"])
    report = mod.reconcile(catalog, registry, {}, consumers=[consumer])
    assert report["results"][0]["state"] == "legacy"
    assert report["results"][0]["review_required"]


def test_cli_offline_and_failure_codes(tmp_path):
    catalog, registry, consumer, lock = fixture()
    files = []
    for filename, data in (("catalog.json", catalog), ("registry.json", registry), ("lock.json", lock)):
        p = tmp_path / filename
        p.write_text(json.dumps(data), encoding="utf-8")
        files.append(p)
    out = tmp_path / "report.json"
    args = ["--catalog", str(files[0]), "--registry", str(files[1]),
            "--consumer", consumer, "--lock", consumer + "=" + str(files[2]),
            "--output", str(out), "--fail-on-drift"]
    assert mod.main(args) == 0
    assert json.loads(out.read_text())["schema"] == mod.SCHEMA
    lock["files"][0]["commit"] = "d"*40
    files[2].write_text(json.dumps(lock), encoding="utf-8")
    assert mod.main(args) == 1
    assert mod.main(args + ["--lock", "not-selected=" + str(files[2])]) == 2

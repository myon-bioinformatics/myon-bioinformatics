"""Offline, deterministic coverage for read-only consumer lock reconciliation."""
import base64
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


def sample_review(catalog, consumer, revision):
    item = catalog["tools"][0]
    return {"schema": mod.REVIEW_SCHEMA, "purpose": "test",
            "reviews": [{
                "consumer": consumer, "consumer_revision": revision,
                "tool_repository": item["repository"], "source": item["source"],
                "recommended_commit": item["commit"],
                "proposed_destination": "vendor/markdown.py",
                "disposition": "scoped_ci_trial_candidate",
                "reason": "stdlib_and_license_checked",
                "registration_approved": False, "source_examined": True,
                "source_dependencies": "python_stdlib_only", "upstream_license": "MIT",
                "license_source": "LICENSE", "license_verified_at_recommended_commit": True,
                "consumer_ci_workflow": ".github/workflows/test.yml",
                "consumer_ci_with_candidate": "not_run",
                "existing_destination_collision": None}]}


def test_manual_evidence_is_never_automatic_registration():
    cat, registry, consumer, lock = fixture()
    # An unregistered source is eligible for a test-only trial, not enrollment.
    registry["consumers"][0]["entries"] = [{
        "repository": "myon-bioinformatics/cli_args",
        "source": "cli_args.py", "destination": "vendor/cli_args.py"}]
    revision = "d" * 40
    evidence = sample_review(cat, consumer, revision)
    metadata = {consumer: {"resolution": "pinned_github_commit", "commit": revision}}
    row = mod.reconcile(cat, registry, {consumer: lock}, consumers=[consumer],
                        metadata=metadata, evidence=evidence)["results"][0]
    candidate = row["unregistered_candidates"][0]
    assert candidate["review"]["evidence_status"] == "current"
    assert candidate["review"]["disposition"] == "scoped_ci_trial_candidate"
    assert candidate["review"]["registration_approved"] is False
    assert row["counts"]["scoped_ci_trial_candidates"] == 1
    metadata[consumer]["commit"] = "e" * 40
    stale = mod.reconcile(cat, registry, {consumer: lock}, consumers=[consumer],
                          metadata=metadata, evidence=evidence)["results"][0]
    assert stale["unregistered_candidates"][0]["review"]["evidence_status"] == "stale_or_unpinned"
    assert stale["counts"]["scoped_ci_trial_candidates"] == 0


def test_duplicate_and_forged_review_rejected():
    cat, registry, consumer, lock = fixture()
    evidence = sample_review(cat, consumer, "d"*40)
    evidence["reviews"].append(copy.deepcopy(evidence["reviews"][0]))
    with pytest.raises(ValueError, match="duplicate candidate review"):
        mod.reconcile(cat, registry, {consumer: lock}, consumers=[consumer], evidence=evidence)
    evidence["reviews"].pop()
    evidence["reviews"][0]["registration_approved"] = True
    with pytest.raises(ValueError, match="approval claim"):
        mod.reconcile(cat, registry, {consumer: lock}, consumers=[consumer], evidence=evidence)


def test_remote_content_is_commit_pinned_and_git_blob_checked(monkeypatch):
    _, _, consumer, lock = fixture()
    payload = json.dumps(lock).encode("utf-8")
    revision = "e" * 40
    calls = []

    def fake_json(url, token=None):
        calls.append(url)
        if "/commits/" in url:
            return {"sha": revision}
        return {"type": "file", "encoding": "base64",
                "content": base64.b64encode(payload).decode("ascii"),
                "sha": mod.vendor_sync.git_blob(payload)}
    monkeypatch.setattr(mod, "_json_api", fake_json)
    assert mod.fetch_revision(consumer, "main") == revision
    assert mod.fetch_lock(consumer, "vendor.lock.json", ref=revision)[0] == lock
    assert calls[1].endswith("?ref=" + revision)
    assert mod.fetch_lock(consumer, "vendor.lock.json", ref=revision)[1]["lock_sha256"]
    def bad_json(url, token=None):
        record = fake_json(url, token)
        record["sha"] = "0" * 40
        return record
    monkeypatch.setattr(mod, "_json_api", bad_json)
    with pytest.raises(ValueError, match="blob mismatch"):
        mod.fetch_lock(consumer, "vendor.lock.json", ref=revision)


def test_remote_rejects_bad_base64_and_malformed_revision(monkeypatch):
    _, _, consumer, _ = fixture()
    monkeypatch.setattr(mod, "_json_api", lambda *a, **k: {"sha": "short"})
    with pytest.raises(ValueError, match="hex"):
        mod.fetch_revision(consumer, "main")
    monkeypatch.setattr(mod, "_json_api", lambda *a, **k: {
        "type": "file", "encoding": "base64", "content": "%%%bad%%%!", "sha": "0"*40})
    with pytest.raises(base64.binascii.Error):
        mod.fetch_lock(consumer, "vendor.lock.json", ref="f"*40)


def test_real_review_snapshot_has_no_approved_registration():
    evidence = json.loads((Path(__file__).resolve().parents[1] /
                           "docs/vendor-registration-review.json").read_text(encoding="utf-8"))
    by_key = mod._review_index(evidence)
    assert len(by_key) == 13
    assert len({r["consumer"] for r in evidence["reviews"]}) == 2
    assert sum(r["disposition"] == "scoped_ci_trial_candidate" for r in evidence["reviews"]) == 6
    assert all(r["registration_approved"] is False for r in evidence["reviews"])

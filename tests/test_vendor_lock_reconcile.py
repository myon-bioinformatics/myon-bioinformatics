"""Offline, deterministic coverage for read-only consumer lock reconciliation."""
import copy
import hashlib
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
    prov = json.loads(out.read_text())["results"][0]["lock_provenance"]
    assert prov["resolution"] == "offline_manifest"
    assert prov["requested_ref"] is None
    assert prov["lock_sha256"] == hashlib.sha256(files[2].read_bytes()).hexdigest()
    assert prov["lock_blob_sha"] == mod.vendor_sync.git_blob(files[2].read_bytes())
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
    monkeypatch.setattr(mod.vendor_sync, "public_ref_sha", lambda *a, **k: revision)
    monkeypatch.setattr(mod.vendor_sync, "public_raw_bytes", lambda *a, **k: payload)
    assert mod.fetch_revision(consumer, "main") == revision
    fetched, meta = mod.fetch_lock(consumer, "vendor.lock.json", ref=revision)
    assert fetched == lock
    assert meta["lock_sha256"] == hashlib.sha256(payload).hexdigest()
    assert meta["source_url"] == (
        "https://raw.githubusercontent.com/" + consumer + "/" + revision + "/vendor.lock.json"
    )
    monkeypatch.setattr(mod.vendor_sync, "public_raw_bytes", lambda *a, **k: b"not-json")
    with pytest.raises(json.JSONDecodeError):
        mod.fetch_lock(consumer, "vendor.lock.json", ref=revision)


def test_remote_rejects_malformed_revision_and_missing_lock(monkeypatch):
    _, _, consumer, _ = fixture()
    monkeypatch.setattr(mod.vendor_sync, "public_ref_sha", lambda *a, **k: (_ for _ in ()).throw(
        ValueError("missing commit in atom feed")))
    with pytest.raises(ValueError, match="atom feed"):
        mod.fetch_revision(consumer, "main")
    monkeypatch.setattr(mod.vendor_sync, "public_raw_bytes", lambda *a, **k: None)
    with pytest.raises(ValueError, match="missing consumer lock"):
        mod.fetch_lock(consumer, "vendor.lock.json", ref="f" * 40)


def test_public_access_blocked_exits_nonzero(monkeypatch, tmp_path):
    catalog, registry, consumer, lock = fixture()
    paths = []
    for name, value in (("catalog.json", catalog), ("registry.json", registry)):
        file = tmp_path / name
        file.write_text(json.dumps(value), encoding="utf-8")
        paths.append(file)
    monkeypatch.setattr(
        mod.vendor_sync, "public_ref_sha",
        lambda *a, **k: (_ for _ in ()).throw(mod.vendor_sync.PublicGitAccessBlocked("blocked")),
    )
    assert mod.main(["--catalog", str(paths[0]), "--registry", str(paths[1]),
                     "--consumer", consumer, "--ref", "main"]) == 2


def test_real_review_snapshot_has_no_approved_registration():
    evidence = json.loads((Path(__file__).resolve().parents[1] /
                           "docs/vendor-registration-review.json").read_text(encoding="utf-8"))
    by_key = mod._review_index(evidence)
    assert len(by_key) == 13
    assert len({r["consumer"] for r in evidence["reviews"]}) == 2
    assert sum(r["disposition"] == "scoped_ci_trial_candidate" for r in evidence["reviews"]) == 6
    assert all(r["registration_approved"] is False for r in evidence["reviews"])


def test_cli_online_records_requested_ref_and_resolved_commit(tmp_path, monkeypatch):
    catalog, registry, consumer, lock = fixture()
    paths = []
    for name, value in (("catalog.json", catalog), ("registry.json", registry)):
        file = tmp_path / name
        file.write_text(json.dumps(value), encoding="utf-8")
        paths.append(file)
    revision = "f" * 40
    monkeypatch.setattr(mod, "fetch_revision", lambda *a, **k: revision)
    monkeypatch.setattr(mod, "fetch_lock", lambda *a, **k: (
        lock, {"resolution": "pinned_github_commit", "commit": revision,
               "lock_blob_sha": "a"*40, "lock_sha256": "b"*64}))
    result = tmp_path / "report.json"
    assert mod.main(["--catalog", str(paths[0]), "--registry", str(paths[1]),
                     "--consumer", consumer, "--ref", "main", "--output", str(result)]) == 0
    prov = json.loads(result.read_text(encoding="utf-8"))["results"][0]["lock_provenance"]
    assert prov["requested_ref"] == "main"
    assert prov["commit"] == revision
    assert prov["lock_blob_sha"] == "a"*40


def test_reconcile_workflow_refreshes_on_parent_registry_and_code_changes():
    """A registry-only update must regenerate the actual-lock report on main."""
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/vendor-lock-reconciliation.yml").read_text(encoding="utf-8")
    pr_section = workflow.split("  pull_request:\n", 1)[1].split("  push:\n", 1)[0]
    push_section = workflow.split("  push:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "    branches: [main]" in push_section
    for required in ("vendor-catalog.json", "vendor-consumers.json", "vendor_catalog.py",
                     "vendor_consumers.py", "vendor_fleet_plan.py", "vendor_sync.py",
                     "vendor_lock_reconcile.py", "docs/vendor-registration-review.json",
                     "tests/test_vendor_lock_reconcile.py",
                     ".github/workflows/vendor-lock-reconciliation.yml"):
        expected = f'      - "{required}"'
        assert expected in pr_section
        assert expected in push_section
    assert "  workflow_dispatch:" in workflow
    assert "--fail-on-drift" not in workflow
    assert "GH_TOKEN" not in workflow
    assert "  contents: read" in workflow


@pytest.mark.parametrize('fault', [None, 'missing', 'invalid'])
def test_cli_recorded_heads_fetch_exact_revision_without_live_fallback(tmp_path, monkeypatch, fault):
    catalog, registry, consumer, lock = fixture()
    revision = 'e' * 40
    heads = {'schema': mod.vendor_sync.CHILD_HEADS_SCHEMA, 'ref': 'main',
             'heads': {consumer: {'sha': revision, 'repository_id': 42}}}
    if fault == 'missing':
        heads['heads'] = {}
    elif fault == 'invalid':
        heads['heads'][consumer]['sha'] = 'main'
    for name, value in [('catalog', catalog), ('registry', registry), ('heads', heads)]:
        (tmp_path / (name + '.json')).write_text(json.dumps(value))
    monkeypatch.setattr(mod, 'fetch_revision', lambda *a, **k: pytest.fail('live fallback'))
    calls = []

    def fetch(repository, path, *, ref):
        calls.append((repository, path, ref))
        return lock, {'resolution': 'pinned_github_commit', 'commit': ref}

    monkeypatch.setattr(mod, 'fetch_lock', fetch)
    output = tmp_path / 'report.json'
    args = ['--catalog', str(tmp_path / 'catalog.json'), '--registry', str(tmp_path / 'registry.json'),
            '--heads', str(tmp_path / 'heads.json'), '--consumer', consumer, '--output', str(output)]
    assert mod.main(args) == (0 if fault is None else 2)
    if fault is None:
        assert calls == [(consumer, 'vendor.lock.json', revision)]
        provenance = json.loads(output.read_text())['results'][0]['lock_provenance']
        assert provenance['commit'] == provenance['requested_ref'] == revision
    else:
        assert calls == []
        assert not output.exists()


@pytest.mark.parametrize('event', ['pull_request', 'push', 'workflow_dispatch'])
def test_reconcile_workflow_selects_recorded_heads_only_for_pr(tmp_path, event):
    import os
    import shutil
    import subprocess
    import textwrap

    bash = shutil.which('bash')
    if not bash:
        pytest.skip('workflow requires bash')
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/vendor-lock-reconciliation.yml').read_text()
    block = workflow.split('      - name: Fetch real consumer locks (read only)\n', 1)[1]
    script = textwrap.dedent(block.split('        run: |\n', 1)[1].split('      - name:', 1)[0])
    stub = tmp_path / 'python'
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE_ARGS"\n')
    stub.chmod(0o755)
    capture = tmp_path / 'args.txt'
    env = dict(os.environ, EVENT_NAME=event, CAPTURE_ARGS=str(capture),
               PATH=str(tmp_path) + os.pathsep + os.environ.get('PATH', ''))
    subprocess.run([bash, '-euo', 'pipefail', '-c', script], cwd=tmp_path, env=env, check=True)
    args = capture.read_text().splitlines()
    assert args[:2] == ['-S', 'vendor_lock_reconcile.py']
    assert ('--heads' in args) == (event == 'pull_request')
    if event == 'pull_request':
        assert args[args.index('--heads') + 1] == 'child-heads.json'
    assert '--fail-on-drift' not in args

"""PR-added registry entries are pending enrollment until the child adopts them."""
import copy

import pytest

import vendor_sync as sync

CHILD = "owner/child"


def _registry(*extra):
    entries = [{"repository": "owner/tool", "source": "tool.py", "destination": "vendor/tool.py"}]
    entries += list(extra)
    return {"schema": "vendor-consumers/1", "consumers": [
        {"repository": CHILD, "state": "locked", "lock": "vendor.lock.json", "entries": entries},
        {"repository": sync.PARENT_REPOSITORY, "state": "locked", "lock": "vendor.lock.json",
         "entries": [{"repository": "owner/tool", "source": "tool.py", "destination": "vendor/tool.py"}]},
    ]}


NEW = {"repository": "owner/other", "source": "x.py", "destination": "vendor/x.py"}


def test_pure_addition_is_pending():
    pending = sync.pending_enrollments(_registry(NEW), _registry())
    assert pending == [{"consumer": CHILD, **NEW}]
    stripped = sync._registry_without_pending(_registry(NEW), pending)
    assert stripped == _registry()


def test_no_base_change_has_no_pending():
    assert sync.pending_enrollments(_registry(), _registry()) == []


def test_modified_entry_is_not_pending():
    head = _registry(NEW)
    head["consumers"][0]["entries"][0]["destination"] = "vendor/renamed.py"
    assert sync.pending_enrollments(head, _registry()) == []


def test_removed_entry_is_not_pending():
    base = _registry(NEW)
    assert sync.pending_enrollments(_registry(), base) == []


def test_new_consumer_and_parent_entries_are_not_pending():
    base = _registry()
    head = copy.deepcopy(base)
    head["consumers"][1]["entries"].append(NEW)
    head["consumers"].append({"repository": "owner/new", "state": "locked",
                              "lock": "vendor.lock.json", "entries": [NEW]})
    assert sync.pending_enrollments(head, base) == []


def _run(monkeypatch, tmp_path, registry, **kwargs):
    seen = {}
    sha = "1" * 40
    head = {"sha": sha, "repository_id": 5}
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "vendor-child-ci.workflow.yml").write_text("name: stub\n")
    monkeypatch.setattr(sync, "monitored_repositories", lambda *a, **k: [CHILD])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *a, **k: dict(head))
    monkeypatch.setattr(sync, "validate_child_heads", lambda d: d)
    monkeypatch.setattr(sync, "verify_repository_id_identity", lambda **k: None)
    monkeypatch.setattr(sync, "_split_extra_heads", lambda *a, **k: ([], 0))
    monkeypatch.setattr(sync, "_copy_parent_vendor_baseline", lambda *a: tmp_path)
    monkeypatch.setattr(sync, "_offline_parent_vendor_check", lambda *a: {})

    def audit(audit_registry, *a, **k):
        seen["registry"] = audit_registry
        child = [c for c in audit_registry["consumers"] if c["repository"] == CHILD][0]
        ok = len(child["entries"]) == 1  # the child main lock has only tool.py
        failures = [] if ok else [{"repository": CHILD, "message": "consumer topology mismatch against parent registry"}]
        return [], {"failures": [], "missing": []}, {"checked": [], "failures": failures,
                                                     "failure_count": len(failures)}
    monkeypatch.setattr(sync, "_baseline_audit_blocked", audit)
    document = {"schema": sync.CHILD_HEADS_SCHEMA, "ref": "main", "heads": {CHILD: dict(head)}}
    return sync.run_parent_child_ci(registry, document, parent_root=tmp_path,
                                    skip_source_diff=True, **kwargs), seen


def test_pr_validation_with_base_reports_pending_and_passes(monkeypatch, tmp_path):
    report, _ = _run(monkeypatch, tmp_path, _registry(NEW), pr_validation=True,
                     base_registry=_registry())
    assert report["pending_enrollment_count"] == 1
    assert report["failure_count"] == 0


def test_pr_validation_without_base_stays_strict(monkeypatch, tmp_path):
    report, _ = _run(monkeypatch, tmp_path, _registry(NEW), pr_validation=True)
    assert report["pending_enrollment_count"] == 0
    assert report["failure_count"] == 1


def test_modified_entry_stays_strict_with_base(monkeypatch, tmp_path):
    head = _registry(NEW)
    head["consumers"][0]["entries"][0]["destination"] = "vendor/renamed.py"
    report, _ = _run(monkeypatch, tmp_path, head, pr_validation=True, base_registry=_registry(NEW))
    assert report["failure_count"] == 1


def test_base_registry_requires_pr_validation(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        _run(monkeypatch, tmp_path, _registry(NEW), base_registry=_registry())


def test_workflow_passes_base_registry_only_for_pull_requests():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/vendor-parent-child-ci.yml").read_text()
    block = text.split('if [[ "$GITHUB_EVENT_NAME" == "pull_request" ]]; then', 1)[1].split("fi\n", 1)[0]
    assert "--base-registry" in block
    assert 'git show "$PR_BASE_SHA:vendor-consumers.json"' in block
    assert text.count("--base-registry") == 1

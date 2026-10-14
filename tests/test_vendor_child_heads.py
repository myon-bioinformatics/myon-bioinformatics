import json
import subprocess
from pathlib import Path

import pytest

import vendor_sync as sync


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


def _init_repo(path, *, initial=b"base\n", branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.name", "Fixture")
    _git(path, "config", "user.email", "fixture@example.invalid")
    (path / "file.txt").write_bytes(initial)
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")
    return _git(path, "rev-parse", "HEAD")


def _head(sha, repository_id=42):
    return {"sha": sha, "repository_id": repository_id}


def _registry(consumer):
    return {
        "schema": "vendor-consumers/1",
        "consumers": [{
            "repository": consumer,
            "state": "locked",
            "lock": "vendor.lock.json",
            "entries": [{
                "repository": "owner/tool",
                "source": "tool.py",
                "destination": "vendor/tool.py",
            }],
        }],
    }


def _lock(commit, repo="owner/tool"):
    data = b"tool\n"
    return {
        "schema": sync.SCHEMA,
        "files": [{
            "repository": repo,
            "ref": "main",
            "commit": commit,
            "source": "tool.py",
            "destination": "vendor/tool.py",
            "blob_sha": sync.git_blob(data),
            "sha256": __import__("hashlib").sha256(data).hexdigest(),
        }],
    }


def test_verify_reference_identity_repo_id_mismatch_fails():
    with pytest.raises(ValueError, match="repository id mismatch"):
        sync.verify_reference_identity(
            expected_sha="a" * 40,
            expected_pull_number=7,
            expected_repository_id=111,
            resolved_sha="a" * 40,
            resolved_pull_number=7,
            resolved_repository_id=222,
        )


def test_verify_reference_identity_optional_created_at_never_fails():
    sync.verify_reference_identity(
        expected_sha="a" * 40,
        expected_pull_number=7,
        expected_repository_id=111,
        resolved_sha="a" * 40,
        resolved_pull_number=7,
        resolved_repository_id=111,
        optional_recorded={"pull_created_at": "2020-01-01T00:00:00Z"},
        optional_live={"pull_created_at": "2024-01-01T00:00:00Z"},
    )


def test_commit_merged_positive_and_negative(tmp_path):
    upstream = tmp_path / "upstream"
    main_sha = _init_repo(upstream)
    _git(upstream, "checkout", "-b", "feature")
    (upstream / "file.txt").write_bytes(b"side\n")
    _git(upstream, "commit", "-am", "feature")
    feature_sha = _git(upstream, "rev-parse", "HEAD")
    _git(upstream, "checkout", "main")
    roots = {"owner/tool": str(upstream)}
    sync.commit_merged_into_default("owner/tool", main_sha, local_roots=roots)
    with pytest.raises(ValueError, match="not merged"):
        sync.commit_merged_into_default("owner/tool", feature_sha, local_roots=roots)


def test_run_parent_child_ci_offline(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child_sha = _init_repo(child)
    consumer = "owner/child"
    registry = _registry(consumer)
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "vendor.lock.json").write_text(json.dumps(_lock(child_sha)) + "\n", encoding="utf-8")
    (parent / "vendor").mkdir()
    (parent / "vendor" / "tool.py").write_bytes(b"tool\n")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(child_sha, 99)},
    })
    record = _head(child_sha, 99)
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: record)
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: {"finding_count": 0, "findings": []})
    report = sync.run_parent_child_ci(registry, document, parent_root=parent)
    assert report["failure_count"] == 0
    assert report["parent_vendor_check"]["mode"] == "check"


def test_run_parent_child_ci_identity_repo_id_mismatch(tmp_path, monkeypatch):
    consumer = "owner/child"
    registry = _registry(consumer)
    parent = tmp_path / "parent"
    parent.mkdir()
    sha = "1" * 40
    (parent / "vendor.lock.json").write_text(json.dumps(_lock(sha)) + "\n", encoding="utf-8")
    (parent / "vendor").mkdir()
    (parent / "vendor" / "tool.py").write_bytes(b"tool\n")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(sha, 100)},
    })
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head(sha, 200))
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: {"finding_count": 0, "findings": []})
    report = sync.run_parent_child_ci(registry, document, parent_root=parent)
    assert report["identity_failure_count"] == 1
    assert report["failure_count"] == 1


def test_run_parent_child_ci_detects_sha_drift(tmp_path, monkeypatch):
    consumer = "owner/child"
    registry = _registry(consumer)
    parent = tmp_path / "parent"
    parent.mkdir()
    sha = "1" * 40
    (parent / "vendor.lock.json").write_text(json.dumps(_lock(sha)) + "\n", encoding="utf-8")
    (parent / "vendor").mkdir()
    (parent / "vendor" / "tool.py").write_bytes(b"tool\n")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(sha, 5)},
    })
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head("2" * 40, 5))
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: {"finding_count": 0, "findings": []})
    report = sync.run_parent_child_ci(registry, document, parent_root=parent)
    assert report["drift_count"] == 1
    assert report["needs_heads_refresh"] is True


def test_source_diff_detects_unmerged_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feature")
    (repo / "file.txt").write_bytes(b"feature\n")
    _git(repo, "commit", "-am", "feature")
    findings = sync.branches_differing_from_main("owner/repo", local_roots={"owner/repo": str(repo)})
    assert findings and findings[0]["branch"] == "feature"


def test_refresh_child_heads_writes_repository_id(tmp_path, monkeypatch):
    registry = _registry("owner/child")
    heads_path = tmp_path / "child-heads.json"
    monkeypatch.setattr(
        sync,
        "fetch_child_head_record",
        lambda repository, ref="main", **kwargs: _head("3" * 40, 4242),
    )
    sync.refresh_child_heads(registry, heads_path)
    document = json.loads(heads_path.read_text(encoding="utf-8"))
    assert document["heads"]["owner/child"]["repository_id"] == 4242


def test_child_head_drift_fails(tmp_path, monkeypatch):
    consumer = "owner/child"
    registry = _registry(consumer)
    recorded_sha = "1" * 40
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(recorded_sha, 1)},
    })
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head("2" * 40, 1))
    monkeypatch.setattr(
        sync,
        "audit_registry_merged_pins",
        lambda *args, **kwargs: {"failure_count": 0, "failures": []},
    )
    monkeypatch.setattr(
        sync,
        "_verify_parent_pr_pins",
        lambda *args, **kwargs: {"failure_count": 0, "failures": []},
    )
    report = sync.check_child_heads(registry, document)
    assert report["drift_count"] == 1
    assert report["failure_count"] == 1


def test_resolve_parent_pr_merged_and_open():
    def fetch(repo, number, token=None):
        if number == 1:
            return {"merged_at": "2020-01-01", "merge_commit_sha": "a" * 40, "head": {"sha": "b" * 40}}
        return {"merged_at": None, "merge_commit_sha": None, "head": {"sha": "c" * 40}}

    def repo_fetch(repository):
        return {"id": 9001, "created_at": "2019-01-01T00:00:00Z"}

    merged = sync.resolve_parent_pr("owner/parent", 1, pr_fetch=fetch, repo_fetch=repo_fetch)
    assert merged["state"] == "merged"
    assert merged["commit"] == "a" * 40
    assert merged["repository_id"] == 9001
    open_pr = sync.resolve_parent_pr("owner/parent", 2, pr_fetch=fetch, repo_fetch=repo_fetch)
    assert open_pr["state"] == "open"
    assert open_pr["commit"] == "c" * 40


def test_resolve_parent_pr_rejects_repository_id_mismatch():
    def fetch(repo, number, token=None):
        return {"merged_at": "2020-01-01", "merge_commit_sha": "a" * 40, "head": {"sha": "b" * 40}}

    def repo_fetch(repository):
        return {"id": 2}

    reference = sync.validate_parent_pr_ref({
        "schema": sync.PARENT_PR_REF_SCHEMA,
        "repository": "owner/parent",
        "pull_number": 1,
        "repository_id": 1,
        "commit": "a" * 40,
    })
    with pytest.raises(ValueError, match="repository id mismatch"):
        sync.resolve_parent_pr("owner/parent", 1, pr_fetch=fetch, repo_fetch=repo_fetch, reference=reference)


def test_audit_consumer_lock_pin_at_recorded_head(tmp_path):
    upstream = tmp_path / "upstream"
    main_sha = _init_repo(upstream)
    report = sync.audit_consumer_merged_pins(
        "owner/consumer",
        [("vendor.lock.json", _lock(main_sha))],
        {},
        local_roots={"owner/tool": str(upstream)},
    )
    assert report["pins_checked"] == 1
    assert not report["failures"]


def test_workflow_parent_pr_pin_resolves(tmp_path, monkeypatch):
    consumer = "owner/consumer"
    registry = _registry(consumer)
    revisions = {consumer: "1" * 40}

    def pr_fetch(repo, number, token=None):
        assert number == 7
        return {"merged_at": "2020-01-01", "merge_commit_sha": "d" * 40, "head": {"sha": "e" * 40}}

    monkeypatch.setattr(sync, "fetch_repository_record", lambda repository, token=None, **kwargs: {"id": 77})
    monkeypatch.setattr(
        sync,
        "commit_merged_into_default",
        lambda repository, commit, **kwargs: True,
    )
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text(
        "jobs:\n  build:\n    steps:\n"
        f"      - uses: {sync.PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@pr/7\n",
        encoding="utf-8",
    )
    report = sync._verify_parent_pr_pins(
        registry,
        revisions,
        parent_repository=sync.PARENT_REPOSITORY,
        local_consumers={consumer: str(tmp_path)},
        pr_fetch=pr_fetch,
        repo_fetch=lambda repository: {"id": 77},
    )
    assert report["pins_checked"] == 1
    assert report["failure_count"] == 0


def test_cli_refresh_child_heads(tmp_path, monkeypatch, capsys):
    registry = _registry("owner/child")
    (tmp_path / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head("3" * 40, 9))
    heads = tmp_path / "heads.json"
    assert sync.main([
        "refresh-child-heads",
        "--registry", str(tmp_path / "registry.json"),
        "--heads", str(heads),
    ]) == 0
    assert json.loads(heads.read_text())["heads"]["owner/child"]["sha"] == "3" * 40

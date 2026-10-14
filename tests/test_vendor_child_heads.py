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


def test_refresh_and_check_child_heads_offline(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child_sha = _init_repo(child)
    consumer = "owner/child"
    registry = _registry(consumer)
    heads_path = tmp_path / "child-heads.json"
    monkeypatch.setattr(sync, "fetch_child_head", lambda repository, ref="main", **kwargs: child_sha)
    sync.refresh_child_heads(registry, heads_path)
    document = json.loads(heads_path.read_text(encoding="utf-8"))
    assert document["heads"][consumer] == child_sha
    report = sync.check_child_heads(registry, document, local_consumers={consumer: str(child)})
    assert report["failure_count"] == 0


def test_child_head_drift_fails(tmp_path, monkeypatch):
    child = tmp_path / "child"
    recorded = _init_repo(child)
    consumer = "owner/child"
    registry = _registry(consumer)
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: recorded},
    })
    live = "2" * 40
    monkeypatch.setattr(sync, "fetch_child_head", lambda *args, **kwargs: live)
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

    merged = sync.resolve_parent_pr("owner/parent", 1, pr_fetch=fetch)
    assert merged["state"] == "merged"
    assert merged["commit"] == "a" * 40
    open_pr = sync.resolve_parent_pr("owner/parent", 2, pr_fetch=fetch)
    assert open_pr["state"] == "open"
    assert open_pr["commit"] == "c" * 40


def test_audit_consumer_lock_pin_at_recorded_head(tmp_path):
    upstream = tmp_path / "upstream"
    main_sha = _init_repo(upstream)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "vendor.lock.json").write_text(json.dumps(_lock(main_sha)) + "\n", encoding="utf-8")
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
    workflow = {
        ".github/workflows/ci.yml": (
            "jobs:\n"
            "  build:\n"
            "    steps:\n"
            f"      - uses: {sync.PARENT_REPOSITORY}/.github/workflows/reusable-actionlint.yml@pr/7\n"
        ),
    }
    registry = _registry(consumer)
    revisions = {consumer: "1" * 40}

    def pr_fetch(repo, number, token=None):
        assert number == 7
        return {"merged_at": "2020-01-01", "merge_commit_sha": "d" * 40, "head": {"sha": "e" * 40}}

    monkeypatch.setattr(
        sync,
        "commit_merged_into_default",
        lambda repository, commit, **kwargs: True,
    )
    report = sync._verify_parent_pr_pins(
        registry,
        revisions,
        parent_repository=sync.PARENT_REPOSITORY,
        local_consumers={consumer: str(tmp_path)},
        pr_fetch=pr_fetch,
    )
    assert report["failure_count"] == 0
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text(workflow[".github/workflows/ci.yml"], encoding="utf-8")
    report = sync._verify_parent_pr_pins(
        registry,
        revisions,
        parent_repository=sync.PARENT_REPOSITORY,
        local_consumers={consumer: str(tmp_path)},
        pr_fetch=pr_fetch,
    )
    assert report["pins_checked"] == 1


def test_cli_refresh_child_heads(tmp_path, monkeypatch, capsys):
    registry = _registry("owner/child")
    (tmp_path / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(sync, "fetch_child_head", lambda *args, **kwargs: "3" * 40)
    heads = tmp_path / "heads.json"
    assert sync.main([
        "refresh-child-heads",
        "--registry", str(tmp_path / "registry.json"),
        "--heads", str(heads),
    ]) == 0
    assert json.loads(heads.read_text())["heads"]["owner/child"] == "3" * 40

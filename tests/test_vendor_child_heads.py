import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError

import pytest

import vendor_sync as sync


@pytest.fixture(autouse=True)
def isolate_source_audit(monkeypatch):
    # This module tests inventory/drift orchestration. Source and child Git
    # evidence, including its integration into failure_count, has its own suite.
    monkeypatch.setattr(sync, "audit_adopted_child_files", lambda *a, **k:
                        {"checked": [], "failures": [], "failure_count": 0})


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


def _source_diff_empty():
    return {
        "schema": "source-diff-report/1",
        "findings": [],
        "finding_count": 0,
        "scan_errors": [],
        "scan_error_count": 0,
    }


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
    template_dir = parent / "templates"
    template_dir.mkdir()
    template_dir.joinpath("vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(child_sha, 99)},
    })
    record = _head(child_sha, 99)
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: record)
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: _source_diff_empty())
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
    (parent / "templates").mkdir()
    (parent / "templates" / "vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(sha, 100)},
    })
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head(sha, 200))
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: _source_diff_empty())
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
    template_dir = parent / "templates"
    template_dir.mkdir()
    template_dir.joinpath("vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head("2" * 40, 5))
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: _source_diff_empty())
    report = sync.run_parent_child_ci(registry, document, parent_root=parent)
    assert report["drift_count"] == 1
    assert report["needs_heads_refresh"] is True
    assert report["failure_count"] == 1


def test_run_parent_child_ci_pr_validation_sha_drift_report_only(tmp_path, monkeypatch):
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
    template_dir = parent / "templates"
    template_dir.mkdir()
    template_dir.joinpath("vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head("2" * 40, 5))
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: _source_diff_empty())
    report = sync.run_parent_child_ci(registry, document, parent_root=parent, pr_validation=True)
    assert report["drift_count"] == 1
    assert report["identity_failure_count"] == 0
    assert report["failure_count"] == 0
    assert report["pr_validation"] is True


def test_commit_merged_into_default_old_main_commit_via_remote(tmp_path):
    upstream = tmp_path / "upstream"
    first_sha = _init_repo(upstream, initial=b"v1\n")
    (upstream / "file.txt").write_bytes(b"v2\n")
    _git(upstream, "commit", "-am", "second")
    remotes = {"owner/tool": "file://" + str(upstream)}
    sync.commit_merged_into_default("owner/tool", first_sha, remotes=remotes)


def test_run_parent_child_ci_audit_failures_fail_in_pr_validation(tmp_path, monkeypatch):
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
    template_dir = parent / "templates"
    template_dir.mkdir()
    template_dir.joinpath("vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head(sha, 5))
    monkeypatch.setattr(
        sync,
        "audit_child_workflow_templates",
        lambda *args, **kwargs: {
            "failures": [{"repository": consumer, "message": "template mismatch"}],
            "missing": [],
        },
    )
    monkeypatch.setattr(
        sync,
        "audit_adopted_child_files",
        lambda *args, **kwargs: {
            "checked": [],
            "failures": [{"repository": consumer, "message": "blob mismatch"}],
            "failure_count": 1,
        },
    )
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: _source_diff_empty())
    report = sync.run_parent_child_ci(registry, document, parent_root=parent, pr_validation=True)
    assert report["audit_failure_count"] == 2
    assert report["failure_count"] == 2


def test_commit_merged_into_default_old_pin_via_github_compare(monkeypatch):
    old_sha = "a" * 40
    calls = []

    def fake_json(url, token=None):
        calls.append(url)
        if url.endswith("/repos/owner/tool"):
            return {"default_branch": "main"}
        if "/compare/" in url:
            return {"merge_base_commit": {"sha": old_sha}}
        raise AssertionError(url)

    monkeypatch.setattr(sync, "_github_json", fake_json)
    sync.commit_merged_into_default("owner/tool", old_sha, token="fixture-token")
    assert any("/compare/" in url for url in calls)


def test_record_is_public_source_pin_allows_archived():
    archived = {
        "full_name": "owner/archived-tool",
        "private": False,
        "visibility": "public",
        "archived": True,
    }
    assert sync.record_is_public_source_pin(archived)
    assert not sync.record_is_public_monitored(archived)


def test_source_diff_only_scans_monitored_public_repos(monkeypatch):
    registry = _registry("myon-bioinformatics/secret-consumer")
    monitored = ["myon-bioinformatics/public-one"]
    scanned = []

    def fake_branches(name, **kwargs):
        scanned.append(name)
        return [{"repository": name, "branch": "feature", "tip": "f" * 40}]

    monkeypatch.setattr(sync, "branches_differing_from_main", fake_branches)
    report = sync.source_diff_report(registry, repository_names=monitored)
    assert scanned == monitored
    payload = json.dumps(report)
    assert "secret" not in payload.casefold()
    assert report["findings"][0]["repository"] == monitored[0]


def test_audit_templates_only_monitored_public_repos(monkeypatch):
    registry = _registry("myon-bioinformatics/secret-consumer")
    monitored = ["myon-bioinformatics/public-one"]
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {monitored[0]: _head("a" * 40, 1)},
    })
    fetched = []

    def fake_fetch(repository, path, ref, token=None):
        fetched.append(repository)
        return None

    monkeypatch.setattr(sync, "_fetch_repo_text", fake_fetch)
    report = sync.audit_child_workflow_templates(
        registry, document, "name: stub\n", repository_names=monitored, token="token",
    )
    assert fetched == [monitored[0]]
    assert "secret" not in json.dumps(report).casefold()


def test_open_or_update_autofix_pr_resets_branch_to_main(monkeypatch):
    calls = []

    def fake_api(method, url, token=None, body=None):
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/repos/owner/parent"):
            return {"default_branch": "main"}
        if method == "GET" and url.endswith("/repos/owner/parent/git/ref/heads/main"):
            return {"object": {"sha": "m" * 40}}
        if method == "GET" and "pulls?state=open" in url:
            return []
        if method == "GET" and "/git/ref/heads/vendor" in url:
            return {"object": {"sha": "o" * 40}}
        if method == "PATCH" and "/git/refs/heads/vendor" in url:
            return {}
        if method == "GET" and "/contents/child-heads.json" in url:
            raise HTTPError(url, 404, "not found", None, None)
        if method == "PUT" and "/contents/" in url:
            return {}
        if method == "POST" and url.endswith("/pulls"):
            return {"number": 9, "html_url": "https://example.invalid/9"}
        raise AssertionError((method, url))

    monkeypatch.setattr(sync, "_github_api", fake_api)
    sync._open_or_update_autofix_pr(
        "owner/parent",
        "vendor/child-heads-sync",
        "title",
        "body",
        {"child-heads.json": b"{}\n"},
        "token",
    )
    patch = next(
        (body for method, url, body in calls if method == "PATCH" and "/git/refs/heads/vendor" in url),
        None,
    )
    assert patch == {"sha": "m" * 40, "force": True}


def test_dispatch_parent_workflow_posts_dispatch(monkeypatch):
    posted = []

    def fake_api(method, url, token=None, body=None):
        posted.append((method, url, body))
        return None

    monkeypatch.setattr(sync, "_github_api", fake_api)
    sync._dispatch_parent_workflow(
        "owner/parent",
        ".github/workflows/vendor-parent-child-ci.yml",
        "vendor/child-heads-sync",
        "token",
    )
    assert posted[0][0] == "POST"
    assert "actions/workflows" in posted[0][1]
    assert posted[0][2] == {"ref": "vendor/child-heads-sync"}


def test_run_parent_child_ci_autofix_propagates_dispatch_failure(tmp_path, monkeypatch):
    consumer = "myon-bioinformatics/public-one"
    registry = _registry(consumer)
    parent = tmp_path / "parent"
    parent.mkdir()
    sha = "1" * 40
    (parent / "vendor.lock.json").write_text(json.dumps(_lock(sha)) + "\n", encoding="utf-8")
    (parent / "vendor").mkdir()
    (parent / "vendor" / "tool.py").write_bytes(b"tool\n")
    (parent / "templates").mkdir()
    (parent / "templates" / "vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(sha, 1)},
    })
    monkeypatch.setattr(sync, "plan_heads_refresh", lambda *a, **k: {"heads": document["heads"], "skipped": []})
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head("2" * 40, 1))
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    monkeypatch.setattr(sync, "source_diff_report", lambda *args, **kwargs: _source_diff_empty())
    monkeypatch.setattr(
        sync,
        "_open_or_update_autofix_pr",
        lambda *args, **kwargs: {"pull_number": 1, "url": "https://example.invalid/1", "branch": "vendor/child-heads-sync"},
    )

    def boom(*args, **kwargs):
        raise HTTPError("https://api.github.com/dispatch", 403, "forbidden", None, None)

    monkeypatch.setattr(sync, "_dispatch_parent_workflow", boom)
    with pytest.raises(HTTPError):
        sync.run_parent_child_ci(registry, document, parent_root=parent, autofix=True, token="token")


def test_run_parent_child_ci_source_diff_uses_monitored_list(tmp_path, monkeypatch):
    consumer = "myon-bioinformatics/public-one"
    registry = _registry("myon-bioinformatics/secret-consumer")
    parent = tmp_path / "parent"
    parent.mkdir()
    sha = "1" * 40
    (parent / "vendor.lock.json").write_text(json.dumps(_lock(sha)) + "\n", encoding="utf-8")
    (parent / "vendor").mkdir()
    (parent / "vendor" / "tool.py").write_bytes(b"tool\n")
    (parent / "templates").mkdir()
    (parent / "templates" / "vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(sha, 1)},
    })
    monitored = [consumer]
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: monitored)
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head(sha, 1))
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    seen = {}

    def capture(registry, **kwargs):
        seen["repository_names"] = kwargs.get("repository_names")
        return _source_diff_empty()

    monkeypatch.setattr(sync, "source_diff_report", capture)
    sync.run_parent_child_ci(registry, document, parent_root=parent)
    assert seen["repository_names"] == monitored
    assert "secret" not in json.dumps(seen)


def test_source_diff_detects_unmerged_branch(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feature")
    (repo / "file.txt").write_bytes(b"feature\n")
    _git(repo, "commit", "-am", "feature")
    findings = sync.branches_differing_from_main("owner/repo", local_roots={"owner/repo": str(repo)})
    assert findings and findings[0]["branch"] == "feature"


def test_refresh_child_heads_writes_repository_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "monitored_repositories", lambda *a, **k: ["owner/child"])
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
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    report = sync.check_child_heads(registry, document)
    assert report["drift_count"] == 1
    assert report["failure_count"] == 1


def test_resolve_parent_pr_merged_and_open():
    def fetch(repo, number, token=None):
        if number == 1:
            return {"number": number, "state": "closed", "base": {"repo": {"id": 9001, "full_name": repo}}, "merged_at": "2020-01-01", "merge_commit_sha": "a" * 40, "head": {"sha": "b" * 40}}
        return {"number": number, "state": "open", "base": {"repo": {"id": 9001, "full_name": repo}}, "merged_at": None, "merge_commit_sha": None, "head": {"sha": "c" * 40, "repo": {"id": 9001, "full_name": repo}}}

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
        return {"number": number, "state": "closed", "base": {"repo": {"id": 2, "full_name": repo}}, "merged_at": "2020-01-01", "merge_commit_sha": "a" * 40, "head": {"sha": "b" * 40}}

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


def test_verify_workflow_template_copy_rejects_pr_pins():
    text = (
        "name: Vendor child CI\n"
        f"uses: {sync.PARENT_REPOSITORY}/.github/workflows/x.yml@pr/7\n"
    )
    with pytest.raises(ValueError, match="@pr/"):
        sync.verify_workflow_template_copy(text, text)


def test_source_diff_does_not_fail_parent_ci_count(tmp_path, monkeypatch):
    consumer = "owner/child"
    registry = _registry(consumer)
    parent = tmp_path / "parent"
    parent.mkdir()
    sha = "1" * 40
    (parent / "vendor.lock.json").write_text(json.dumps(_lock(sha)) + "\n", encoding="utf-8")
    (parent / "vendor").mkdir()
    (parent / "vendor" / "tool.py").write_bytes(b"tool\n")
    (parent / "templates").mkdir()
    (parent / "templates" / "vendor-child-ci.workflow.yml").write_text("name: stub\n", encoding="utf-8")
    document = sync.validate_child_heads({
        "schema": sync.CHILD_HEADS_SCHEMA,
        "ref": "main",
        "heads": {consumer: _head(sha, 5)},
    })
    monkeypatch.setattr(sync, "monitored_repositories", lambda *args, **kwargs: [consumer])
    monkeypatch.setattr(sync, "fetch_child_head_record", lambda *args, **kwargs: _head(sha, 5))
    monkeypatch.setattr(sync, "audit_child_workflow_templates", lambda *args, **kwargs: {"failures": [], "missing": []})
    monkeypatch.setattr(
        sync,
        "source_diff_report",
        lambda *args, **kwargs: {
            "schema": "source-diff-report/1",
            "findings": [{"repository": consumer, "branch": "feature", "tip": "f" * 40}],
            "finding_count": 1,
            "scan_errors": [],
            "scan_error_count": 0,
        },
    )
    report = sync.run_parent_child_ci(registry, document, parent_root=parent)
    assert report["source_diff"]["finding_count"] == 1
    assert report["failure_count"] == 0


def test_record_is_public_monitored_filters():
    public = {
        "full_name": "myon-bioinformatics/demo",
        "archived": False,
        "private": False,
        "visibility": "public",
    }
    assert sync.record_is_public_monitored(public)
    archived = dict(public, archived=True)
    assert not sync.record_is_public_monitored(archived)
    private = dict(public, private=True, visibility="private")
    assert not sync.record_is_public_monitored(private)
    internal = dict(public, private=True, visibility="internal")
    assert not sync.record_is_public_monitored(internal)


def test_list_org_repositories_falls_back_to_users_endpoint(monkeypatch):
    calls = []

    def fake_api(method, url, token=None, body=None):
        calls.append(url)
        if "/orgs/" in url:
            raise HTTPError(url, 404, "not found", None, None)
        return [{
            "full_name": "myon-bioinformatics/demo",
            "archived": False,
            "private": False,
            "visibility": "public",
        }]

    monkeypatch.setattr(sync, "_github_api", fake_api)
    repos = sync.list_org_repositories("myon-bioinformatics", token="token")
    assert repos == ["myon-bioinformatics/demo"]
    assert any("/users/" in url for url in calls)


def test_list_org_repositories_omits_private_and_archived(monkeypatch):
    def fake_api(method, url, token=None, body=None):
        return [
            {
                "full_name": "myon-bioinformatics/public-one",
                "archived": False,
                "private": False,
                "visibility": "public",
            },
            {
                "full_name": "myon-bioinformatics/hidden",
                "archived": False,
                "private": True,
                "visibility": "private",
            },
            {
                "full_name": "myon-bioinformatics/old",
                "archived": True,
                "private": False,
                "visibility": "public",
            },
            {
                "full_name": "myon-bioinformatics/enterprise",
                "archived": False,
                "private": True,
                "visibility": "internal",
            },
        ]

    monkeypatch.setattr(sync, "_github_api", fake_api)
    repos = sync.list_org_repositories("myon-bioinformatics", token="token")
    assert repos == ["myon-bioinformatics/public-one"]


def test_split_extra_heads_omits_private_names(monkeypatch):
    monitored = ["myon-bioinformatics/public-one"]
    heads = {
        "myon-bioinformatics/public-one": _head("a" * 40, 1),
        "myon-bioinformatics/secret": _head("b" * 40, 2),
    }

    def repo_fetch(repository):
        if repository.endswith("secret"):
            return {
                "id": 2,
                "full_name": repository,
                "archived": False,
                "private": True,
                "visibility": "private",
            }
        raise AssertionError("unexpected fetch")

    unexpected, excluded = sync._split_extra_heads(
        heads, monitored, token="token", repo_fetch=repo_fetch,
    )
    assert unexpected == []
    assert excluded == 1
    assert "secret" not in str(unexpected)


def test_cli_refresh_child_heads(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sync, "monitored_repositories", lambda *a, **k: ["owner/child"])
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


def test_missing_monitored_repository_fails_parent_ci(tmp_path, monkeypatch):
    registry = _registry('owner/child')
    (tmp_path / 'vendor.lock.json').write_text(json.dumps(_lock('a' * 40)))
    (tmp_path / 'vendor').mkdir()
    (tmp_path / 'vendor/tool.py').write_bytes(b'tool\n')
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text('name: stub\n')
    document = {'schema': sync.CHILD_HEADS_SCHEMA, 'ref': 'main',
                'heads': {'owner/child': _head('a' * 40)}}
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: ['owner/child', 'owner/new'])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: _head('a' * 40))
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    report = sync.run_parent_child_ci(registry, document, parent_root=tmp_path, skip_source_diff=True)
    assert report['missing_heads'] == ['owner/new']
    assert report['failure_count'] == 1


def test_unauthenticated_inventory_still_filters_private_and_archived(monkeypatch):
    monkeypatch.delenv('GH_TOKEN', raising=False)
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    rows = [{'full_name': 'owner/public', 'archived': False, 'private': False, 'visibility': 'public'},
            {'full_name': 'owner/secret', 'archived': False, 'private': True, 'visibility': 'private'},
            {'full_name': 'owner/old', 'archived': True, 'private': False, 'visibility': 'public'}]
    monkeypatch.setattr(sync, '_github_json', lambda *a, **k: rows)
    assert sync.monitored_repositories(_registry('owner/secret'), owner='owner') == ['owner/public']
    monkeypatch.setattr(sync, '_github_json', lambda *a, **k: {'message': 'error'})
    with pytest.raises(ValueError, match='invalid account'):
        sync.monitored_repositories(_registry('owner/secret'), owner='owner')


@pytest.mark.parametrize('field,value', [('number', 9), ('state', 'closed')])
def test_resolver_rejects_wrong_pr_response(field, value):
    repo = {'id': 2, 'full_name': 'owner/parent'}
    pull = {'number': 1, 'state': 'open', 'base': {'repo': repo},
            'head': {'repo': repo, 'sha': 'a' * 40}}
    pull[field] = value
    with pytest.raises(ValueError):
        sync.resolve_parent_pr('owner/parent', 1, pr_fetch=lambda *a, **k: pull,
                               repo_fetch=lambda *a: repo)

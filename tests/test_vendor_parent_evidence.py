"""Parent evidence must remain independent of a child's editable claims."""
import copy
import hashlib
import json

import pytest

import vendor_sync as sync

NAME = 'myon-bioinformatics/child'
SHA = 'a' * 40
NEW = 'b' * 40
DATA = b'upstream bytes\n'
TEMPLATE = b'name: stub\n'


def fixture(monkeypatch):
    registry = {'schema': 'vendor-consumers/1', 'consumers': [{
        'repository': NAME, 'state': 'locked', 'lock': 'vendor.lock.json',
        'entries': [{'repository': 'owner/source', 'source': 'tool.py',
                     'destination': 'vendor/tool.py'}]}]}
    lock = {'schema': sync.SCHEMA, 'files': [{
        'repository': 'owner/source', 'ref': SHA, 'commit': SHA,
        'source': 'tool.py', 'destination': 'vendor/tool.py',
        'blob_sha': sync.git_blob(DATA), 'sha256': hashlib.sha256(DATA).hexdigest()}]}
    heads = {'schema': sync.CHILD_HEADS_SCHEMA, 'ref': 'main',
             'heads': {NAME: {'sha': SHA, 'repository_id': 42}}}
    monkeypatch.setattr(sync, 'fetch_repository_record', lambda *a, **k: {
        'id': 42, 'full_name': NAME, 'private': False, 'visibility': 'public', 'archived': False})
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda *a, **k: json.dumps(lock))
    return registry, lock, heads


@pytest.mark.parametrize('fault', [None, 'child', 'both', 'source_sha', 'missing_source', 'id'])
def test_independent_source_evidence(monkeypatch, fault):
    registry, lock, heads = fixture(monkeypatch)
    altered = b'changed child bytes\n'
    if fault == 'both':
        lock['files'][0].update(blob_sha=sync.git_blob(altered), sha256=hashlib.sha256(altered).hexdigest())
    if fault == 'id':
        heads['heads'][NAME]['repository_id'] = 999
    calls = []

    def snapshot(repository, revision, paths):
        calls.append(repository)
        if repository == 'owner/source' and fault == 'missing_source':
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR)
        data = altered if repository == NAME and fault in ('child', 'both') else DATA
        return (NEW if fault == 'source_sha' else revision), {
            path: (sync.git_blob(data), data) for path in paths}

    monkeypatch.setattr(sync, '_public_git_snapshot', snapshot)
    report = sync.audit_adopted_child_files(registry, heads, [NAME])
    assert report['failure_count'] == (0 if fault is None else 1)
    assert bool(report['checked']) == (fault is None)
    if fault in ('both', 'source_sha', 'missing_source', 'id'):
        assert NAME not in calls  # reject unproven claims before reading child bytes


def test_private_consumer_is_not_fetched(monkeypatch):
    registry, _, heads = fixture(monkeypatch)
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda *a, **k: pytest.fail('private read'))
    report = sync.audit_adopted_child_files(registry, heads, [])
    assert report == {'checked': [], 'failures': [], 'failure_count': 0}


def test_private_source_repo_name_not_in_audit_failure(monkeypatch):
    registry, lock, heads = fixture(monkeypatch)
    private_source = 'owner/secret-source'

    def public_only(repository, token=None, repo_fetch=None):
        if repository.casefold() == private_source.casefold():
            return {
                'id': 9,
                'full_name': private_source,
                'private': True,
                'visibility': 'private',
                'archived': False,
            }
        return {
            'id': 42,
            'full_name': repository,
            'private': False,
            'visibility': 'public',
            'archived': False,
        }

    lock['files'][0]['repository'] = private_source
    registry['consumers'][0]['entries'][0]['repository'] = private_source
    monkeypatch.setattr(sync, 'fetch_repository_record', public_only)
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda *a, **k: json.dumps(lock))
    report = sync.audit_adopted_child_files(registry, heads, [NAME])
    assert report['failure_count'] == 1
    payload = json.dumps(report)
    assert 'secret' not in payload.casefold()
    assert 'source repository is not public' in payload


@pytest.mark.parametrize('fault,expect_skip', [
    (None, False),
    ('lock', False),
    ('deleted_workflow', True),
    ('modified_workflow', True),
    ('missing_lock', False),
    ('id', True),
    ('new_repo', True),
])
def test_plan_heads_refresh_skips_only_review_items(monkeypatch, fault, expect_skip):
    registry, lock, heads = fixture(monkeypatch)
    new_heads = {NAME: {'sha': NEW, 'repository_id': 42}}
    before_wf = 'jobs:\n  check:\n    steps: []\n'
    after_wf = before_wf
    if fault == 'deleted_workflow':
        after_wf = None
    if fault == 'modified_workflow':
        after_wf = 'jobs:\n  custom:\n    steps: []\n'
    if fault == 'id':
        new_heads[NAME]['repository_id'] = 9
    if fault == 'new_repo':
        new_heads[NAME + '-new'] = new_heads[NAME]

    def fetch_text(name, path, revision, token=None):
        if path == sync.CHILD_CI_WORKFLOW_PATH:
            return before_wf if revision == SHA else after_wf
        if revision == NEW and fault == 'missing_lock':
            return None
        return json.dumps(lock)

    monkeypatch.setattr(sync, '_fetch_repo_text', fetch_text)
    plan = sync.plan_heads_refresh(registry, heads, new_heads, TEMPLATE.decode(), token='token')
    if fault == 'new_repo':
        assert any(row['reason'] == 'new_monitored_repository' for row in plan['skipped'])
    if expect_skip and fault not in ('new_repo',):
        assert plan['heads'][NAME]['sha'] == SHA
        assert plan['skipped']
    elif fault == 'lock':
        assert plan['heads'][NAME]['sha'] == NEW
    elif fault is None:
        assert plan['heads'][NAME]['sha'] == NEW


def test_parent_ci_counts_source_failure_and_does_not_write(tmp_path, monkeypatch):
    registry, _, heads = fixture(monkeypatch)
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text('template')
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: heads['heads'][NAME])
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda *a: (_ for _ in ()).throw(ValueError(sync.PUBLIC_GIT_FETCH_ERROR)))
    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', lambda *a, **k: pytest.fail('write'))
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path, skip_source_diff=True)
    assert report['failure_count'] == 1
    assert report['adopted_child_files']['failure_count'] == 1
    assert sync.PUBLIC_GIT_FETCH_ERROR in json.dumps(report['adopted_child_files']['failures'])


def test_autofix_continues_when_one_repo_skipped(tmp_path, monkeypatch):
    registry, lock, heads = fixture(monkeypatch)
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text(TEMPLATE.decode())
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: {'sha': NEW, 'repository_id': 42})
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda repo, sha, paths:
                        (sha, {p: (sync.git_blob(DATA), DATA) for p in paths}))
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda name, path, sha, token:
                        None if path == sync.CHILD_CI_WORKFLOW_PATH and sha == NEW else json.dumps(lock))
    writes = []
    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', lambda *a, **k: writes.append(1) or {
        'pull_number': 1, 'url': 'https://example.invalid/1', 'branch': 'vendor/child-heads-sync'})
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: None)
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                      skip_source_diff=True, autofix=True, token='fixture')
    assert writes
    assert report['heads_refresh_skipped']


@pytest.mark.parametrize('fault', [None, 'file', 'both'])
def test_parent_candidate_ref_keeps_independent_source_check(tmp_path, monkeypatch, fault):
    registry, lock, heads = fixture(monkeypatch)
    lock['files'][0]['ref'] = 'main'  # parent's existing candidate-update contract
    data = DATA if fault is None else b'edited\n'
    if fault == 'both':
        lock['files'][0].update(blob_sha=sync.git_blob(data), sha256=hashlib.sha256(data).hexdigest())
    (tmp_path / 'vendor').mkdir()
    (tmp_path / 'vendor/tool.py').write_bytes(data)
    (tmp_path / 'vendor.lock.json').write_text(json.dumps(lock))
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda repo, sha, paths:
                        (sha, {p: (sync.git_blob(DATA), DATA) for p in paths}))
    report = sync.audit_adopted_child_files(registry, heads, [NAME],
                                            parent_root=tmp_path, parent_repository=NAME)
    assert report['failure_count'] == (0 if fault is None else 1)

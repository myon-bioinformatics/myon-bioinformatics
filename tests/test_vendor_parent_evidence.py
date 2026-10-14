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
            raise ValueError('source unavailable')
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


@pytest.mark.parametrize('fault', [None, 'lock', 'deleted_workflow', 'modified_workflow', 'missing_lock', 'id', 'new_repo', 'parent_ref'])
def test_refresh_cannot_adopt_contract_changes(monkeypatch, fault):
    registry, lock, heads = fixture(monkeypatch)
    new_heads = {NAME: {'sha': NEW, 'repository_id': 42}}
    before = ([('vendor.lock.json', lock)], {'ci.yml': 'required check'})
    after = copy.deepcopy(before)
    if fault == 'lock': after[0][0][1]['files'][0]['commit'] = NEW
    if fault == 'deleted_workflow': after[1].clear()
    if fault == 'modified_workflow': after[1]['ci.yml'] = 'exit 0'
    if fault == 'id': new_heads[NAME]['repository_id'] = 9
    if fault == 'new_repo': new_heads[NAME + '-new'] = new_heads[NAME]

    def fetch(name, path, revision, token):
        if revision == NEW and fault == 'missing_lock':
            raise ValueError('missing consumer lock')
        return before if revision == SHA else after

    monkeypatch.setattr(sync, '_fetch_consumer_documents', fetch)
    if fault == 'parent_ref':
        monkeypatch.setattr(sync, '_fetch_repo_text', lambda name, path, revision, token: revision)
    if fault:
        with pytest.raises(ValueError):
            sync.verify_heads_refresh_preserves_contract(registry, heads, new_heads)
    else:
        sync.verify_heads_refresh_preserves_contract(registry, heads, new_heads)


def test_parent_ci_counts_source_failure_and_does_not_write(tmp_path, monkeypatch):
    registry, _, heads = fixture(monkeypatch)
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text('template')
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: heads['heads'][NAME])
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda *a: (_ for _ in ()).throw(ValueError('missing source')))
    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', lambda *a, **k: pytest.fail('write'))
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path, skip_source_diff=True)
    assert report['failure_count'] == 1
    assert report['adopted_child_files']['failure_count'] == 1


def test_autofix_contract_rejection_precedes_any_write(tmp_path, monkeypatch):
    registry, lock, heads = fixture(monkeypatch)
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text('template')
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: {'sha': NEW, 'repository_id': 42})
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda repo, sha, paths:
                        (sha, {p: (sync.git_blob(DATA), DATA) for p in paths}))
    monkeypatch.setattr(sync, '_fetch_consumer_documents', lambda name, path, sha, token:
                        ([('vendor.lock.json', lock)], {'ci.yml': 'check'} if sha == SHA else {}))
    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', lambda *a, **k: pytest.fail('write'))
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: pytest.fail('dispatch'))
    with pytest.raises(ValueError, match='requires reviewed parent baseline'):
        sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                 skip_source_diff=True, autofix=True, token='fixture')


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

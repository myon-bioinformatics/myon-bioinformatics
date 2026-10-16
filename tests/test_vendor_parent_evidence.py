"""Parent evidence must remain independent of a child's editable claims."""
import copy
import hashlib
import json
import os
import subprocess

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
    public_record = {
        'id': 42, 'full_name': NAME, 'private': False, 'visibility': 'public', 'archived': False,
    }
    monkeypatch.setattr(sync, 'fetch_repository_record', lambda *a, **k: dict(public_record))
    monkeypatch.setattr(sync, 'public_repository_page_record', lambda *a, **k: dict(public_record))
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
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)
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


LOCKED_WORKFLOW = b'''name: Non-Dart checks
on: [push, pull_request, workflow_dispatch]
jobs:
  python-vendor-locked:
    runs-on: ubuntu-latest
    steps:
      - run: python -S vendor_sync.py check --manifest vendor.lock.json
      - run: python -S vendor_sync.py materialize --manifest vendor.lock.json
      - run: python -S vendor_sync.py check --manifest vendor.lock.json
'''
LOCKED_PATH = '.github/workflows/non-dart.yml'


def locked_fixture(monkeypatch, *, data=LOCKED_WORKFLOW, resolved=None):
    registry, lock, heads = fixture(monkeypatch)
    registry['consumers'][0]['locked_ci'] = {
        'workflow': LOCKED_PATH, 'sha256': hashlib.sha256(LOCKED_WORKFLOW).hexdigest(),
    }

    def snapshot(repo, sha, paths):
        if LOCKED_PATH in paths:
            if data is None:
                raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR)
            return resolved or sha, {LOCKED_PATH: (sync.git_blob(data), data)}
        return sha, {p: (sync.git_blob(DATA), DATA) for p in paths}

    monkeypatch.setattr(sync, '_public_git_snapshot', snapshot)
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda name, path, sha, token=None:
                        None if path == sync.CHILD_CI_WORKFLOW_PATH else json.dumps(lock))
    return registry, lock, heads


def test_intentional_template_removal_uses_approved_existing_lane(monkeypatch):
    registry, _, heads = locked_fixture(monkeypatch)
    report = sync.audit_child_workflow_templates(registry, heads, TEMPLATE.decode(),
                                                repository_names=[NAME])
    assert report['failures'] == report['missing'] == []
    assert report['locked_ci_checked'][0]['workflow'] == LOCKED_PATH
    plan = sync.plan_heads_refresh(registry, heads, {NAME: {'sha': NEW, 'repository_id': 42}},
                                   TEMPLATE.decode())
    assert plan['heads'][NAME]['sha'] == NEW
    assert plan['skipped'] == []


@pytest.mark.parametrize('data,resolved', [
    (None, None),  # required replacement workflow removed
    (LOCKED_WORKFLOW.replace(b'python-vendor-locked:', b'other-job:'), None),
    (LOCKED_WORKFLOW.replace(b'    runs-on:', b'    if: false\n    runs-on:'), None),
    (LOCKED_WORKFLOW.replace(b'    runs-on:', b'    continue-on-error: true\n    runs-on:'), None),
    (LOCKED_WORKFLOW.replace(b'check --manifest', b'update --manifest'), None),
    (LOCKED_WORKFLOW.replace(b'pull_request, ', b''), None),
    (LOCKED_WORKFLOW, NEW),  # bytes read from the wrong revision
])
def test_missing_or_weakened_locked_ci_fails_and_blocks_refresh(monkeypatch, data, resolved):
    registry, _, heads = locked_fixture(monkeypatch, data=data, resolved=resolved)
    report = sync.audit_child_workflow_templates(registry, heads, TEMPLATE.decode(),
                                                repository_names=[NAME])
    assert len(report['failures']) == 1
    assert report['missing'] == report['locked_ci_checked'] == []
    if resolved is None:  # NEW is the requested revision during refresh.
        plan = sync.plan_heads_refresh(registry, heads, {NAME: {'sha': NEW, 'repository_id': 42}},
                                       TEMPLATE.decode())
        assert plan['heads'][NAME]['sha'] == SHA
        assert plan['skipped'][0]['reason'] == 'locked_ci_mismatch'


def test_unregistered_missing_template_remains_explicitly_unenforced(monkeypatch):
    registry, _, heads = fixture(monkeypatch)
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda *a, **k: None)
    report = sync.audit_child_workflow_templates(registry, heads, TEMPLATE.decode(),
                                                repository_names=[NAME])
    assert report['failures'] == []
    assert len(report['missing']) == 1
    assert report['locked_ci_checked'] == []


def test_approved_lane_does_not_mask_invalid_legacy_template_or_missing_head(monkeypatch):
    registry, _, heads = locked_fixture(monkeypatch)
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda *a, **k: 'changed template')
    report = sync.audit_child_workflow_templates(registry, heads, TEMPLATE.decode(),
                                                repository_names=[NAME])
    assert len(report['failures']) == 1
    heads['heads'].clear()
    report = sync.audit_child_workflow_templates(registry, heads, TEMPLATE.decode(),
                                                repository_names=[NAME])
    assert len(report['failures']) == 1
    assert report['missing'] == []


@pytest.mark.parametrize('fault', [None, 'workflow', 'file', 'topology'])
def test_parent_contract_runs_both_locked_lane_and_adopted_file_audits(tmp_path, monkeypatch, fault):
    registry, lock, heads = locked_fixture(monkeypatch,
                                          data=None if fault == 'workflow' else LOCKED_WORKFLOW)
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_bytes(TEMPLATE)
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: heads['heads'][NAME])
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    if fault == 'file':
        lock['files'][0]['sha256'] = '0' * 64
    elif fault == 'topology':
        lock['files'][0]['destination'] = 'unregistered.py'
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                      skip_source_diff=True, pr_validation=True)
    assert report['failure_count'] == report['audit_failure_count'] == (0 if fault is None else 1)
    assert report['template_missing_count'] == 0
    assert report['audit_blocked'] == ([] if fault is None else [NAME])
    if fault is None:
        summary = tmp_path / 'summary.md'
        sync._write_child_heads_summary(report, summary)
        assert 'Approved locked CI workflow:' in summary.read_text()
        assert 'without copied child CI workflow' not in summary.read_text()


@pytest.mark.parametrize('removed', [False, True])
def test_autofix_accepts_template_removal_but_preserves_head_when_required_lane_is_removed(
        tmp_path, monkeypatch, removed):
    registry, _, heads = locked_fixture(monkeypatch)
    snapshot = sync._public_git_snapshot

    def candidate_snapshot(repo, sha, paths):
        if removed and sha == NEW and LOCKED_PATH in paths:
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR)
        return snapshot(repo, sha, paths)

    monkeypatch.setattr(sync, '_public_git_snapshot', candidate_snapshot)
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_bytes(TEMPLATE)
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k:
                        {'sha': NEW, 'repository_id': 42})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    written = {}

    def capture(repo, branch, title, body, files, token):
        written.update(json.loads(files['child-heads.json']))
        return {'url': 'https://example.invalid/1'}

    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', capture)
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: None)
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                      skip_source_diff=True, autofix=True, token='fixture')
    assert written['heads'][NAME]['sha'] == (SHA if removed else NEW)
    assert bool(report['heads_refresh_skipped']) is removed
    assert registry['consumers'][0]['locked_ci']['sha256'] == hashlib.sha256(LOCKED_WORKFLOW).hexdigest()


@pytest.mark.parametrize('mode', ['regular', 'symlink', 'directory'])
def test_approved_workflow_must_be_a_regular_git_blob(tmp_path, monkeypatch, mode):
    repo = tmp_path / 'child'
    repo.mkdir()
    _git(repo, 'init', '-b', 'main')
    _git(repo, 'config', 'user.name', 'Fixture')
    _git(repo, 'config', 'user.email', 'fixture@example.invalid')
    path = repo / LOCKED_PATH
    path.parent.mkdir(parents=True)
    if mode == 'symlink':
        (repo / 'actual.yml').write_bytes(LOCKED_WORKFLOW)
        path.symlink_to('../../actual.yml')
    elif mode == 'directory':
        path.mkdir()
        (path / 'nested.yml').write_bytes(LOCKED_WORKFLOW)
    else:
        path.write_bytes(LOCKED_WORKFLOW)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'workflow')
    revision = _git(repo, 'rev-parse', 'HEAD')
    snapshot = sync._public_git_snapshot
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda name, sha, paths:
                        snapshot(name, sha, paths, remote=str(repo)))
    contract = {'workflow': LOCKED_PATH, 'sha256': hashlib.sha256(LOCKED_WORKFLOW).hexdigest()}
    if mode == 'regular':
        assert sync._verify_locked_ci(NAME, revision, contract)['workflow'] == LOCKED_PATH
    else:
        with pytest.raises(ValueError, match='regular file'):
            sync._verify_locked_ci(NAME, revision, contract)


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
    ('deleted_workflow', False),
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
    elif fault in (None, 'deleted_workflow'):
        assert plan['heads'][NAME]['sha'] == NEW


def test_plan_heads_refresh_allows_vendor_child_ci_removal(monkeypatch):
    registry, lock, heads = fixture(monkeypatch)
    workflow = TEMPLATE.decode()
    new_heads = {NAME: {'sha': NEW, 'repository_id': 42}}

    def fetch_text(name, path, revision, token=None):
        if path == sync.CHILD_CI_WORKFLOW_PATH:
            if revision == SHA:
                return workflow
            return None
        return json.dumps(lock)

    monkeypatch.setattr(sync, '_fetch_repo_text', fetch_text)
    plan = sync.plan_heads_refresh(registry, heads, new_heads, workflow, token='token')
    assert plan['heads'][NAME]['sha'] == NEW
    assert not any(row.get('reason') == 'workflow_removed' for row in plan['skipped'])


def test_plan_heads_refresh_drops_unmonitored_stale_entries():
    registry = {'schema': 'vendor-consumers/1', 'consumers': []}
    heads = {
        'schema': sync.CHILD_HEADS_SCHEMA,
        'ref': 'main',
        'heads': {
            NAME: {'sha': SHA, 'repository_id': 42},
            'owner/former-public': {'sha': NEW, 'repository_id': 99},
        },
    }
    plan = sync.plan_heads_refresh(
        registry, heads, {NAME: {'sha': NEW, 'repository_id': 42}}, TEMPLATE.decode(),
    )
    assert set(plan['heads']) == {NAME}
    assert plan['heads'][NAME]['sha'] == NEW


def test_autofix_drops_unmonitored_stale_heads(tmp_path, monkeypatch):
    registry, lock, heads = fixture(monkeypatch)
    stale_name = 'owner/former-public'
    heads['heads'][stale_name] = {'sha': NEW, 'repository_id': 99}
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text(TEMPLATE.decode())
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: {'sha': NEW, 'repository_id': 42})
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda repo, sha, paths:
                        (sha, {p: (sync.git_blob(DATA), DATA) for p in paths}))
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda *a, **k: json.dumps(lock))
    captured = {}

    def capture_pr(repo, branch, title, body, files, token):
        captured['heads'] = json.loads(files['child-heads.json'].decode())['heads']
        return {'pull_number': 1, 'url': 'https://example.invalid/1', 'branch': branch}

    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', capture_pr)
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: None)
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                      skip_source_diff=True, autofix=True, token='fixture')
    assert stale_name not in captured['heads']
    assert report['heads_dropped_unmonitored_count'] == 1


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
    assert report['audit_failure_count'] == 1
    assert report['adopted_child_files']['failure_count'] == 1
    assert sync.PUBLIC_GIT_FETCH_ERROR in json.dumps(report['audit_skipped'])


def _git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()


def _merged_source_repo(tmp_path):
    path = tmp_path / 'owner-source'
    path.mkdir()
    _git(path, 'init', '-b', 'main')
    _git(path, 'config', 'user.name', 'Fixture')
    _git(path, 'config', 'user.email', 'fixture@example.invalid')
    (path / 'tool.py').write_bytes(DATA)
    _git(path, 'add', '.')
    _git(path, 'commit', '-m', 'base')
    _git(path, 'checkout', '-b', 'feature')
    _git(path, 'commit', '--allow-empty', '-m', 'feature pin')
    intermediate = _git(path, 'rev-parse', 'HEAD')
    _git(path, 'checkout', 'main')
    _git(path, 'merge', '--no-ff', 'feature', '-m', 'merge feature')
    merge_commit = _git(path, 'rev-parse', 'HEAD')
    return path, intermediate, merge_commit


def test_portfolio_pr39_merged_pr_intermediate_pin_fails_parent_ci(tmp_path, monkeypatch):
    """github.io #39: merged PR intermediate pin is an ancestor but not on main first-parent."""
    registry, lock, heads = fixture(monkeypatch)
    source_path, intermediate, merge_commit = _merged_source_repo(tmp_path)
    lock['files'][0]['commit'] = intermediate
    lock['files'][0]['ref'] = intermediate
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text(TEMPLATE.decode())
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: heads['heads'][NAME])
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    monkeypatch.setattr(sync, '_fetch_repo_text', lambda *a, **k: json.dumps(lock))
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda repo, sha, paths:
                        (sha, {p: (sync.git_blob(DATA), DATA) for p in paths}))
    report = sync.run_parent_child_ci(
        registry, heads, parent_root=tmp_path, skip_source_diff=True, pr_validation=True,
        local_roots={'owner/source': str(source_path)},
    )
    assert report['failure_count'] == 1
    assert sync.PIN_NOT_MERGED_ERROR in json.dumps(report['audit_skipped'])
    sync.commit_merged_into_default(
        'owner/source', merge_commit, local_roots={'owner/source': str(source_path)},
    )


def test_autofix_keeps_old_head_when_audit_fails(tmp_path, monkeypatch):
    registry, lock, heads = fixture(monkeypatch)
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text(TEMPLATE.decode())
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: {'sha': NEW, 'repository_id': 42})
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})
    def snapshot(repo, revision, paths):
        if repo == NAME and revision == NEW:
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR)
        data = DATA
        return revision, {path: (sync.git_blob(data), data) for path in paths}

    monkeypatch.setattr(sync, '_public_git_snapshot', snapshot)
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)
    captured = {}

    def capture_pr(repo, branch, title, body, files, token):
        captured['heads'] = json.loads(files['child-heads.json'].decode())['heads']
        return {'pull_number': 1, 'url': 'https://example.invalid/1', 'branch': branch}

    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', capture_pr)
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: None)
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                      skip_source_diff=True, autofix=True, token='fixture')
    assert captured['heads'][NAME]['sha'] == SHA
    assert any(row['reason'] == 'adopted_baseline_audit_failure'
               for row in report['heads_refresh_skipped'])


def test_autofix_records_new_head_after_child_fixes_main(tmp_path, monkeypatch):
    registry, lock, heads = fixture(monkeypatch)
    bad_sha = 'd' * 40
    good_sha = NEW
    bad_lock = copy.deepcopy(lock)
    bad_lock['files'][0]['commit'] = bad_sha
    bad_lock['files'][0]['ref'] = bad_sha
    (tmp_path / 'templates').mkdir()
    (tmp_path / sync.CHILD_CI_TEMPLATE).write_text(TEMPLATE.decode())
    monkeypatch.setattr(sync, 'monitored_repositories', lambda *a, **k: [NAME])
    monkeypatch.setattr(sync, 'fetch_child_head_record', lambda *a, **k: {'sha': good_sha, 'repository_id': 42})
    monkeypatch.setattr(sync, 'audit_child_workflow_templates', lambda *a, **k: {'failures': [], 'missing': []})
    monkeypatch.setattr(sync, '_copy_parent_vendor_baseline', lambda *a: tmp_path)
    monkeypatch.setattr(sync, '_offline_parent_vendor_check', lambda *a: {})

    def fetch_text(name, path, revision, token=None):
        if path == sync.CHILD_CI_WORKFLOW_PATH:
            return TEMPLATE.decode()
        if revision == SHA:
            return json.dumps(bad_lock)
        if revision == good_sha:
            return json.dumps(lock)
        return json.dumps(lock)

    monkeypatch.setattr(sync, '_fetch_repo_text', fetch_text)

    def merged(repository, commit, **kwargs):
        if commit == bad_sha:
            raise ValueError(sync.PIN_NOT_MERGED_ERROR)
        return True

    monkeypatch.setattr(sync, 'commit_merged_into_default', merged)
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda repo, sha, paths:
                        (sha, {p: (sync.git_blob(DATA), DATA) for p in paths}))
    captured = {}

    def capture_pr(repo, branch, title, body, files, token):
        captured['heads'] = json.loads(files['child-heads.json'].decode())['heads']
        return {'pull_number': 1, 'url': 'https://example.invalid/1', 'branch': branch}

    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', capture_pr)
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: None)
    validate_report = sync.run_parent_child_ci(
        registry, heads, parent_root=tmp_path, skip_source_diff=True, pr_validation=True,
    )
    assert validate_report['failure_count'] == 1
    assert sync.PIN_NOT_MERGED_ERROR in json.dumps(validate_report['audit_skipped'])
    sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                             skip_source_diff=True, autofix=True, token='fixture')
    assert captured['heads'][NAME]['sha'] == good_sha


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
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)
    bad_workflow = 'jobs:\n  custom:\n    steps: []\n'

    def fetch_text(name, path, sha, token=None):
        if path == sync.CHILD_CI_WORKFLOW_PATH:
            return bad_workflow if sha == NEW else TEMPLATE.decode()
        return json.dumps(lock)

    monkeypatch.setattr(sync, '_fetch_repo_text', fetch_text)
    writes = []
    captured = {}

    def capture_pr(repo, branch, title, body, files, token):
        writes.append(1)
        captured['heads'] = json.loads(files['child-heads.json'].decode())['heads']
        return {'pull_number': 1, 'url': 'https://example.invalid/1', 'branch': 'vendor/child-heads-sync'}

    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', capture_pr)
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: None)
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                      skip_source_diff=True, autofix=True, token='fixture')
    assert writes
    assert captured['heads'][NAME]['sha'] == SHA
    assert any(row['reason'] == 'workflow_template_mismatch' for row in report['heads_refresh_skipped'])


def test_autofix_refreshes_head_when_vendor_child_ci_removed(tmp_path, monkeypatch):
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
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)

    def fetch_text(name, path, sha, token=None):
        if path == sync.CHILD_CI_WORKFLOW_PATH:
            return TEMPLATE.decode() if sha == SHA else None
        return json.dumps(lock)

    monkeypatch.setattr(sync, '_fetch_repo_text', fetch_text)
    captured = {}

    def capture_pr(repo, branch, title, body, files, token):
        captured['heads'] = json.loads(files['child-heads.json'].decode())['heads']
        return {'pull_number': 1, 'url': 'https://example.invalid/1', 'branch': 'vendor/child-heads-sync'}

    monkeypatch.setattr(sync, '_open_or_update_autofix_pr', capture_pr)
    monkeypatch.setattr(sync, '_dispatch_parent_workflow', lambda *a, **k: None)
    report = sync.run_parent_child_ci(registry, heads, parent_root=tmp_path,
                                      skip_source_diff=True, autofix=True, token='fixture')
    assert captured['heads'][NAME]['sha'] == NEW
    assert not any(row.get('reason') == 'workflow_removed' for row in report['heads_refresh_skipped'])


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
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)
    report = sync.audit_adopted_child_files(registry, heads, [NAME],
                                            parent_root=tmp_path, parent_repository=NAME)
    assert report['failure_count'] == (0 if fault is None else 1)

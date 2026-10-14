"""Execute the distributed bootstrap, then check the actual child validation path."""
import copy
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
import yaml
import vendor_sync as sync

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / sync.CHILD_CI_TEMPLATE).read_bytes()
PARENT = sync.PARENT_REPOSITORY
SHA = 'a' * 40


def bootstrap(tmp_path, monkeypatch, fault=None, merged=False):
    monkeypatch.chdir(tmp_path)
    ref = {'schema': sync.PARENT_PR_REF_SCHEMA, 'repository': PARENT,
           'pull_number': 78, 'repository_id': 638801160, 'commit': SHA}
    repo = {'id': ref['repository_id'], 'full_name': PARENT}
    pull = {'number': 78, 'state': 'open', 'base': {'repo': dict(repo)},
            'head': {'sha': SHA, 'repo': dict(repo)}, 'merged_at': None}
    if merged:
        pull.update(state='closed', merged_at='2026-10-14', merge_commit_sha=SHA)
        pull['head']['sha'] = 'b' * 40
    if fault == 'sha': ref['commit'] = 'b' * 40
    if fault == 'missing_sha': ref.pop('commit')
    if fault == 'repo': repo['id'] += 1
    if fault == 'base': pull['base']['repo']['id'] += 1
    if fault == 'number': pull['number'] += 1
    if fault == 'fork': pull['head']['repo']['id'] += 1
    if fault == 'closed': pull['state'] = 'closed'
    if fault == 'bool': ref['pull_number'] = True
    if fault == 'shell': ref['pull_number'] = '78; touch PWNED'
    (tmp_path / 'ref.json').write_text(json.dumps(ref))
    monkeypatch.setenv('PARENT_REPOSITORY', PARENT)
    monkeypatch.setenv('PARENT_REF_PATH', str(tmp_path / 'ref.json'))
    monkeypatch.setenv('CHILD_CONSUMER', 'owner/child')
    monkeypatch.delenv('GH_TOKEN', raising=False)
    calls = []

    def urlopen(request, **kwargs):
        if fault == 'api': raise HTTPError(request.full_url, 404, 'missing', None, None)
        return io.BytesIO(json.dumps(pull if '/pulls/' in request.full_url else repo).encode())

    def run(argv, **kwargs):
        calls.append(argv)
        if fault == 'fetch' and 'fetch' in argv:
            raise subprocess.CalledProcessError(128, argv)
        if 'rev-parse' in argv:
            return SimpleNamespace(stdout=('b' * 40 if fault == 'checkout' else SHA) + '\n')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    monkeypatch.setattr(subprocess, 'run', run)
    workflow = yaml.safe_load(TEMPLATE)
    script = workflow['jobs']['vendor-verify']['steps'][-1]['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    try:
        exec(compile(script, '<distributed-child-bootstrap>', 'exec'), {})
    except (SystemExit, HTTPError, subprocess.CalledProcessError) as error:
        return calls, error
    raise AssertionError('bootstrap did not exit')


@pytest.mark.parametrize('merged', [False, True])
def test_bootstrap_runs_only_verified_parent_sha(tmp_path, monkeypatch, merged):
    calls, error = bootstrap(tmp_path, monkeypatch, merged=merged)
    assert isinstance(error, SystemExit) and error.code == 0
    assert calls[2][-1] == SHA
    assert 'run-child-ci' in calls[-1]
    assert '--consumer-name' in calls[-1]
    assert calls[-1][calls[-1].index('--root') + 1] == '.'


@pytest.mark.parametrize('fault', ['sha', 'missing_sha', 'repo', 'base', 'number', 'fork', 'closed', 'bool', 'shell', 'api'])
def test_bootstrap_rejects_before_any_parent_code_or_git(tmp_path, monkeypatch, fault):
    calls, error = bootstrap(tmp_path, monkeypatch, fault=fault)
    assert error and not calls
    assert not (tmp_path / 'PWNED').exists()


@pytest.mark.parametrize('fault', ['fetch', 'checkout'])
def test_reference_fetch_failure_never_executes_parent(tmp_path, monkeypatch, fault):
    calls, error = bootstrap(tmp_path, monkeypatch, fault=fault)
    assert error
    assert not any('run-child-ci' in call for call in calls)


def child_fixture(tmp_path, monkeypatch):
    parent, child = tmp_path / 'parent', tmp_path / 'child'
    parent.mkdir(); child.mkdir()
    registry = {'schema': 'vendor-consumers/1', 'consumers': [{
        'repository': 'owner/child', 'state': 'locked', 'lock': 'vendor.lock.json', 'entries': []}]}
    lock = {'schema': sync.SCHEMA, 'files': []}
    for source, data in [('tool.py', b'adopted code\n'), ('LICENSE', b'adopted license\n')]:
        item = {'repository': 'owner/tool', 'ref': SHA, 'commit': SHA, 'source': source,
                'destination': 'vendor/' + source, 'blob_sha': sync.git_blob(data),
                'sha256': sync.hashlib.sha256(data).hexdigest()}
        lock['files'].append(item)
        registry['consumers'][0]['entries'].append({k: item[k] for k in ('repository', 'source', 'destination')})
        target = child / item['destination']; target.parent.mkdir(exist_ok=True); target.write_bytes(data)
    (child / 'vendor.lock.json').write_text(json.dumps(lock))
    (parent / 'vendor-consumers.json').write_text(json.dumps(registry))
    (parent / 'vendor-catalog.json').write_text(json.dumps({
        'schema': 'vendor-catalog/1',
        'tools': [{
            'repository': 'owner/tool', 'source': 'tool.py', 'commit': SHA, 'reason': 'test',
            'consumers': {'owner/child': {'enrollment': 'enrolled', 'reason': 'test'}},
        }, {
            'repository': 'owner/tool', 'source': 'LICENSE', 'commit': SHA, 'reason': 'test',
            'consumers': {'owner/child': {'enrollment': 'enrolled', 'reason': 'test'}},
        }],
    }))
    stale_heads = {'schema': sync.CHILD_HEADS_SCHEMA, 'ref': 'main',
                   'heads': {'owner/child': {'sha': 'c' * 40, 'repository_id': 42}}}
    (parent / 'child-heads.json').write_text(json.dumps(stale_heads))
    for root, path in [(parent, sync.CHILD_CI_TEMPLATE), (child, sync.CHILD_CI_WORKFLOW_PATH)]:
        target = root / path; target.parent.mkdir(parents=True); target.write_bytes(TEMPLATE)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(parent), *args]).decode().strip()
    git('init', '-b', 'main'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
    git('add', '.'); git('commit', '-m', 'baseline')
    pin = git('rev-parse', 'HEAD')
    (child / 'ref.json').write_text(json.dumps({'schema': sync.PARENT_PR_REF_SCHEMA, 'repository': PARENT,
                                              'pull_number': 78, 'repository_id': 638801160, 'commit': pin}))
    monkeypatch.setattr(sync, 'fetch_repository_record', lambda *a, **k: {
        'id': 42, 'full_name': 'owner/child', 'private': False, 'visibility': 'public', 'archived': False})
    monkeypatch.setattr(sync, '_ensure_public_source_repository', lambda *a, **k: True)
    monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: True)
    source_bytes = {'tool.py': b'adopted code\n', 'LICENSE': b'adopted license\n'}

    def snapshot(repo, revision, paths):
        return revision, {
            path: (sync.git_blob(source_bytes[path]), source_bytes[path]) for path in paths
        }

    monkeypatch.setattr(sync, '_public_git_snapshot', snapshot)
    def no_resolve(*a, **k): raise AssertionError('must not resolve parent PR twice')
    monkeypatch.setattr(sync, 'resolve_parent_pr', no_resolve)
    return parent, child, lock


def test_child_ci_rejects_coordinated_lock_and_file_tamper(tmp_path, monkeypatch):
    import hashlib
    parent, child, lock = child_fixture(tmp_path, monkeypatch)
    tampered = b'tampered lock and bytes\n'
    local = copy.deepcopy(lock)
    local['files'][0]['blob_sha'] = sync.git_blob(tampered)
    local['files'][0]['sha256'] = hashlib.sha256(tampered).hexdigest()
    (child / 'vendor.lock.json').write_text(json.dumps(local))
    (child / 'vendor/tool.py').write_bytes(tampered)
    with pytest.raises(ValueError, match='child vendor file mismatch'):
        sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)


def test_child_uses_local_lock_not_parent_recorded_snapshot(tmp_path, monkeypatch):
    parent, child, lock = child_fixture(tmp_path, monkeypatch)
    local = copy.deepcopy(lock)
    local['files'][0]['commit'] = 'b' * 40
    local['files'][0]['ref'] = 'b' * 40
    (child / 'vendor.lock.json').write_text(json.dumps(local))
    result = sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)
    assert result['files_checked'] == 2
    assert json.loads((parent / 'child-heads.json').read_text())['heads']['owner/child']['sha'] == 'c' * 40


def test_run_child_ci_does_not_read_child_heads_snapshot(tmp_path, monkeypatch):
    parent, child, lock = child_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(sync, 'validate_child_heads', lambda *a, **k: pytest.fail('child-heads must not gate child CI'))
    sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)


@pytest.mark.parametrize('fault', ['file', 'license', 'lock', 'missing_lock', 'template', 'crlf', 'parent_sha',
                                  'topology', 'private_source'])
def test_child_rejects_modified_or_unavailable_baseline(tmp_path, monkeypatch, fault):
    parent, child, lock = child_fixture(tmp_path, monkeypatch)
    if fault in ('file', 'license'):
        (child / ('vendor/tool.py' if fault == 'file' else 'vendor/LICENSE')).write_bytes(b'tampered')
    elif fault == 'lock':
        local = copy.deepcopy(lock); local['files'][0]['commit'] = 'b' * 40
        local['files'][0]['ref'] = 'b' * 40
        (child / 'vendor.lock.json').write_text(json.dumps(local))
        monkeypatch.setattr(sync, 'commit_merged_into_default', lambda *a, **k: (_ for _ in ()).throw(ValueError('not merged')))
    elif fault == 'missing_lock': (child / 'vendor.lock.json').unlink()
    elif fault == 'template': (child / sync.CHILD_CI_WORKFLOW_PATH).write_bytes(b'name: custom\n')
    elif fault == 'crlf': (child / sync.CHILD_CI_WORKFLOW_PATH).write_bytes(TEMPLATE.replace(b'\n', b'\r\n'))
    elif fault == 'parent_sha':
        ref = json.loads((child / 'ref.json').read_text()); ref['commit'] = 'b' * 40
        (child / 'ref.json').write_text(json.dumps(ref))
    elif fault == 'topology':
        doc = json.loads((parent / 'vendor-consumers.json').read_text()); doc['consumers'][0]['entries'].pop()
        (parent / 'vendor-consumers.json').write_text(json.dumps(doc))
    elif fault == 'private_source':
        monkeypatch.setattr(sync, '_ensure_public_source_repository', lambda *a, **k: (_ for _ in ()).throw(ValueError('source repository is not public')))
    with pytest.raises((ValueError, OSError)):
        sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)

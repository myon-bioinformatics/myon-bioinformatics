import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import vendor_candidate as candidate
import vendor_sync as sync

OLD = '1' * 40
NEW = '2' * 40


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / 'consumer'
    root.mkdir()
    (root / 'vendor').mkdir()
    files = []
    for source, destination in [('example.py', 'vendor/example.py'), ('LICENSE', 'vendor/LICENSE')]:
        data = b'OLD\n'
        (root / destination).write_bytes(data)
        files.append(dict(repository='owner/example', source=source, destination=destination,
                          ref='main', commit=OLD, blob_sha=sync.git_blob(data),
                          sha256=hashlib.sha256(data).hexdigest()))
    (root / 'vendor.lock.json').write_text(json.dumps({'schema': sync.SCHEMA, 'files': files}))
    (root / 'test_use.py').write_text("from pathlib import Path\ndef test_candidate():\n assert Path('vendor/example.py').read_bytes() == b'NEW\\n'\n")
    catalog = {'schema': 'vendor-catalog/1', 'tools': [dict(repository='owner/example', commit=NEW, reason='reviewed')]}
    registry = {'schema': 'vendor-consumers/1', 'consumers': [dict(repository='owner/consumer', state='locked', lock='vendor.lock.json',
        entries=[{k: v for k, v in f.items() if k in ('repository', 'source', 'destination')} for f in files])]}
    return root, catalog, registry


def check(fixture, **kwargs):
    root, catalog, registry = fixture
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    def snapshot(repo, commit, sources):
        assert repo == 'owner/example' and commit == NEW
        assert sources == ['LICENSE', 'example.py']
        return commit, {s: (sync.git_blob(b'NEW\n'), b'NEW\n') for s in sources}
    result = candidate.verify_candidate(root, catalog, registry, 'owner/consumer', 'owner/example', 'example.py',
                                        ['test_use.py'], snapshot=kwargs.pop('snapshot', snapshot), **kwargs)
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert after == before
    return result


def test_real_offline_candidate_pytest_and_exact_receipt(fixture):
    receipt, junit = check(fixture)
    assert receipt['status'] == 'success'
    assert receipt['promoted'] is False
    assert receipt['tests']['executed'] == 1
    assert receipt['locked'][0]['commit'] == OLD
    assert {f['commit'] for f in receipt['candidate']} == {NEW}
    assert receipt['tests']['junit_sha256'] == candidate.digest(junit)


@pytest.mark.parametrize('bad', ['commit', 'blob', 'missing', 'oversize'])
def test_identity_failures_never_test_or_modify_lock(fixture, bad):
    def snapshot(repo, commit, sources):
        data = b'x' * (sync.MAX_BYTES + 1) if bad == 'oversize' else b'NEW\n'
        return (OLD if bad == 'commit' else commit), ({} if bad == 'missing' else
            {s: ('0' * 40 if bad == 'blob' else sync.git_blob(data), data) for s in sources})
    receipt, junit = check(fixture, snapshot=snapshot, run=lambda *a, **k: pytest.fail('tests ran'))
    assert receipt['status'] == 'mismatch' and junit is None


def test_network_failure_is_unverified(fixture):
    def snapshot(*args):
        raise OSError('unavailable')
    receipt, _ = check(fixture, snapshot=snapshot)
    assert receipt['status'] == 'unverified' and receipt['reason'] == 'fetch_error'


@pytest.mark.parametrize('outcome', ['success', 'fetch_error', 'tests_failed'])
def test_equal_recommendation_still_fetches_and_tests(fixture, outcome):
    root, catalog, registry = fixture
    catalog['tools'][0]['commit'] = OLD
    calls = []

    def snapshot(repo, commit, sources):
        calls.append((repo, commit, sources))
        assert commit == OLD and sources == ['LICENSE', 'example.py']
        if outcome == 'fetch_error':
            raise OSError('unavailable')
        return commit, {s: (sync.git_blob(b'OLD\n'), b'OLD\n') for s in sources}

    body = "assert Path('vendor/example.py').read_bytes() == b'OLD\\n'"
    if outcome == 'tests_failed':
        body = 'assert False'
    (root / 'test_use.py').write_text('from pathlib import Path\ndef test_equal():\n ' + body + '\n')
    receipt, junit = check(fixture, snapshot=snapshot)
    assert len(calls) == 1
    assert receipt['recommended_commit'] == OLD
    assert receipt['promoted'] is False
    if outcome == 'fetch_error':
        assert receipt['status'] == 'unverified' and receipt['reason'] == outcome
        assert junit is None
    else:
        assert junit is not None and receipt['tests']['executed'] == 1
        assert {f['commit'] for f in receipt['candidate']} == {OLD}
        assert receipt['status'] == ('success' if outcome == 'success' else 'mismatch')
        assert receipt['reason'] == ('candidate_compatible' if outcome == 'success' else outcome)


@pytest.mark.parametrize('body,status,reason', [
    ('assert False', 'mismatch', 'tests_failed'),
    ("import pytest; pytest.skip('unavailable')", 'unverified', 'empty_or_skipped_tests'),
    ("from pathlib import Path; Path('vendor.lock.json').write_text('{}')", 'mismatch', 'post_test_identity_error'),
    ("from pathlib import Path; Path('vendor/example.py').write_text('drift')", 'mismatch', 'post_test_identity_error')])
def test_real_test_failures_skips_and_candidate_mutation(fixture, body, status, reason):
    (fixture[0] / 'test_use.py').write_text('def test_case():\n ' + body + '\n')
    receipt, junit = check(fixture)
    assert receipt['status'] == status and receipt['reason'] == reason
    assert junit is not None


@pytest.mark.parametrize('junit,reason', [
    (None, 'missing_junit'), (b'<bad', 'junit_error'),
    (b'<testsuites><testsuite tests="0"/></testsuites>', 'empty_or_skipped_tests')])
def test_missing_invalid_empty_junit_is_not_success(fixture, junit, reason):
    def run(command, **kwargs):
        if junit is not None:
            Path(command[-1].split('=', 1)[1]).write_bytes(junit)
        return subprocess.CompletedProcess(command, 0, b'', b'')
    receipt, _ = check(fixture, run=run)
    assert receipt['status'] == 'unverified' and receipt['reason'] == reason


def test_timeout_is_unverified(fixture):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    receipt, _ = check(fixture, run=run)
    assert receipt['status'] == 'unverified'


def test_edited_locked_bytes_rejected_before_network(fixture):
    (fixture[0] / 'vendor/example.py').write_bytes(b'edit')
    receipt, _ = check(fixture, snapshot=lambda *a: pytest.fail('network'))
    assert receipt['status'] == 'mismatch' and receipt['reason'] == 'locked_error'


def test_topology_skip_missing_enrollment_and_no_tests(fixture):
    fixture[1]['tools'][0]['default_enrollment'] = {'enrollment': 'skipped', 'reason': 'explicit_skip'}
    assert check(fixture)[0]['reason'] == 'skipped'
    del fixture[1]['tools'][0]['default_enrollment']
    fixture[2]['consumers'][0]['entries'].pop()
    assert check(fixture)[0]['status'] != 'success'


def test_cli_reserves_output_and_writes_unverified_receipt(fixture, tmp_path):
    root, catalog, registry = fixture
    cp, rp = tmp_path / 'catalog.json', tmp_path / 'registry.json'
    cp.write_text(json.dumps(catalog)); rp.write_text(json.dumps(registry))
    args = ['--root', str(root), '--catalog', str(cp), '--registry', str(rp),
            '--consumer', 'owner/consumer', '--repository', 'owner/example', '--source', 'example.py',
            '--output', str(tmp_path / 'result')]
    assert candidate.main(args) == 2
    receipt = json.loads((tmp_path / 'result/verification.json').read_text())
    assert receipt['reason'] == 'no_tests_selected'
    assert candidate.main(args) == 2
    assert candidate.main(args[:-1] + [str(root / 'result')]) == 2
    assert not (root / 'result').exists()


def test_live_workflow_is_read_only_and_uses_recommendation():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / '.github/workflows/vendor-candidate.yml').read_text())
    assert workflow['permissions'] == {'contents': 'read'}
    steps = workflow['jobs']['gh-identity']['steps']
    command = next(s['run'] for s in steps if 'Verify recommended' in s.get('name', ''))
    assert 'vendor_candidate.py' in command and '--test tests/test_gh_ops.py' in command
    assert 'promote' not in command and 'update' not in command
    assert steps[0]['with']['persist-credentials'] is False


def test_missing_enrollment_and_legacy_are_unverified(fixture):
    fixture[1]['tools'][0]['source'] = 'other.py'
    receipt, _ = candidate.verify_candidate(*fixture, 'owner/consumer', 'owner/example', 'other.py', ['test_use.py'],
        snapshot=lambda *a: pytest.fail('network'))
    assert receipt['status'] == 'unverified' and receipt['reason'] == 'not_enrolled'
    fixture[2]['consumers'][0].update(state='legacy', lock=None, reason='legacy_format', legacy_provenance=['provenance.json'])
    receipt, _ = candidate.verify_candidate(*fixture, 'owner/consumer', 'owner/example', 'other.py', ['test_use.py'])
    assert receipt['reason'] == 'legacy_consumer'


def test_unsafe_test_path_rejected_before_fetch(fixture):
    receipt, _ = candidate.verify_candidate(*fixture, 'owner/consumer', 'owner/example', 'example.py', ['../outside.py'],
        snapshot=lambda *a: pytest.fail('network'))
    assert receipt['status'] == 'unverified'


def test_missing_license_and_symlink_do_not_fetch(fixture):
    root, catalog, registry = fixture
    lock = json.loads((root / 'vendor.lock.json').read_text())
    lock['files'].pop()
    registry['consumers'][0]['entries'].pop()
    (root / 'vendor.lock.json').write_text(json.dumps(lock))
    receipt, _ = check(fixture, snapshot=lambda *a: pytest.fail('network'))
    assert receipt['status'] == 'mismatch' and 'LICENSE' in receipt['error']
    target = root / 'vendor/example.py'
    target.unlink()
    target.symlink_to('../test_use.py')
    receipt, _ = check(fixture, snapshot=lambda *a: pytest.fail('network'))
    assert receipt['status'] == 'mismatch' and 'symlink' in receipt['error']


def test_junit_failure_overrides_zero_runner_exit(fixture):
    def run(command, **kwargs):
        Path(command[-1].split('=', 1)[1]).write_text('<testsuites><testsuite errors="1"><testcase name="bad"/></testsuite></testsuites>')
        return subprocess.CompletedProcess(command, 0, b'', b'')
    assert check(fixture, run=run)[0]['status'] == 'mismatch'

"""Exercise standalone push against local bare repositories (no GitHub writes)."""
import json
import subprocess

import pytest
from test_gh_ops import gh_ops


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    work, remote = tmp_path / 'work', tmp_path / 'remote.git'
    work.mkdir()
    git(work, 'init', '-q', '-b', 'feature')
    git(work, 'config', 'user.name', 'Test')
    git(work, 'config', 'user.email', 'test@example.invalid')
    (work / 'a').write_text('one')
    git(work, 'add', 'a'); git(work, 'commit', '-qm', 'initial')
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    git(work, 'remote', 'add', 'origin', str(remote))
    return work, remote


def test_plan_then_push_existing_commit_and_verify(checkout):
    work, remote = checkout
    before = git(work, 'rev-parse', 'HEAD')
    planned = gh_ops.git_push(cwd=str(work))
    assert planned['status'] == 'planned' and not planned['pushed']
    assert git(remote, 'for-each-ref', '--format=%(refname)') == ''
    result = gh_ops.git_push(cwd=str(work), write=True)
    assert result['ok'] and result['status'] == 'verified'
    assert result['head_sha'] == before == git(remote, 'rev-parse', 'refs/heads/feature')
    assert git(work, 'rev-parse', 'HEAD') == before


def test_verify_uses_pushurl_not_fetchurl(checkout, tmp_path):
    work, original = checkout
    push = tmp_path / 'push.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(push)], check=True)
    git(work, 'remote', 'set-url', '--push', 'origin', str(push))
    result = gh_ops.git_push(cwd=str(work), write=True)
    assert result['ok'] and result['verified']
    assert git(original, 'for-each-ref', '--format=%(refname)') == ''
    assert git(push, 'rev-parse', 'refs/heads/feature') == result['head_sha']


def test_rejected_push_is_nonzero_and_does_not_force(checkout, capsys):
    work, remote = checkout
    assert gh_ops.git_push(cwd=str(work), write=True)['ok']
    original = git(work, 'rev-parse', 'HEAD')
    (work / 'a').write_text('two')
    git(work, 'commit', '-qam', 'new remote')
    newer = git(work, 'rev-parse', 'HEAD')
    git(work, 'push', '-q', 'origin', 'HEAD')
    git(work, 'reset', '--hard', original)
    assert gh_ops.main(['--json', 'git-push', '--root', str(work), '--write']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'mutation_uncertain'
    assert git(remote, 'rev-parse', 'refs/heads/feature') == newer


def test_detached_requires_explicit_branch(checkout):
    work, remote = checkout
    git(work, 'checkout', '--detach', '-q')
    with pytest.raises(gh_ops.GhOpsError, match='detached'):
        gh_ops.git_push(cwd=str(work), write=True)
    assert gh_ops.git_push(cwd=str(work), branch='explicit', write=True)['ok']


def test_multiple_push_destinations_refused_before_write(checkout, tmp_path):
    work, remote = checkout
    git(work, 'remote', 'set-url', '--add', '--push', 'origin', str(remote))
    git(work, 'remote', 'set-url', '--add', '--push', 'origin', str(tmp_path / 'other'))
    with pytest.raises(gh_ops.GhOpsError, match='exactly one'):
        gh_ops.git_push(cwd=str(work), write=True)
    assert git(remote, 'for-each-ref', '--format=%(refname)') == ''


def test_push_does_not_follow_tags_from_config(checkout):
    work, remote = checkout
    git(work, 'tag', '-a', 'v1', '-m', 'tag')
    git(work, 'config', 'push.followTags', 'true')
    assert gh_ops.git_push(cwd=str(work), write=True)['ok']
    assert git(remote, 'for-each-ref', '--format=%(refname)', 'refs/tags') == ''


@pytest.mark.parametrize('value', [0, -1, True, float('nan')])
def test_push_invalid_timeout(value):
    with pytest.raises(gh_ops.GhOpsError):
        gh_ops.git_push(timeout=value)


def test_successful_push_readback_failure_preserves_mutation(checkout, monkeypatch):
    work, remote = checkout
    original = gh_ops.gh_identity._git_run
    def run(root, args, **kwargs):
        if args[0] == 'ls-remote':
            raise gh_ops.gh_identity.GitInspectionError('timeout')
        return original(root, args, **kwargs)
    monkeypatch.setattr(gh_ops.gh_identity, '_git_run', run)
    result = gh_ops.git_push(cwd=str(work), write=True)
    assert result['status'] == 'verification_failed' and result['pushed'] is True
    assert result['mutation_status'] == 'succeeded'

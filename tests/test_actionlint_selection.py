"""Execute the reusable workflow's Bash selector against real Git histories."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/reusable-actionlint.yml'


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def write(root, name, text='name: sample\non: push\njobs: {}\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def commit(root):
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'fixture')
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    write(root, '.github/workflows/existing.yml')
    commit(root)
    return root


def select(root, tmp_path, **event):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    step = next(s for s in workflow['jobs']['actionlint']['steps']
                if s.get('id') == 'files')
    selected = tmp_path / 'selected'
    output = tmp_path / 'output'
    # Isolate only the fixed scratch filename; execute the production script.
    script = step['run'].replace('/tmp/actionlint-files', str(selected))
    env = dict(os.environ, EXPLICIT_PATHS='', EVENT_NAME='push', PR_BASE_SHA='',
               PR_HEAD_SHA='', PUSH_BEFORE_SHA='', PUSH_HEAD_SHA=git(root, 'rev-parse', 'HEAD'),
               GITHUB_OUTPUT=str(output))
    env.update(event)
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=root,
                            env=env, text=True, capture_output=True, timeout=20)
    files = selected.read_text().splitlines() if selected.exists() else []
    return result, files, output.read_text() if output.exists() else ''


def test_unavailable_before_lints_all_workflows_in_rewritten_checkout(repo, tmp_path):
    old = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '--orphan', 'rewritten')
    # A workflow introduced before the tip must not be lost by a head-only diff.
    write(repo, '.github/workflows/earlier.yaml')
    commit(repo)
    write(repo, 'README.md', 'latest commit only changes docs\n')
    commit(repo)
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '-q', '--depth=1', '--branch=rewritten',
                    repo.as_uri(), str(checkout)], check=True)
    assert subprocess.run(['git', '-C', str(checkout), 'cat-file', '-e', old],
                          capture_output=True).returncode != 0
    write(checkout, '.github/workflows/untracked.yml')
    result, files, output = select(checkout, tmp_path, PUSH_BEFORE_SHA=old)
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/earlier.yaml', '.github/workflows/existing.yml']
    assert 'count=2' in output
    assert '::warning::Push diff base is unavailable' in result.stdout


def test_available_before_docs_only_selects_nothing(repo, tmp_path):
    before = git(repo, 'rev-parse', 'HEAD')
    write(repo, 'README.md', 'docs\n')
    commit(repo)
    result, files, output = select(repo, tmp_path, PUSH_BEFORE_SHA=before)
    assert result.returncode == 0, result.stderr
    assert files == [] and 'count=0' in output
    assert '::warning::' not in result.stdout


@pytest.mark.parametrize('event', ['push', 'pull_request'])
def test_normal_diff_selects_changed_existing_files_and_skips_deletions(repo, tmp_path, event):
    before = git(repo, 'rev-parse', 'HEAD')
    (repo / '.github/workflows/existing.yml').unlink()
    write(repo, '.github/workflows/new.yaml')
    head = commit(repo)
    result, files, output = select(repo, tmp_path, EVENT_NAME=event,
                                  PUSH_BEFORE_SHA=before, PR_BASE_SHA=before, PR_HEAD_SHA=head)
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/new.yaml'] and 'count=1' in output


def test_new_ref_retains_root_commit_detection(repo, tmp_path):
    result, files, output = select(repo, tmp_path, PUSH_BEFORE_SHA='0' * 40)
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/existing.yml'] and 'count=1' in output


def test_missing_head_is_not_silently_accepted(repo, tmp_path):
    result, _, output = select(repo, tmp_path, PUSH_BEFORE_SHA='1' * 40, PUSH_HEAD_SHA='2' * 40)
    assert result.returncode != 0
    assert 'count=' not in output


def test_explicit_paths_keep_precedence(repo, tmp_path):
    result, files, output = select(repo, tmp_path, EVENT_NAME='workflow_dispatch',
                                  EXPLICIT_PATHS='.github/workflows/existing.yml missing.yml')
    assert result.returncode == 0, result.stderr
    assert files == ['.github/workflows/existing.yml'] and 'count=1' in output

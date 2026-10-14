"""Execute the actual reusable classifier with controlled Git diff output."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def classify(tmp_path, files, *, event='pull_request', forced='', fail=False):
    doc = yaml.safe_load((ROOT / '.github/workflows/reusable-change-detection.yml').read_text())
    script = doc['jobs']['changes']['steps'][1]['run']
    paths = tmp_path / 'paths'
    paths.write_bytes(b''.join(p.encode() + b'\0' for p in files))
    git = tmp_path / 'git'
    git.write_text('#!/bin/bash\n' + ('exit 128\n' if fail else 'cat "$FAKE_PATHS"\n'))
    git.chmod(0o755)
    out = tmp_path / 'output'
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'],
               FAKE_PATHS=str(paths), GITHUB_OUTPUT=str(out), RUNNER_TEMP=str(tmp_path),
               EVENT_NAME=event, PR_BASE_SHA='a'*40, PR_HEAD_SHA='b'*40,
               PUSH_BEFORE_SHA='a'*40, PUSH_HEAD_SHA='b'*40, FORCE_HEAVY_PATHS=forced)
    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True)
    values = dict(line.split('=', 1) for line in out.read_text().splitlines()) if out.exists() else {}
    return result, values


@pytest.mark.parametrize('files,product,docs,workflow', [
    (['docs/guide.md'], False, True, False),
    (['templates/parent-pr.ref.json'], False, False, False),
    (['.github/workflows/vendor-child-ci.yml'], False, False, True),
    (['templates/parent-pr.ref.json', '.github/workflows/vendor-child-ci.yml', 'README.md'], False, False, True),
    (['vendor/tool.py'], True, False, False),
    (['vendor.lock.json'], True, False, False),
    (['requirements.txt'], True, False, False),
    (['pyproject.toml'], True, False, False),
    (['tool.py'], True, False, False),
    (['.github/workflows/ci.yml'], True, False, True),
    (['templates/parent-pr.ref.json', 'vendor/tool.py'], True, False, False),
    (['unknown.json'], True, False, False),
    (['docs/looks-safe.md\nunsafe.py'], True, False, False),
    ([], True, False, False),
])
def test_exact_rollout_paths_and_legacy_outputs(tmp_path, files, product, docs, workflow):
    result, values = classify(tmp_path, files)
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == str(product).lower()
    assert values['docs_only'] == str(docs).lower()
    assert values['non_docs_changed'] == str(not docs).lower()
    assert values['workflow_changed'] == str(workflow).lower()


def test_force_override_and_manual_keep_product_tests(tmp_path):
    result, values = classify(tmp_path, ['templates/parent-pr.ref.json'], forced='templates')
    assert result.returncode == 0 and values['product_changed'] == 'true'
    result, values = classify(tmp_path, [], event='workflow_dispatch')
    assert result.returncode == 0 and values['product_changed'] == 'true'


def test_diff_failure_cannot_claim_product_skip(tmp_path):
    result, values = classify(tmp_path, [], fail=True)
    assert result.returncode != 0
    assert values.get('product_changed') != 'false'

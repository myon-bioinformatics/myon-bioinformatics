"""Execute the actual reusable classifier with controlled Git diff output."""
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def classify(tmp_path, files, *, event='pull_request', forced='', fail=False, missing_base=False,
            push_forced=False, push_before_sha=None):
    doc = yaml.safe_load((ROOT / '.github/workflows/reusable-change-detection.yml').read_text())
    script = doc['jobs']['changes']['steps'][1]['run']
    paths = tmp_path / 'paths'
    paths.write_bytes(b''.join(p.encode() + b'\0' for p in files))
    git = tmp_path / 'git'
    git.write_text(
        '#!/bin/bash\n'
        'set -euo pipefail\n'
        'if [[ "$1" == "cat-file" && "$2" == "-e" ]]; then\n'
        '  [[ "${FAKE_MISSING_BASE:-}" == "1" ]] && exit 1\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1" == "fetch" ]]; then exit 0; fi\n'
        'if [[ "$1" == "diff" ]]; then\n'
        '  [[ "${FAKE_DIFF_FAIL:-}" == "1" ]] && exit 128\n'
        '  cat "$FAKE_PATHS"\n'
        '  exit 0\n'
        'fi\n'
        'exit 0\n'
    )
    git.chmod(0o755)
    out = tmp_path / 'output'
    before = push_before_sha if push_before_sha is not None else 'a' * 40
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'],
               FAKE_PATHS=str(paths), GITHUB_OUTPUT=str(out), RUNNER_TEMP=str(tmp_path),
               EVENT_NAME=event, PR_BASE_SHA='a'*40, PR_HEAD_SHA='b'*40,
               PUSH_BEFORE_SHA=before, PUSH_HEAD_SHA='b'*40, FORCE_HEAVY_PATHS=forced,
               PUSH_FORCED='true' if push_forced else 'false',
               FAKE_MISSING_BASE='1' if missing_base else '0',
               FAKE_DIFF_FAIL='1' if fail else '0')
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
    assert result.returncode == 0
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'


def test_all_zero_push_before_fails_open_with_workflow_lint(tmp_path):
    result, values = classify(
        tmp_path, [], event='push', push_before_sha='0' * 40,
    )
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'


def test_diff_failure_cannot_claim_product_skip(tmp_path):
    result, values = classify(tmp_path, [], fail=True)
    assert result.returncode != 0
    assert values.get('product_changed') != 'false'


def test_missing_push_base_fails_open_with_workflow_lint(tmp_path):
    result, values = classify(tmp_path, [], event='push', missing_base=True)
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'


def test_forced_push_fails_open_with_workflow_lint(tmp_path):
    result, values = classify(tmp_path, ['docs/guide.md'], event='push', push_forced=True)
    assert result.returncode == 0, result.stderr
    assert values['product_changed'] == 'true'
    assert values['workflow_changed'] == 'true'

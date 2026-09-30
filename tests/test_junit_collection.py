"""Execute the actual reusable-workflow importer against local artifacts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

WORKFLOW = Path(__file__).parents[1] / '.github/workflows/reusable-junit-identity.yml'
if 'XPROBE_SOURCE' not in os.environ:
    pytest.skip('Run the collector lane with its pinned XPROBE_SOURCE', allow_module_level=True)
SOURCE = Path(os.environ['XPROBE_SOURCE'])


def run_collection(tmp_path, reports, expected_artifacts=None):
    importer = tmp_path / 'shared-xprobe'
    importer.mkdir()
    shutil.copyfile(SOURCE, importer / 'xprobe.py')
    for name, data in reports.items():
        path = tmp_path / 'reports' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    text = WORKFLOW.read_text()
    code = text.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
    code = '\n'.join(line[10:] for line in code.splitlines())
    if expected_artifacts is None:
        expected_artifacts = sorted({Path(name).parts[0] for name in reports})
    artifact_names = sorted({Path(name).parts[0] for name in reports})
    env = dict(
        os.environ,
        PYTHONPATH=str(importer),
        SOURCE_REPOSITORY='owner/repo',
        ARTIFACT_PATTERN='*',
        EXPECTED_ARTIFACTS='\n'.join(expected_artifacts),
        FOUND_ARTIFACTS_JSON=json.dumps({
            'total_count': len(artifact_names),
            'names': artifact_names,
        }),
    )
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
    )
    summary = json.loads((tmp_path / 'identity/collection.json').read_text())
    cases = [
        json.loads(line)
        for line in (tmp_path / 'identity/failure-identity.jsonl').read_text().splitlines()
    ]
    return result, summary, cases


def test_failures_preserve_provenance_and_redact(tmp_path):
    xml = b'<testsuite><testcase classname="a" name="b[secret]"><failure message="secret">secret</failure></testcase><testcase name="c"><error>secret</error></testcase><testcase name="skip"><skipped type="pytest.xfail">secret</skipped></testcase></testsuite>'
    result, summary, cases = run_collection(
        tmp_path,
        {'job1/result.xml': xml, 'job2/result.xml': xml},
    )
    assert result.returncode == 0, result.stderr
    assert summary['complete'] and len(cases) == 4
    assert summary['expected_artifacts'] == 2
    assert summary['found_artifacts'] == 2
    assert summary['missing_artifacts'] == []
    assert summary['unexpected_artifact_count'] == 0
    assert {row['value']['kind'] for row in cases} == {'failure', 'error'}
    assert len({row['id'] for row in cases}) == 4
    assert all(row['context']['commit_sha'] is None for row in cases)
    assert 'secret' not in json.dumps([summary, cases])


def test_missing_expected_report_is_not_complete(tmp_path):
    xml = b'<testsuite/>'
    result, summary, cases = run_collection(
        tmp_path,
        {f'junit-py3.{version}/result.xml': xml for version in range(9, 14)},
        expected_artifacts=[f'junit-py3.{version}' for version in range(9, 15)],
    )
    assert result.returncode == 1
    assert not summary['complete']
    assert summary['expected_artifacts'] == 6
    assert summary['found_artifacts'] == 5
    assert summary['missing_artifacts'] == ['junit-py3.14']
    assert summary['unexpected_artifact_count'] == 0
    assert cases == []


def test_unexpected_artifact_is_not_complete(tmp_path):
    xml = b'<testsuite/>'
    result, summary, cases = run_collection(
        tmp_path,
        {'job1/result.xml': xml, 'job-extra/result.xml': xml},
        expected_artifacts=['job1'],
    )
    assert result.returncode == 1
    assert not summary['complete']
    assert summary['missing_artifacts'] == []
    assert summary['unexpected_artifact_count'] == 1
    assert cases == []


def test_each_artifact_must_contain_exactly_one_xml(tmp_path):
    xml = b'<testsuite/>'
    result, summary, cases = run_collection(
        tmp_path,
        {'job1/a.xml': xml, 'job1/b.xml': xml},
        expected_artifacts=['job1'],
    )
    assert result.returncode == 1
    assert not summary['complete']
    assert summary['reports'] == [{
        'artifact': 'job1',
        'error': 'report_cardinality',
        'measured': False,
        'report_count': 2,
    }]
    assert cases == []


def test_no_report_is_not_success(tmp_path):
    result, summary, cases = run_collection(
        tmp_path,
        {},
        expected_artifacts=['job1'],
    )
    assert result.returncode == 1
    assert not summary['complete']
    assert summary['missing_artifacts'] == ['job1']
    assert cases == []


def test_expected_artifacts_are_required_and_unique(tmp_path):
    result, summary, cases = run_collection(tmp_path, {}, expected_artifacts=[])
    assert result.returncode == 1
    assert summary['error'] == 'expected_artifacts_required'
    assert cases == []

    duplicate = tmp_path / 'duplicate'
    duplicate.mkdir()
    result, summary, cases = run_collection(
        duplicate,
        {'job1/result.xml': b'<testsuite/>'},
        expected_artifacts=['job1', 'job1'],
    )
    assert result.returncode == 1
    assert summary['error'] == 'duplicate_expected_artifact'
    assert cases == []


def test_malformed_and_oversized_are_not_silent(tmp_path):
    result, summary, cases = run_collection(
        tmp_path,
        {'a/result.xml': b'not XML secret', 'b/result.xml': b'x' * 1_000_001},
    )
    assert result.returncode == 1 and not summary['complete'] and cases == []
    assert all(not row['measured'] for row in summary['reports'])
    assert 'secret' not in json.dumps(summary)


def test_truncated_cases_are_recorded(tmp_path):
    xml = b'<testsuite>' + b'<testcase name="b"><failure/></testcase>' * 1001 + b'</testsuite>'
    result, summary, cases = run_collection(tmp_path, {'a/result.xml': xml})
    assert result.returncode == 1
    assert summary['reports'][0]['truncated']
    assert len(cases) == 1000


def test_expected_artifact_count_is_bounded(tmp_path):
    expected = [f'job{i}' for i in range(101)]
    result, summary, cases = run_collection(tmp_path, {}, expected_artifacts=expected)
    assert result.returncode == 1
    assert summary['error'] == 'expected_artifact_limit'
    assert cases == []

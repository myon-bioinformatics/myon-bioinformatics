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


def run_collection(tmp_path, reports, expected=None):
    reports = {name if '/' in name else 'fixture/' + name: data for name, data in reports.items()}
    if expected is None:
        expected = list(reports)[:100] or ['fixture/report.xml']
    importer = tmp_path / 'shared-xprobe'
    importer.mkdir()
    shutil.copyfile(SOURCE, importer / 'xprobe.py')
    for name, data in reports.items():
        path = tmp_path / 'reports' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    text = WORKFLOW.read_text().split('      - name: Import bounded reports', 1)[1]
    code = text.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
    code = '\n'.join(line[10:] for line in code.splitlines())
    env = dict(os.environ, PYTHONPATH=str(importer), SOURCE_REPOSITORY='owner/repo', EXPECTED_REPORTS=json.dumps(expected))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env, capture_output=True)
    summary = json.loads((tmp_path / 'identity/collection.json').read_text())
    cases = [json.loads(line) for line in (tmp_path / 'identity/failure-identity.jsonl').read_text().splitlines()]
    return result, summary, cases


def test_failures_preserve_provenance_and_redact(tmp_path):
    xml = b'<testsuite><testcase classname="a" name="b[secret]"><failure message="secret">secret</failure></testcase><testcase name="c"><error>secret</error></testcase><testcase name="skip"><skipped type="pytest.xfail">secret</skipped></testcase></testsuite>'
    result, summary, cases = run_collection(tmp_path, {'job1/result.xml': xml, 'job2/result.xml': xml})
    assert result.returncode == 0, result.stderr
    assert summary['complete'] and len(cases) == 4
    assert {row['value']['kind'] for row in cases} == {'failure', 'error'}
    assert len({row['id'] for row in cases}) == 4
    assert all(row['context']['commit_sha'] is None for row in cases)
    assert 'secret' not in json.dumps([summary, cases])


def test_no_report_is_not_success(tmp_path):
    result, summary, cases = run_collection(tmp_path, {})
    assert result.returncode == 1 and summary['error'] == 'no_reports' and cases == []


def test_malformed_and_oversized_are_not_silent(tmp_path):
    result, summary, cases = run_collection(tmp_path, {'a.xml': b'not XML secret', 'b.xml': b'x' * 1_000_001})
    assert result.returncode == 1 and not summary['complete'] and cases == []
    assert all(not row['measured'] for row in summary['reports'])
    assert 'secret' not in json.dumps(summary)


def test_truncated_cases_are_recorded(tmp_path):
    xml = b'<testsuite>' + b'<testcase name="b"><failure/></testcase>' * 1001 + b'</testsuite>'
    result, summary, cases = run_collection(tmp_path, {'a.xml': xml})
    assert result.returncode == 1 and summary['reports'][0]['truncated'] and len(cases) == 1000


def test_report_count_is_bounded(tmp_path):
    result, summary, cases = run_collection(tmp_path, {f'{i}.xml': b'<testsuite/>' for i in range(101)})
    assert result.returncode == 1 and summary['limit'] == 'report_count'
    assert len(summary['reports']) == 100 and not cases


def test_partial_matrix_is_incomplete(tmp_path):
    expected = [f'junit-py3.{i}/pytest-3.{i}.xml' for i in range(9, 15)]
    result, summary, _ = run_collection(tmp_path, {name: b'<testsuite><testcase name="ok"/></testsuite>' for name in expected[:-1]}, expected)
    assert result.returncode == 1 and not summary['complete']
    assert summary['missing_reports'] == [expected[-1]]


def test_full_matrix_is_complete(tmp_path):
    expected = [f'junit-py3.{i}/pytest-3.{i}.xml' for i in range(9, 15)]
    result, summary, _ = run_collection(tmp_path, {name: b'<testsuite><testcase name="ok"/></testsuite>' for name in expected}, expected)
    assert result.returncode == 0 and summary['complete']
    assert summary['missing_reports'] == summary['unexpected_reports'] == []


def test_extra_cannot_replace_missing_report(tmp_path):
    result, summary, _ = run_collection(tmp_path, {'job1/a.xml': b'<testsuite/>', 'stray/b.xml': b'<testsuite/>'}, ['job1/a.xml', 'job2/b.xml'])
    assert result.returncode == 1 and summary['missing_reports'] == ['job2/b.xml']
    assert len(summary['unexpected_reports']) == 1
    assert 'stray' not in json.dumps(summary)


def test_extra_with_complete_expected_set_still_fails(tmp_path):
    result, summary, _ = run_collection(tmp_path, {'job/a.xml': b'<testsuite/>', 'job/b.xml': b'<testsuite/>'}, ['job/a.xml'])
    assert result.returncode == 1 and not summary['missing_reports']
    assert len(summary['unexpected_reports']) == 1


@pytest.mark.parametrize('expected', [[], ['job/a.xml', 'job/a.xml'], ['../a.xml'], ['job/../a.xml'], 'job/a.xml', None])
def test_invalid_expectation_fails_closed(tmp_path, expected):
    # JSON null is supplied explicitly instead of invoking helper defaults.
    if expected is None:
        expected = {'unexpected': 'shape'}
    result, summary, _ = run_collection(tmp_path, {'job/a.xml': b'<testsuite/>'}, expected)
    assert result.returncode == 1 and summary['error'] == 'invalid_expected_reports'


@pytest.mark.parametrize('xml', [b'', b'<bad/>', b'<testsuite>' + b'<testcase><failure/></testcase>' * 1001 + b'</testsuite>'])
def test_invalid_or_truncated_report_does_not_fill_slot(tmp_path, xml):
    result, summary, _ = run_collection(tmp_path, {'job/a.xml': xml}, ['job/a.xml'])
    assert result.returncode == 1 and summary['missing_reports'] == ['job/a.xml']


@pytest.mark.parametrize('expected,name', [(['one/a.xml'], 'one'), (['one/a.xml', 'one/b.xml'], 'one'), (['one/a.xml', 'two/a.xml'], '')])
def test_artifact_layout_uses_exact_name_only_for_one_artifact(tmp_path, expected, name):
    text = WORKFLOW.read_text().split('      - name: Select single-artifact layout', 1)[1]
    code = text.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
    code = '\n'.join(line[10:] for line in code.splitlines())
    output = tmp_path / 'output'
    env = dict(os.environ, EXPECTED_REPORTS=json.dumps(expected), GITHUB_OUTPUT=str(output))
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True)
    assert result.returncode == 0
    assert output.read_text() == 'single_artifact=' + name + '\n'

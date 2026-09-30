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


def run_collection(tmp_path, reports):
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
    env = dict(os.environ, PYTHONPATH=str(importer), SOURCE_REPOSITORY='owner/repo')
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

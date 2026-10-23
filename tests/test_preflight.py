import json
from pathlib import Path
import subprocess
import sys

import pytest
import preflight


def test_observation_uses_adopted_ghi_and_keeps_no_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.ghi, 'capabilities', lambda: {'git': True, 'gh': False, 'token_present': False})
    monkeypatch.setattr(preflight.ghi, 'git_status', lambda root: {'clean': False, 'truncated': False,
                                                               'records': [{'path': 'sensitive.txt'}]})
    value = preflight.observe(tmp_path, required=['git', 'git', 'gh'], require_clean=True, require_token=True)
    assert not value['ready']
    assert [r['check'] for r in value['checks']] == ['git', 'gh', 'clean_worktree', 'token_present']
    assert value['worktree']['changed_records'] == 1
    assert 'sensitive.txt' not in json.dumps(value)


def test_runtime_facts_are_whitelisted_and_not_live(tmp_path, monkeypatch):
    path = tmp_path / 'facts.json'
    data = {'schema_version': 1, 'os': {'system': 'Linux', 'release': 'test', 'machine': 'x86_64'},
            'runtime': {'implementation': 'CPython', 'version': '3.9.0', 'bits': 64},
            'host': {'name': 'private'}, 'directory': {'entries': ['secret']}}
    path.write_text(json.dumps(data))
    facts = preflight.runtime_facts(path)
    assert set(facts) == {'os', 'runtime'}
    monkeypatch.setattr(preflight.ghi, 'capabilities', lambda: {})
    monkeypatch.setattr(preflight.ghi, 'git_status', lambda root: {'clean': True, 'truncated': False, 'records': []})
    result = preflight.observe(tmp_path, python_min='3', reported_runtime=facts)
    assert result['ready']
    assert result['reported_runtime']['freshness'] == 'not_verified'
    assert 'private' not in json.dumps(result)
    for invalid in ([], {}, {**data, 'schema_version': True}, {**data, 'runtime': {}},
                    {**data, 'os': {**data['os'], 'system': '\nsecret'}}):
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError): preflight.runtime_facts(path)
    path.write_bytes(b' ' * 65537)
    with pytest.raises(ValueError): preflight.runtime_facts(path)


def test_stdlib_cli_exit_codes_with_real_git(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    command = [sys.executable, '-S', str(Path(preflight.__file__)), '--root', str(tmp_path), '--require', 'git']
    result = subprocess.run(command + ['--require-clean', '--python-min', '3'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ready']
    (tmp_path / 'untracked').write_text('x')
    result = subprocess.run(command + ['--require-clean'], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['worktree']['changed_records'] == 1
    for args in (['--python-min', '999'], ['--python-min', 'bad'],
                 ['--runtime-facts', str(tmp_path / 'missing')]):
        result = subprocess.run(command + args, capture_output=True)
        assert result.returncode == (1 if args == ['--python-min', '999'] else 2)
    result = subprocess.run(command[:4] + [str(tmp_path / 'missing')], capture_output=True)
    assert result.returncode == 2
    nested = tmp_path / 'nested.json'
    nested.write_bytes(b'[' * 10000 + b'0' + b']' * 10000)
    result = subprocess.run(command + ['--runtime-facts', str(nested)], capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stdout == ''
    assert result.stderr == 'preflight: ValueError\n'
    explained = subprocess.run(command + ['--runtime-facts', str(nested), '--explain'],
                               capture_output=True, text=True)
    assert explained.returncode == 2 and explained.stdout == ''
    assert explained.stderr.startswith('preflight: ValueError: ')
    assert 'nesting' in explained.stderr or 'too deep' in explained.stderr

"""No installation occurs until both release identities have been checked."""
import hashlib, json, subprocess
from pathlib import Path; from unittest.mock import Mock

import pytest
from scripts.install_actions_lock import install, ROOT


@pytest.fixture(autouse=True)
def supported_platform(monkeypatch):
    monkeypatch.setattr('scripts.install_actions_lock.platform.system', lambda: 'Linux')
    monkeypatch.setattr('scripts.install_actions_lock.platform.machine', lambda: 'x86_64')


@pytest.fixture
def manifest(tmp_path):
    p = tmp_path / 'tool.json'
    p.write_text((ROOT / 'actions-lock-tool.json').read_text())
    return p


def responses(config, wrong=None):
    return [subprocess.CompletedProcess([], 0, json.dumps({'object': {
        'type': kind, 'sha': '0' * 40 if wrong == index else config[key]}}))
        for index, (kind, key) in enumerate([('tag', 'tag_sha'), ('commit', 'commit_sha')])]


def test_install_checks_tag_and_commit_before_installing(manifest, tmp_path, monkeypatch):
    prepare_binary(manifest, tmp_path, monkeypatch)
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    install(manifest, run)
    calls = [c.args[0] for c in run.call_args_list]
    assert calls == [
        ['gh', 'api', 'repos/github/gh-actions-lock/git/ref/tags/' + config['version']],
        ['gh', 'api', 'repos/github/gh-actions-lock/git/tags/' + config['tag_sha']],
        ['gh', 'extension', 'install', 'github/gh-actions-lock', '--pin', config['version']],
    ]


@pytest.mark.parametrize('wrong', [0, 1])
def test_moved_release_never_installs(manifest, wrong):
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config, wrong))
    with pytest.raises(ValueError, match='changed'):
        install(manifest, run)
    assert not any('extension' in c.args[0] for c in run.call_args_list)


@pytest.mark.parametrize('field,value', [('version', 'main'), ('tag_sha', 'bad'),
                                         ('commit_sha', None), ('linux_amd64_sha256', 'bad'), ('extra', 1)])
def test_invalid_config_never_calls_github(manifest, field, value):
    config = json.loads(manifest.read_text()); config[field] = value
    manifest.write_text(json.dumps(config))
    run = Mock()
    with pytest.raises(ValueError):
        install(manifest, run)
    run.assert_not_called()


def test_api_failure_never_installs(manifest):
    run = Mock(side_effect=subprocess.CalledProcessError(1, ['gh', 'api']))
    with pytest.raises(subprocess.CalledProcessError):
        install(manifest, run)
    assert run.call_count == 1


def prepare_binary(manifest, tmp_path, monkeypatch, contents=b'reviewed test binary'):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    binary = tmp_path / 'data/gh/extensions/gh-actions-lock/gh-actions-lock'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(contents)
    config = json.loads(manifest.read_text())
    config['linux_amd64_sha256'] = hashlib.sha256(b'reviewed test binary').hexdigest()
    manifest.write_text(json.dumps(config))
    return binary


def test_changed_binary_fails_before_any_extension_execution(manifest, tmp_path, monkeypatch):
    prepare_binary(manifest, tmp_path, monkeypatch, b'unreviewed bytes')
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    with pytest.raises(ValueError, match='binary checksum mismatch'):
        install(manifest, run)
    assert len(run.call_args_list) == 3
    assert all(c.args[0][1] != 'actions-lock' for c in run.call_args_list)


def test_missing_binary_fails_closed(manifest, tmp_path, monkeypatch):
    prepare_binary(manifest, tmp_path, monkeypatch).unlink()
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    with pytest.raises(FileNotFoundError):
        install(manifest, run)


def test_unsupported_platform_does_not_install(manifest, monkeypatch):
    monkeypatch.setattr('scripts.install_actions_lock.platform.machine', lambda: 'aarch64')
    run = Mock()
    with pytest.raises(ValueError, match='linux-amd64'):
        install(manifest, run)
    run.assert_not_called()


def test_default_data_home_matches_gh(manifest, tmp_path, monkeypatch):
    binary = prepare_binary(manifest, tmp_path, monkeypatch)
    home = tmp_path / 'home'
    destination = home / '.local/share/gh/extensions/gh-actions-lock/gh-actions-lock'
    destination.parent.mkdir(parents=True)
    destination.write_bytes(binary.read_bytes())
    monkeypatch.delenv('XDG_DATA_HOME')
    monkeypatch.setattr('scripts.install_actions_lock.Path.home', lambda: home)
    config = json.loads(manifest.read_text())
    run = Mock(side_effect=responses(config) + [subprocess.CompletedProcess([], 0)])
    install(manifest, run)

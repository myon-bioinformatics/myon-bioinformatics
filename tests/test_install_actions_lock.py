"""No installation occurs until both release identities have been checked."""
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest
from scripts.install_actions_lock import install, ROOT


@pytest.fixture
def manifest(tmp_path):
    p = tmp_path / 'tool.json'
    p.write_text((ROOT / 'actions-lock-tool.json').read_text())
    return p


def responses(config, wrong=None):
    return [subprocess.CompletedProcess([], 0, json.dumps({'object': {
        'type': kind, 'sha': '0' * 40 if wrong == index else config[key]}}))
        for index, (kind, key) in enumerate([('tag', 'tag_sha'), ('commit', 'commit_sha')])]


def test_install_checks_tag_and_commit_before_installing(manifest):
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
                                         ('commit_sha', None), ('extra', 1)])
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

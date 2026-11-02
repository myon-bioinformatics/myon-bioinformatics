#!/usr/bin/env python3
"""Install the reviewed official Actions lock generator from one shared manifest."""
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'github/gh-actions-lock'


def install(manifest=ROOT / 'actions-lock-tool.json', run=subprocess.run):
    config = json.loads(Path(manifest).read_text(encoding='utf-8'))
    if not isinstance(config, dict) or set(config) != {'version', 'tag_sha', 'commit_sha', 'linux_amd64_sha256'}:
        raise ValueError('invalid Actions lock tool manifest')
    for name, pattern in [('version', r'v\d+\.\d+\.\d+'),
                          ('tag_sha', r'[0-9a-f]{40}'),
                          ('commit_sha', r'[0-9a-f]{40}'),
                          ('linux_amd64_sha256', r'[0-9a-f]{64}')]:
        if not isinstance(config[name], str) or not re.fullmatch(pattern, config[name]):
            raise ValueError(f'invalid {name}')

    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise ValueError('only the reviewed linux-amd64 generator asset is supported')

    def query(endpoint):
        result = run(['gh', 'api', f'repos/{REPOSITORY}/{endpoint}'],
                     check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        obj = data.get('object') if isinstance(data, dict) else None
        if not isinstance(obj, dict):
            raise ValueError('GitHub response missing tag object')
        return obj.get('type'), obj.get('sha')

    if query(f"git/ref/tags/{config['version']}") != ('tag', config['tag_sha']):
        raise ValueError('generator release tag changed; review manifest before updating')
    if query(f"git/tags/{config['tag_sha']}") != ('commit', config['commit_sha']):
        raise ValueError('generator release commit changed; review manifest before updating')
    run(['gh', 'extension', 'install', REPOSITORY, '--pin', config['version']], check=True)
    # gh installs without executing the extension. Its Linux data directory is
    # XDG_DATA_HOME/gh, otherwise ~/.local/share/gh (not GH_CONFIG_DIR).
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    binary = data_home / 'gh/extensions/gh-actions-lock/gh-actions-lock'
    if hashlib.sha256(binary.read_bytes()).hexdigest() != config['linux_amd64_sha256']:
        raise ValueError('generator binary checksum mismatch; refusing execution')


if __name__ == '__main__':
    install()

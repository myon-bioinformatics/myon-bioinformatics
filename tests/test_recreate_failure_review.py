"""Failure-injection characterization of recreate; every filesystem is temporary."""
from pathlib import Path
import json
import pytest
import vendor_sync as sync
from test_vendor_sync import lock, entry, _staging_left


def setup(root):
    path, value = lock(root, [entry(), entry(b'two\n', source='scripts/two.py', destination='vendor/two.py')])
    (root / 'vendor').mkdir()
    (root / 'vendor/adapter.py').write_bytes(b'old\n')
    (root / 'vendor/two.py').write_bytes(b'two\n')
    return path, value


def get(url):
    return b'two\n' if url.endswith('/two.py') else b'old\n'


def test_middle_replace_failure_is_partial_but_rerunnable(tmp_path, monkeypatch):
    path, _ = setup(tmp_path)
    first, second = tmp_path/'vendor/adapter.py', tmp_path/'vendor/two.py'
    before = first.stat().st_ino, second.stat().st_ino
    real = sync.os.replace
    def replace(src, dst):
        if Path(dst) == second:
            raise OSError('injected second replacement failure')
        return real(src, dst)
    with monkeypatch.context() as m:
        m.setattr(sync.os, 'replace', replace)
        assert sync.main(['recreate', '--root', str(tmp_path), '--manifest', path.name], get=get) == 2
    assert first.stat().st_ino != before[0] and second.stat().st_ino == before[1]
    assert first.read_bytes() == b'old\n' and second.read_bytes() == b'two\n'
    assert _staging_left(tmp_path) == []
    assert sync.recreate(path.name, tmp_path, get=get)['recreated'] == ['vendor/adapter.py', 'vendor/two.py']


def test_final_check_failure_does_not_rollback(tmp_path, monkeypatch):
    path, _ = setup(tmp_path)
    original = sync.synchronize
    root_checks = 0
    def synchronize(manifest, root, mode, **kwargs):
        nonlocal root_checks
        if Path(root) == tmp_path and mode == 'check':
            root_checks += 1
            if root_checks == 3:
                (tmp_path/'vendor/adapter.py').write_bytes(b'concurrent final edit\n')
        return original(manifest, root, mode, **kwargs)
    monkeypatch.setattr(sync, 'synchronize', synchronize)
    with pytest.raises(ValueError, match='digest mismatch'):
        sync.recreate(path.name, tmp_path, get=get)
    assert (tmp_path/'vendor/adapter.py').read_bytes() == b'concurrent final edit\n'
    assert _staging_left(tmp_path) == []


def test_edit_during_download_is_preserved(tmp_path):
    path, _ = setup(tmp_path)
    def download(url):
        (tmp_path/'vendor/adapter.py').write_bytes(b'user edit\n')
        return get(url)
    with pytest.raises(ValueError, match='digest mismatch'):
        sync.recreate(path.name, tmp_path, get=download)
    assert (tmp_path/'vendor/adapter.py').read_bytes() == b'user edit\n'


def test_manifest_edit_during_download_is_rejected(tmp_path):
    path, value = setup(tmp_path)
    def download(url):
        path.write_text(json.dumps(value, indent=2)+'\n')
        return get(url)
    with pytest.raises(ValueError, match='manifest changed'):
        sync.recreate(path.name, tmp_path, get=download)
    assert path.read_text() == json.dumps(value, indent=2)+'\n'


def test_edit_to_later_target_after_first_replace_is_preserved(tmp_path, monkeypatch):
    path, _ = setup(tmp_path)
    real = sync.os.replace
    def replace(src, dst):
        result = real(src, dst)
        if Path(dst) == tmp_path/'vendor/adapter.py':
            (tmp_path/'vendor/two.py').write_bytes(b'concurrent second edit\n')
        return result
    monkeypatch.setattr(sync.os, 'replace', replace)
    with pytest.raises(ValueError, match='digest mismatch'):
        sync.recreate(path.name, tmp_path, get=get)
    assert (tmp_path/'vendor/two.py').read_bytes() == b'concurrent second edit\n'
    assert _staging_left(tmp_path) == []


def test_write_inside_replace_window_remains_unsupported(tmp_path, monkeypatch):
    """No compare-and-swap guarantee: the last check cannot close this race."""
    path, _ = setup(tmp_path)
    real = sync.os.replace
    def replace(src, dst):
        if Path(dst) == tmp_path/'vendor/adapter.py':
            Path(dst).write_bytes(b'edit after last verification\n')
        return real(src, dst)
    monkeypatch.setattr(sync.os, 'replace', replace)
    sync.recreate(path.name, tmp_path, get=get)
    assert (tmp_path/'vendor/adapter.py').read_bytes() == b'old\n'

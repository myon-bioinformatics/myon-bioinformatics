"""Count actual snapshot requests while retaining immutable revision boundaries."""
import pytest
import vendor_sync as sync

SHA = 'a' * 40


def test_overlapping_subsets_share_blobs_but_not_across_audits(monkeypatch):
    calls = []
    def read(repo, ref, paths, *, remote=None):
        calls.append((repo, ref, tuple(paths), remote))
        return ref, {p: ('b' * 40, p.encode()) for p in paths}
    monkeypatch.setattr(sync, '_read_public_git_snapshot', read)
    @sync._parent_fetch_scope
    def audit():
        _, first = sync._public_git_snapshot('owner/repo', SHA, ['a', 'LICENSE'])
        first.clear()  # Caller changes cannot alter the cache.
        _, second = sync._public_git_snapshot('owner/repo', SHA, ['a', 'b'])
        assert second['a'][1] == b'a'
        assert sync._public_git_snapshot('owner/repo', SHA, ['LICENSE'])[1]['LICENSE'][1] == b'LICENSE'
    audit()
    audit()
    assert [c[2] for c in calls] == [('LICENSE', 'a'), ('b',), ('LICENSE', 'a'), ('b',)]
    assert sync._PARENT_FETCH_CACHE.get() is None


@pytest.mark.parametrize('failure', ['exception', 'revision', 'missing'])
def test_incomplete_snapshot_is_not_cached(monkeypatch, failure):
    calls = []
    def read(repo, ref, paths, *, remote=None):
        calls.append(1)
        if len(calls) == 1:
            if failure == 'exception':
                raise ValueError('fetch failed')
            if failure == 'revision':
                return 'c' * 40, {'a': ('b' * 40, b'wrong')}
            return ref, {}
        return ref, {'a': ('b' * 40, b'right')}
    monkeypatch.setattr(sync, '_read_public_git_snapshot', read)
    @sync._parent_fetch_scope
    def audit():
        with pytest.raises((ValueError, KeyError)):
            sync._public_git_snapshot('owner/repo', SHA, ['a'])
        assert sync._public_git_snapshot('owner/repo', SHA, ['a'])[1]['a'][1] == b'right'
    audit()
    assert len(calls) == 2


def test_cache_key_separates_repository_revision_and_remote(monkeypatch):
    calls = []
    def read(repo, ref, paths, *, remote=None):
        calls.append((repo, ref, remote))
        return ref, {'a': ('b' * 40, str(calls[-1]).encode())}
    monkeypatch.setattr(sync, '_read_public_git_snapshot', read)
    @sync._parent_fetch_scope
    def audit():
        for repo, ref, remote in [('o/a', SHA, None), ('o/b', SHA, None),
                                  ('o/a', 'c' * 40, None), ('o/a', SHA, '/local')]:
            sync._public_git_snapshot(repo, ref, ['a'], remote=remote)
    audit()
    assert len(calls) == 4


def test_moving_refs_and_unscoped_calls_are_never_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, '_read_public_git_snapshot',
                        lambda *a, **kw: calls.append(a) or (SHA, {}))
    for _ in range(2):
        sync._public_git_snapshot('o/r', SHA, [])
    @sync._parent_fetch_scope
    def audit():
        for _ in range(2):
            sync._public_git_snapshot('o/r', 'main', [])
    audit()
    assert len(calls) == 4


def test_real_git_batch_tree_preserves_literal_paths_modes_sizes_and_bytes(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / 'source'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    paths = ['a.py', 'nested/日本語.py', 'literal[1].py', 'with space.py']
    for name in paths:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('data: ' + name + '\n').encode())
    git('add', '.')
    git('update-index', '--chmod=+x', 'a.py')
    git('commit', '-m', 'fixture')
    commit = git('rev-parse', 'HEAD')
    calls = []
    original = subprocess.run
    def run(argv, **kwargs):
        calls.append(argv)
        return original(argv, **kwargs)
    monkeypatch.setattr(sync.subprocess, 'run', run)
    resolved, files = sync._read_public_git_snapshot('public/fixture', commit, paths, remote=repo.as_uri())
    assert resolved == commit and set(files) == set(paths)
    for name in paths:
        data = (repo / name).read_bytes()
        assert files[name] == (sync.git_blob(data), data)
    assert sum('ls-tree' in c for c in calls) == 1
    assert sum('cat-file' in c for c in calls) == len(paths)
    assert not any('cat-file' in c and '-s' in c for c in calls)
    assert next(c for c in calls if 'ls-tree' in c).count('--literal-pathspecs') == 1


@pytest.mark.parametrize('oversized', [False, True])
def test_real_promisor_snapshot_sizes_missing_blobs(tmp_path, monkeypatch, oversized):
    import subprocess
    repo = tmp_path / 'source'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args],
                                       stderr=subprocess.DEVNULL).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'uploadpack.allowFilter', 'true')
    (repo / 'a.py').write_bytes(b'content\n')
    (repo / 'LICENSE').write_bytes(b'license\n')
    git('add', '.')
    git('commit', '-m', 'fixture')
    commit = git('rev-parse', 'HEAD')
    missing_before, content_reads = [], []
    original = subprocess.run
    def run(argv, **kwargs):
        if 'cat-file' in argv and 'blob' in argv:
            content_reads.append(argv)
        if 'ls-tree' in argv:
            prefix = argv[:argv.index('--literal-pathspecs')]
            result = original(prefix + ['rev-list', '--objects', '--missing=print', 'FETCH_HEAD'],
                              env=kwargs['env'], capture_output=True, timeout=10)
            assert result.returncode == 0
            missing_before.extend(line for line in result.stdout.splitlines() if line.startswith(b'?'))
        return original(argv, **kwargs)
    monkeypatch.setattr(sync.subprocess, 'run', run)
    if oversized:
        monkeypatch.setattr(sync, 'MAX_BYTES', 3)
        with pytest.raises(ValueError, match='byte limit'):
            sync._read_public_git_snapshot('public/fixture', commit, ['a.py', 'LICENSE'], remote=repo.as_uri())
    else:
        resolved, files = sync._read_public_git_snapshot('public/fixture', commit,
                                                        ['a.py', 'LICENSE'], remote=repo.as_uri())
        assert resolved == commit
        assert files['a.py'] == (sync.git_blob(b'content\n'), b'content\n')
        assert files['LICENSE'] == (sync.git_blob(b'license\n'), b'license\n')
    assert len(content_reads) == (0 if oversized else 2)
    assert len(missing_before) == 2  # Proves the server actually honored blob:none.

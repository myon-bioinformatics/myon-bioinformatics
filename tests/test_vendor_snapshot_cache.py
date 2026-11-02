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

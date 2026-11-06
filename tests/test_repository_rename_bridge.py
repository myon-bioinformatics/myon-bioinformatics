"""Temporary aliases never authorize different artifacts or repository identities."""
import copy
import hashlib

import pytest
import vendor_consumers as consumers
import vendor_sync as sync

PAIRS = list(sync._REPOSITORY_RENAMES.items())
DATA = b'approved source\n'


def documents(source):
    row = {'repository': source, 'source': 'tool.py', 'destination': 'vendor/tool.py'}
    registry = {'schema': consumers.SCHEMA, 'consumers': [{
        'repository': 'owner/app', 'state': 'locked', 'lock': 'vendor.lock.json',
        'entries': [dict(row)],
    }]}
    lock = {'schema': sync.SCHEMA, 'files': [dict(row, ref='1'*40, commit='1'*40,
        blob_sha=sync.git_blob(DATA), sha256=hashlib.sha256(DATA).hexdigest())]}
    return registry, lock


def identity(old):
    canonical, rid = sync._REPOSITORY_RENAMES[old]
    return dict(id=rid, full_name=canonical, private=False, visibility='public', archived=False)


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('registry_new,lock_new', [(False,False),(False,True),(True,False),(True,True)])
def test_both_migration_orders_preserve_input(monkeypatch, old, pair, registry_new, lock_new):
    new, _ = pair
    registry, _ = documents(new if registry_new else old)
    _, lock = documents(new if lock_new else old)
    before = copy.deepcopy((registry, lock))
    calls = []
    def fetch(name, *args, **kwargs):
        calls.append(name)
        return identity(old)
    monkeypatch.setattr(sync, 'fetch_repository_record', fetch)
    assert consumers.compare(registry, lock, 'owner/app')['matches']
    assert (registry, lock) == before
    assert calls == ([old, new] if registry_new != lock_new else [])


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('name_index', [0,1])
@pytest.mark.parametrize('field,value', [('id',1),('id',True),('full_name','owner/impostor'),
    ('private',True),('visibility','internal'),('archived',True)])
def test_identity_mismatch_fails_closed(monkeypatch, old, pair, name_index, field, value):
    new, _ = pair
    registry, _ = documents(old)
    _, lock = documents(new)
    bad_name = (old,new)[name_index]
    def fetch(name, *args, **kwargs):
        record = identity(old)
        if name == bad_name:
            record[field] = value
        return record
    monkeypatch.setattr(sync, 'fetch_repository_record', fetch)
    with pytest.raises(ValueError, match='rename identity mismatch'):
        consumers.compare(registry, lock, 'owner/app')


@pytest.mark.parametrize('fault', ['source','destination','repository','missing','duplicate','unavailable'])
def test_other_failures_are_not_reclassified_as_renames(monkeypatch, fault):
    old, (new, _) = PAIRS[0]
    registry, _ = documents(old)
    _, lock = documents(new)
    def unavailable(*a, **k):
        raise OSError('identity lookup unavailable')
    monkeypatch.setattr(sync, 'fetch_repository_record', unavailable)
    if fault in ('source','destination','repository'):
        lock['files'][0][fault] = 'owner/other' if fault == 'repository' else 'other.py'
    elif fault == 'missing':
        lock['files'] = []
    elif fault == 'duplicate':
        lock['files'].append(dict(lock['files'][0]))
    if fault in ('missing','duplicate'):
        with pytest.raises(ValueError):
            consumers.compare(registry, lock, 'owner/app')
    elif fault == 'unavailable':
        with pytest.raises(OSError):
            consumers.compare(registry, lock, 'owner/app')
    else:
        assert not consumers.compare(registry, lock, 'owner/app')['matches']


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('bad', [False,True])
def test_public_redirect_requires_exact_reviewed_id(monkeypatch, old, pair, bad):
    canonical, rid = pair
    html = (f'<meta name="octolytics-dimension-repository_id" content="{rid + int(bad)}">'
            f'<meta name="octolytics-dimension-repository_nwo" content="{canonical}">'
            '<meta name="octolytics-dimension-repository_public" content="true">').encode()
    monkeypatch.setattr(sync, '_public_http_get', lambda url: html)
    if bad:
        with pytest.raises(ValueError, match='rename identity mismatch'):
            sync.public_repository_page_record(old)
    else:
        assert sync.public_repository_page_record(old) == identity(old)


def test_unknown_redirect_still_rejected(monkeypatch):
    html = (b'<meta name="octolytics-dimension-repository_id" content="1382466695">'
            b'<meta name="octolytics-dimension-repository_nwo" content="owner/other">'
            b'<meta name="octolytics-dimension-repository_public" content="true">')
    monkeypatch.setattr(sync, '_public_http_get', lambda url: html)
    with pytest.raises(ValueError, match='name mismatch'):
        sync.public_repository_page_record('owner/original')


@pytest.mark.parametrize('fault', [None,'unmerged','source_blob','source_bytes','child_bytes'])
def test_adopted_audit_retains_provenance_and_bytes(monkeypatch, fault):
    old, (new, _) = PAIRS[0]
    registry, _ = documents(old)
    _, lock = documents(new)
    monkeypatch.setattr(sync, 'fetch_repository_record', lambda *a, **k: identity(old))
    monkeypatch.setattr(sync, '_load_recorded_child_lock', lambda *a, **k: copy.deepcopy(lock))
    seen = []
    def merged(repository, revision, **kwargs):
        seen.append((repository,revision))
        if fault == 'unmerged':
            raise ValueError(sync.PIN_NOT_MERGED_ERROR)
    monkeypatch.setattr(sync, 'commit_merged_into_default', merged)
    def snapshot(repository, revision, paths):
        child = repository == 'owner/app'
        data = b'tampered' if fault == ('child_bytes' if child else 'source_bytes') else DATA
        blob = '0'*40 if fault == 'source_blob' and not child else sync.git_blob(DATA)
        return revision, {path:(blob,data) for path in paths}
    monkeypatch.setattr(sync, '_public_git_snapshot', snapshot)
    report = sync.audit_adopted_child_files(registry, {'heads':{'owner/app':{'sha':'2'*40}}}, ['owner/app'])
    assert seen == [(new,'1'*40)]
    assert report['failure_count'] == (0 if fault is None else 1)
    assert lock['files'][0]['ref'] == lock['files'][0]['commit'] == '1'*40


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('consumer_new,source_new', [(False,False),(False,True),(True,False),(True,True)])
def test_aliases_cannot_hide_self_vendoring(old, pair, consumer_new, source_new):
    new, _ = pair
    consumer = new if consumer_new else old
    registry, lock = documents(new if source_new else old)
    registry['consumers'][0]['repository'] = consumer
    with pytest.raises(ValueError, match='own canonical artifact'):
        consumers.compare(registry, lock, consumer)


def test_repeated_comparison_rechecks_old_name(monkeypatch):
    old, (new, _) = PAIRS[0]
    registry, _ = documents(old)
    _, lock = documents(new)
    current = identity(old)
    monkeypatch.setattr(sync, 'fetch_repository_record', lambda *a, **k: dict(current))
    assert consumers.compare(registry, lock, 'owner/app')['matches']
    current['id'] += 1
    with pytest.raises(ValueError, match='rename identity mismatch'):
        consumers.compare(registry, lock, 'owner/app')


@pytest.mark.parametrize('fault', ['canonical','private','archived'])
def test_public_redirect_metadata_is_strict(monkeypatch, fault):
    old, (new, rid) = PAIRS[0]
    html = (f'<meta name="octolytics-dimension-repository_id" content="{rid}">'
            f'<meta name="octolytics-dimension-repository_nwo" content="{new if fault != "canonical" else "owner/other"}">'
            f'<meta name="octolytics-dimension-repository_public" content="{"false" if fault == "private" else "true"}">'
            + ('This repository has been archived' if fault == 'archived' else '')).encode()
    monkeypatch.setattr(sync, '_public_http_get', lambda url: html)
    with pytest.raises(ValueError, match='rename identity mismatch'):
        sync.public_repository_page_record(old)


def test_both_pairs_are_independently_verified(monkeypatch):
    old1, (new1, _) = PAIRS[0]
    old2, (new2, _) = PAIRS[1]
    registry, _ = documents(old1)
    _, lock = documents(new1)
    registry['consumers'][0]['entries'].append(dict(registry['consumers'][0]['entries'][0],
        repository=old2, destination='vendor/second.py'))
    lock['files'].append(dict(lock['files'][0], repository=new2, destination='vendor/second.py'))
    calls = []
    def fetch(name, *a, **k):
        calls.append(name)
        return identity(old1 if name in (old1,new1) else old2)
    monkeypatch.setattr(sync, 'fetch_repository_record', fetch)
    assert consumers.compare(registry, lock, 'owner/app')['matches']
    assert set(calls) == {old1,new1,old2,new2}

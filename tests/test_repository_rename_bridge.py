"""Retired aliases fail closed; current source provenance checks stay strict."""
import copy, hashlib, json
from pathlib import Path

import pytest
import vendor_consumers as consumers
import vendor_sync as sync

PAIRS = [
    ('myon-bioinformatics/web-ui', ('myon-bioinformatics/PySaiyanWeBoo', 1382466695)),
    ('myon-bioinformatics/browser-test-kit', ('myon-bioinformatics/BrowserTestKit', 1384313653)),
]
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


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('registry_new,lock_new', [(False,False),(False,True),(True,False),(True,True)])
def test_names_compare_exactly_without_network(monkeypatch, old, pair, registry_new, lock_new):
    new, _ = pair
    registry, _ = documents(new if registry_new else old)
    _, lock = documents(new if lock_new else old)
    before = copy.deepcopy((registry, lock))
    def forbidden(*args, **kwargs):
        pytest.fail('comparison must not resolve repository aliases')
    monkeypatch.setattr(sync, 'fetch_repository_record', forbidden)
    assert consumers.compare(registry, lock, 'owner/app')['matches'] == (registry_new == lock_new)
    assert (registry, lock) == before


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('wrong_id', [False, True])
def test_redirect_is_rejected_even_with_expected_id(monkeypatch, old, pair, wrong_id):
    canonical, rid = pair
    html = (f'<meta name="octolytics-dimension-repository_id" content="{rid + int(wrong_id)}">'
            f'<meta name="octolytics-dimension-repository_nwo" content="{canonical}">'
            '<meta name="octolytics-dimension-repository_public" content="true">').encode()
    monkeypatch.setattr(sync, '_public_http_get', lambda url: html)
    with pytest.raises(ValueError, match='name mismatch'):
        sync.public_repository_page_record(old)
    assert sync.public_repository_page_record(canonical)['full_name'] == canonical


@pytest.mark.parametrize('old,pair', PAIRS)
@pytest.mark.parametrize('use_new', [False, True])
def test_exact_self_vendoring_remains_rejected(old, pair, use_new):
    name = pair[0] if use_new else old
    registry, lock = documents(name)
    registry['consumers'][0]['repository'] = name
    with pytest.raises(ValueError, match='own canonical artifact'):
        consumers.compare(registry, lock, name)


def test_current_registration_has_no_retired_identities():
    root = Path(__file__).resolve().parents[1]
    old = {pair[0].casefold() for pair in PAIRS}
    registry = json.loads((root / 'vendor-consumers.json').read_text())
    heads = json.loads((root / 'child-heads.json').read_text())
    catalog = json.loads((root / 'vendor-catalog.json').read_text())
    def check(value):
        if isinstance(value, dict):
            for k, v in value.items():
                assert k.casefold() not in old
                check(v)
        elif isinstance(value, list):
            for v in value:
                check(v)
        elif isinstance(value, str):
            assert value.casefold() not in old
    for doc in (registry, heads, catalog):
        check(doc)


@pytest.mark.parametrize('fault', [None,'unmerged','source_blob','source_bytes','child_bytes'])
def test_adopted_audit_retains_provenance_and_bytes(monkeypatch, fault):
    old, (new, _) = PAIRS[0]
    registry, _ = documents(new)
    _, lock = documents(new)
    monkeypatch.setattr(sync, '_load_recorded_child_lock', lambda *a, **k: copy.deepcopy(lock))
    # This fixture tests provenance/byte auditing with fake commits, not live repository metadata.
    monkeypatch.setattr(sync, '_ensure_public_source_repository', lambda *a, **k: None)
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

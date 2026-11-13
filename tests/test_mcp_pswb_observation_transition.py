"""Frozen observations retain strict byte audits during an explicit contract replacement."""
import copy, hashlib, json
from pathlib import Path
from unittest.mock import patch

import pytest
import vendor_sync as sync
import vendor_consumers as consumers

ROOT = Path(__file__).resolve().parents[1]
NAME = 'myon-bioinformatics/mcp-toolcall-lab'

def inputs():
    load = lambda p: json.loads((ROOT / p).read_text())
    return (load('vendor-consumers.json'), load('child-heads.json'),
            load('vendor-observation-transitions.json'), load('tests/fixtures/mcp-pswb-observation-old.lock.json'))

def resolve(registry, heads, manifest, lock, selected=None):
    with patch.object(sync, '_load_recorded_child_lock', return_value=lock):
        return sync._registry_for_recorded_transitions(registry, heads, manifest,
                                                       [NAME] if selected is None else selected)

def test_exact_old_observation_uses_old_contract_without_mutating_target():
    registry, heads, manifest, lock = inputs(); original = copy.deepcopy(registry)
    result, reports = resolve(registry, heads, manifest, lock)
    assert registry == original
    assert consumers.compare(result, lock, NAME)['matches']
    assert not consumers.compare(registry, lock, NAME)['matches']
    assert reports[0]['recorded_sha'] == heads['heads'][NAME]['sha']

@pytest.mark.parametrize('change', ['digest','source','missing','mixed'])
def test_changed_old_lock_fails_closed(change):
    registry, heads, manifest, lock = inputs()
    if change == 'digest': lock['files'][0]['sha256'] = '0' * 64
    if change == 'source': lock['files'][0]['source'] = 'other.py'
    if change == 'missing': lock['files'].pop()
    if change == 'mixed': lock['files'].append(copy.deepcopy(lock['files'][0]))
    with pytest.raises(ValueError, match='lock changed'): resolve(registry, heads, manifest, lock)

def test_unknown_observation_cannot_use_old_contract():
    registry, heads, manifest, lock = inputs(); heads['heads'][NAME]['sha'] = '1' * 40
    result, reports = resolve(registry, heads, manifest, lock)
    assert not reports and not consumers.compare(result, lock, NAME)['matches']

def test_identity_change_is_rejected():
    registry, heads, manifest, lock = inputs(); heads['heads'][NAME]['repository_id'] += 1
    with pytest.raises(ValueError, match='repository id'): resolve(registry, heads, manifest, lock)

def test_changed_desired_contract_is_rejected():
    registry, heads, manifest, lock = inputs()
    next(c for c in registry['consumers'] if c['repository'] == NAME)['entries'][0]['source'] = 'other.py'
    with pytest.raises(ValueError, match='target contract'): resolve(registry, heads, manifest, lock)

def test_unmonitored_repo_is_never_read():
    registry, heads, manifest, lock = inputs()
    with patch.object(sync, '_load_recorded_child_lock', side_effect=AssertionError('unexpected fetch')):
        _, reports = sync._registry_for_recorded_transitions(registry, heads, manifest, [])
    assert not reports

def test_failed_read_is_not_converted_to_permission():
    registry, heads, manifest, lock = inputs()
    with patch.object(sync, '_load_recorded_child_lock', side_effect=ValueError('unavailable')):
        with pytest.raises(ValueError, match='unavailable'):
            sync._registry_for_recorded_transitions(registry, heads, manifest, [NAME])

@pytest.mark.parametrize('bad', ['source', 'child', 'clean', 'unmerged'])
def test_selected_old_contract_still_rejects_tampered_bytes(bad):
    registry, heads, manifest, lock = inputs()
    registry, _ = resolve(registry, heads, manifest, lock)
    # Use one exact entry to exercise the unchanged source and placed-byte audit.
    item = copy.deepcopy(lock['files'][0]); data = b'reviewed source bytes\n'
    item['sha256'] = hashlib.sha256(data).hexdigest(); item['blob_sha'] = sync.git_blob(data)
    lock = {'schema': 'vendor-lock/1', 'files': [item]}
    consumer = consumers.get_consumer(registry, NAME)
    consumer['entries'] = [{k:item[k] for k in ('repository','source','destination')}]
    registry = {'schema':'vendor-consumers/1', 'consumers':[consumer]}
    def snapshot(repo, revision, paths):
        source = repo == item['repository']; blobdata = data
        if (source and bad == 'source') or (not source and bad == 'child'): blobdata += b'tamper'
        return revision, {p:(sync.git_blob(blobdata), blobdata) for p in paths}
    with patch.object(sync,'_load_recorded_child_lock',return_value=lock), \
         patch.object(sync,'_ensure_public_source_repository'), \
         patch.object(sync,'commit_merged_into_default', side_effect=ValueError('source not merged') if bad == 'unmerged' else None), \
         patch.object(sync,'_public_git_snapshot',side_effect=snapshot):
        report = sync.audit_adopted_child_files(registry, heads, [NAME])
    assert report['failure_count'] == (0 if bad == 'clean' else 1)

def test_new_observation_uses_desired_contract_without_transition():
    registry, heads, manifest, _ = inputs()
    heads['heads'][NAME]['sha'] = '2' * 40
    lock = json.loads((ROOT/'tests/fixtures/mcp-pswb-observation-new.lock.json').read_text())
    result, reports = resolve(registry, heads, manifest, lock)
    assert not reports and consumers.compare(result, lock, NAME)['matches']

def test_parent_run_exposes_transition_and_still_invokes_old_contract_audit(monkeypatch, tmp_path):
    from test_vendor_pending_enrollment import _registry, _run, CHILD
    old = _registry(); desired = _registry()
    desired['consumers'][0]['entries'][0]['source'] = 'replacement.py'
    observed = {'schema':'vendor-lock/1','files': [{**old['consumers'][0]['entries'][0], 'ref':'3'*40, 'commit':'3'*40, 'blob_sha':'4'*40, 'sha256':'5'*64}]}
    original_resolver = sync._registry_for_recorded_transitions
    def resolver(*args, **kwargs):
        with patch.object(sync, '_load_recorded_child_lock', return_value=observed):
            return original_resolver(*args, **kwargs)
    monkeypatch.setattr(sync, '_registry_for_recorded_transitions', resolver)
    manifest = {'schema':'vendor-observation-transitions/1','transitions':[{
        'consumer':CHILD,'repository_id':5,'recorded_sha':'1'*40,
        'lock_sha256':sync._transition_digest(observed),
        'target_consumer_sha256':sync._transition_digest(consumers.get_consumer(desired,CHILD)),
        'old_entries':old['consumers'][0]['entries'], 'reason':'test reviewed replacement',
    }]}
    (tmp_path/'vendor-observation-transitions.json').write_text(json.dumps(manifest))
    report, seen = _run(monkeypatch,tmp_path,desired,adopted=old)
    assert report['failure_count'] == 0
    assert report['observation_transitions'][0]['consumer'] == CHILD
    assert seen['registry'] == old

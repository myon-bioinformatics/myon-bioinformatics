"""Advisory skips cannot revoke parent-registered adopted copies."""
import copy
import json
from pathlib import Path

import pytest
import vendor_sync as sync
from test_vendor_child_bootstrap import child_fixture

ROOT = Path(__file__).resolve().parents[1]


def test_ironmate_registered_contract_ignores_recommendation_skips():
    registry = json.loads((ROOT / 'vendor-consumers.json').read_text())
    catalog = json.loads((ROOT / 'vendor-catalog.json').read_text())
    lock = json.loads((ROOT / 'tests/fixtures/ironmate-flow-workspace.lock.json').read_text())
    ironmate_rows = [t for t in catalog['tools'] if t['repository'].casefold() == 'myon-bioinformatics/ironmate']
    assert len(ironmate_rows) == 1 and ironmate_rows[0]['source'] == 'pillow_min.py'
    assert 'myon-bioinformatics/Ironmate' not in ironmate_rows[0].get('consumers', {})
    assert sync.verify_child_lock_against_parent(registry, catalog, lock, 'myon-bioinformatics/Ironmate') == sync.validate(lock)
    changed = copy.deepcopy(lock)
    changed['files'].pop()
    with pytest.raises(ValueError, match='topology mismatch'):
        sync.verify_child_lock_against_parent(registry, catalog, changed, 'myon-bioinformatics/Ironmate')


@pytest.mark.parametrize('fault', [None, 'file', 'file_and_lock', 'unregistered', 'source_failure'])
def test_skipped_adopted_copy_still_requires_source_and_child_integrity(tmp_path, monkeypatch, fault):
    parent, child, lock = child_fixture(tmp_path, monkeypatch)
    catalog_path = parent / 'vendor-catalog.json'
    catalog = json.loads(catalog_path.read_text())
    for row in catalog['tools']:
        row['consumers']['owner/child'] = {'enrollment': 'skipped', 'reason': 'parent_managed_tool'}
    catalog_path.write_text(json.dumps(catalog))
    if fault in ('file', 'file_and_lock'):
        altered = b'altered\n'
        (child / 'vendor/tool.py').write_bytes(altered)
        if fault == 'file_and_lock':
            lock['files'][0]['blob_sha'] = sync.git_blob(altered)
            lock['files'][0]['sha256'] = sync.hashlib.sha256(altered).hexdigest()
            (child / 'vendor.lock.json').write_text(json.dumps(lock))
    if fault == 'unregistered':
        lock['files'][0]['destination'] = 'vendor/unregistered.py'
        (child / 'vendor.lock.json').write_text(json.dumps(lock))
    if fault == 'source_failure':
        def fail(*args):
            raise ValueError(sync.PUBLIC_GIT_FETCH_ERROR)
        monkeypatch.setattr(sync, '_public_git_snapshot', fail)
    if fault is None:
        sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)
    else:
        with pytest.raises(ValueError):
            sync.run_child_ci(child, child / 'ref.json', 'owner/child', parent_root=parent)

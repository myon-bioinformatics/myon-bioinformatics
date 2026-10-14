"""Parent topology follows Ironmate #94's locked snapshot, not auto-enrollment."""
import copy
import json
from pathlib import Path

import vendor_consumers
import vendor_fleet_plan

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = 'myon-bioinformatics/Ironmate'


def load(path):
    return json.loads((ROOT / path).read_text())


def test_ironmate_registry_matches_reviewed_pr94_lock():
    registry = load('vendor-consumers.json')
    lock = load('tests/fixtures/ironmate_vendor_lock_841bf541.json')
    assert len(lock['files']) == 51
    assert vendor_consumers.compare(registry, lock, CONSUMER)['matches']
    # All adopted source digests remain owned by the child's immutable lock.
    assert all(x['ref'] == x['commit'] for x in lock['files'])
    changed = copy.deepcopy(lock)
    changed['files'].pop()
    assert not vendor_consumers.compare(registry, changed, CONSUMER)['matches']


def test_retired_sources_are_not_proposed_for_reenrollment():
    result = vendor_fleet_plan.plan(load('vendor-catalog.json'), load('vendor-consumers.json'),
                                   active_consumers=[CONSUMER])
    for source in ('git_inspector.py', 'vendor_sync.py'):
        rows = [x for x in result['rows'] if x['tool_repository'] ==
                'myon-bioinformatics/myon-bioinformatics' and x['source'] == source]
        assert len(rows) == 1 and rows[0]['action'] == 'skipped'

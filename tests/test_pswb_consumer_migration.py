"""Canonical PSWB consumers keep old observations strict and new locks exact."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import vendor_consumers as consumers
import vendor_sync as sync

ROOT = Path(__file__).resolve().parents[1]
load = lambda path: json.loads((ROOT / path).read_text())
NEW = load('tests/fixtures/pswb_consumer_migration_20261113.json')
OLD = load('tests/fixtures/pswb_consumer_old_locks_20261113.json')


@pytest.mark.parametrize('name', NEW)
def test_merged_lock_matches_desired_topology(name):
    snapshot = NEW[name]
    assert sync._transition_digest(snapshot['lock']) == snapshot['lock_sha256']
    assert consumers.compare(load('vendor-consumers.json'), snapshot['lock'], name)['matches']
    assert all(row['ref'] == row['commit'] for row in snapshot['lock']['files'])
    assert not any(row['source'] == 'web_ui.py' for row in snapshot['lock']['files'])
    changed = copy.deepcopy(snapshot['lock'])
    changed['files'].pop()
    assert not consumers.compare(load('vendor-consumers.json'), changed, name)['matches']


@pytest.mark.parametrize('name', OLD)
def test_only_exact_historical_observation_uses_old_contract(name):
    registry = load('vendor-consumers.json')
    heads = load('child-heads.json')
    transitions = load('vendor-observation-transitions.json')
    with patch.object(sync, '_load_recorded_child_lock', return_value=OLD[name]):
        old_registry, reports = sync._registry_for_recorded_transitions(registry, heads, transitions, [name])
    assert len(reports) == 1
    assert consumers.compare(old_registry, OLD[name], name)['matches']
    assert not consumers.compare(registry, OLD[name], name)['matches']
    tampered = copy.deepcopy(OLD[name])
    tampered['files'][0]['sha256'] = '0' * 64
    with patch.object(sync, '_load_recorded_child_lock', return_value=tampered):
        with pytest.raises(ValueError, match='lock changed'):
            sync._registry_for_recorded_transitions(registry, heads, transitions, [name])
    heads['heads'][name]['sha'] = NEW[name]['sha']
    with patch.object(sync, '_load_recorded_child_lock', side_effect=AssertionError('old lock read')):
        new_registry, reports = sync._registry_for_recorded_transitions(registry, heads, transitions, [name])
    assert reports == []
    assert consumers.compare(new_registry, NEW[name]['lock'], name)['matches']

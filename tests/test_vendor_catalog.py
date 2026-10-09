import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import vendor_catalog as catalog
import vendor_sync as sync

OLD = '1' * 40
NEW = '2' * 40


def manifest():
    return {'schema': catalog.SCHEMA, 'tools': [
        {'repository': 'owner/example', 'commit': NEW, 'reason': 'Reviewed baseline'}]}


def locked():
    return {'schema': sync.SCHEMA, 'files': [dict(
        repository='owner/example', source='example.py', destination='vendor/example.py',
        commit=OLD, ref='main', blob_sha=sync.git_blob(b'old'),
        sha256=hashlib.sha256(b'old').hexdigest())]}


def test_defaults_override_and_no_mutation():
    value = manifest()
    before = copy.deepcopy(value)
    assert catalog.validate(value)['tools'][0]['source'] == 'example.py'
    assert value == before
    value['tools'][0]['source'] = 'helpers/other.py'
    assert catalog.validate(value)['tools'][0]['source'] == 'helpers/other.py'


@pytest.mark.parametrize('field,value', [
    ('commit', 'main'), ('commit', 'abcd123'), ('commit', 'A' * 40),
    ('source', '../escape.py'), ('source', '/absolute.py'), ('source', 'a\\b.py'),
    ('repository', '../repo'), ('reason', ''), ('development_head', NEW)])
def test_invalid_tool(field, value):
    doc = manifest()
    doc['tools'][0][field] = value
    with pytest.raises(ValueError):
        catalog.validate(doc)


def test_duplicates_and_invalid_decisions():
    doc = manifest()
    doc['tools'] *= 2
    with pytest.raises(ValueError, match='duplicate'):
        catalog.validate(doc)
    for decision in ({'enrollment': 'skipped'}, {'enrollment': 'pending', 'reason': 'unknown'},
                     {'enrollment': 'skipped', 'reason': ''}):
        doc = manifest()
        doc['tools'][0]['consumers'] = {'owner/consumer': decision}
        with pytest.raises(ValueError):
            catalog.validate(doc)


def test_compare_all_destinations_skip_unused_and_unrelated_license():
    doc, lock = manifest(), locked()
    lock['files'].append({**lock['files'][0], 'source': 'LICENSE', 'destination': 'vendor/LICENSE'})
    row = catalog.compare(doc, lock, 'owner/consumer')['tools'][0]
    assert row['comparison'] == 'different'
    assert row['enrollment'] == 'enrolled'
    assert len(row['locked']) == 1
    lock['files'][0]['commit'] = NEW
    assert catalog.compare(doc, lock, 'owner/consumer')['tools'][0]['comparison'] == 'at_recommended'
    lock['files'].append({**lock['files'][0], 'commit': OLD, 'destination': 'other/example.py'})
    assert catalog.compare(doc, lock, 'owner/consumer')['tools'][0]['comparison'] == 'different'
    doc['tools'][0]['consumers'] = {'owner/consumer': {'enrollment': 'skipped', 'reason': 'requires_external_package'}}
    row = catalog.compare(doc, lock, 'OWNER/consumer')['tools'][0]
    assert row['enrollment'] == 'skipped' and len(row['locked']) == 2
    doc['tools'][0]['source'] = 'override.py'
    assert catalog.compare(doc, lock, 'owner/consumer')['tools'][0]['comparison'] == 'missing'


def test_recommendation_change_never_changes_lock_or_source(tmp_path, monkeypatch):
    doc, lock = manifest(), locked()
    original = copy.deepcopy(lock)
    (tmp_path / 'vendor').mkdir()
    (tmp_path / 'vendor/example.py').write_bytes(b'old')
    path = tmp_path / 'vendor.lock.json'
    path.write_text(json.dumps(lock))
    before = path.read_bytes()
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda *a: pytest.fail('offline used network'))
    for commit in (OLD, NEW):
        doc['tools'][0]['commit'] = commit
        catalog.compare(doc, lock, 'owner/consumer')
        cp = tmp_path / 'catalog.json'
        cp.write_text(json.dumps(doc))
        assert catalog.main(['compare', '--catalog', str(cp), '--lock', str(path), '--consumer', 'owner/consumer']) == 0
    assert lock == original
    assert path.read_bytes() == before
    assert (tmp_path / 'vendor/example.py').read_bytes() == b'old'
    sync.synchronize(path.name, tmp_path, 'check')


@pytest.mark.parametrize('failure', [None, 'commit', 'blob'])
def test_verification_uses_canonical_identity(monkeypatch, failure):
    data = b'raise RuntimeError("must never import")\n'
    calls = []
    def snapshot(repo, commit, sources):
        calls.append((repo, commit, sources))
        return (OLD if failure == 'commit' else commit,
                {sources[0]: ('0' * 40 if failure == 'blob' else sync.git_blob(data), data)})
    monkeypatch.setattr(sync, '_public_git_snapshot', snapshot)
    if failure:
        with pytest.raises(ValueError):
            catalog.verify(manifest())
    else:
        result = catalog.verify(manifest())['files'][0]
        assert result['blob_sha'] == sync.git_blob(data)
        assert result['sha256'] == hashlib.sha256(data).hexdigest()
    assert calls == [('owner/example', NEW, ['example.py'])]


def test_real_git_source_mode_and_exact_pin(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args]).decode().strip()
    git('init', '-b', 'main')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path / 'example.py').write_bytes(b'old')
    (tmp_path / 'link.py').symlink_to('example.py')
    git('add', '.')
    git('commit', '-m', 'initial')
    commit = git('rev-parse', 'HEAD')
    (tmp_path / 'example.py').write_bytes(b'new')
    git('commit', '-am', 'development')
    original = sync._public_git_snapshot
    monkeypatch.setattr(sync, '_public_git_snapshot', lambda r, c, s: original(r, c, s, remote=tmp_path.as_uri()))
    doc = manifest()
    doc['tools'][0]['commit'] = commit
    assert catalog.verify(doc)['files'][0]['sha256'] == hashlib.sha256(b'old').hexdigest()
    doc['tools'][0]['source'] = 'link.py'
    with pytest.raises(ValueError, match='regular file'):
        catalog.verify(doc)


def test_checked_in_catalog_and_cli_errors(tmp_path):
    root = Path(__file__).resolve().parents[1]
    value = catalog.validate(json.loads((root / 'vendor-catalog.json').read_text()))
    assert {'gh_identity.py', 'xprobe.py', 'yourself.py', 'cli_args.py', 'markdown.py', 'ascii_artist.py'} <= {t['source'] for t in value['tools']}
    assert catalog.main(['validate', '--catalog', str(tmp_path / 'missing')]) == 2
    result = subprocess.run([sys.executable, str(root / 'vendor_catalog.py'), '--help'], cwd=tmp_path, capture_output=True)
    assert result.returncode == 0


def test_default_skip_and_consumer_override_preserve_lock():
    doc, lock = manifest(), locked()
    before = copy.deepcopy(lock)
    doc["tools"][0]["default_enrollment"] = {"enrollment": "skipped", "reason": "requires_external_package"}
    row = catalog.compare(doc, lock, "owner/consumer")["tools"][0]
    assert row["enrollment"] == "skipped" and row["comparison"] == "different"
    doc["tools"][0]["consumers"] = {"owner/consumer": {"enrollment": "enrolled", "reason": "dependency_available"}}
    assert catalog.compare(doc, lock, "OWNER/consumer")["tools"][0]["enrollment"] == "enrolled"
    assert lock == before


@pytest.mark.parametrize("decision", [None, {}, {"enrollment": "pending", "reason": "unknown"},
    {"enrollment": "skipped", "reason": "not a code"}])
def test_invalid_default_decision(decision):
    doc = manifest()
    doc["tools"][0]["default_enrollment"] = decision
    with pytest.raises(ValueError):
        catalog.validate(doc)


def test_public_inventory_has_complete_catalog_coverage():
    root = Path(__file__).resolve().parents[1]
    doc = catalog.validate(json.loads((root / "vendor-catalog.json").read_text()))
    inventory = json.loads((root / "docs/vendor-target-inventory.json").read_text())
    tools = {(t["repository"], t["source"]): t for t in doc["tools"]}
    assert len(inventory["repositories"]) + inventory["private_repositories_omitted"] == inventory["inspected_repositories"]
    for source in inventory["sources"]:
        tool = tools[(source["repo"], source["source"])]
        if source["enrollment"] == "skipped":
            assert tool["default_enrollment"]["reason"] == source["reason"]
        assert len(tool["commit"]) == 40

import copy,json
from pathlib import Path
from unittest.mock import patch
import pytest
import vendor_sync as s,vendor_consumers as c
ROOT=Path(__file__).resolve().parents[1]
REPOS=['mcp-toolcall-lab','PySaiyanWeBoo','markdown','xprobe']
def load(p): return json.loads((ROOT/p).read_text())
@pytest.mark.parametrize('repo',REPOS)
def test_exact_old_and_desired_new_contracts(repo):
 name='myon-bioinformatics/'+repo
 r=load('vendor-consumers.json'); h=load('child-heads.json'); t=load('vendor-observation-transitions.json')
 old=load('tests/fixtures/njs-'+repo+'-old.lock.json');new=load('tests/fixtures/njs-'+repo+'-new.lock.json');before=copy.deepcopy(r)
 assert c.compare(r,new,name)['matches']
 assert not c.compare(r,old,name)['matches']
 with patch.object(s,'_load_recorded_child_lock',return_value=old):
  restored,reports=s._registry_for_recorded_transitions(r,h,t,[name])
 assert r==before and len(reports)==1 and c.compare(restored,old,name)['matches']
 changed=copy.deepcopy(old); changed['files'][0]['sha256']='0'*64
 with patch.object(s,'_load_recorded_child_lock',return_value=changed),pytest.raises(ValueError,match='lock changed'):
  s._registry_for_recorded_transitions(r,h,t,[name])
 h['heads'][name]['sha']='1'*40
 with patch.object(s,'_load_recorded_child_lock',side_effect=AssertionError('new observation must not use transition')):
  actual,reports=s._registry_for_recorded_transitions(r,h,t,[name])
 assert not reports and c.compare(actual,new,name)['matches'] and not c.compare(actual,old,name)['matches']

def test_pswb_cleanup_predates_launcher_migration():
    base=load('tests/fixtures/njs-PySaiyanWeBoo-adopted-base.lock.json')
    new=load('tests/fixtures/njs-PySaiyanWeBoo-new.lock.json')
    old=load('tests/fixtures/njs-PySaiyanWeBoo-old.lock.json')
    assert {e['destination'] for e in base['files']} == {'tools/vendor/LICENSE','vendor/node_launch.py','vendor/py_launch.cjs'}
    license=next(e for e in base['files'] if e['source']=='LICENSE')
    assert license['commit']=='73157cb7fed236a4a941722a6dcddd69a33ab95a'
    assert license['blob_sha']=='4ec4989b801bf1a3df6184d21f79e6e7ed5931f6'
    assert license==next(e for e in new['files'] if e['source']=='LICENSE')
    assert next(e for e in old['files'] if e['destination']=='tool/vendor/LICENSE')['blob_sha']==license['blob_sha']
    assert [e for e in base['files'] if e['source']!='node_launch.py']==[e for e in new['files'] if e['source']!='njs_launch.py']

def test_mcp_prior_pswb_transition_keeps_same_frozen_lock():
    row=next(t for t in load('vendor-observation-transitions.json')['transitions'] if t['consumer'].endswith('/mcp-toolcall-lab'))
    assert row['recorded_sha']=='cd04472040f0db991c5138144c01f56a2b79e44b'
    assert row['lock_sha256']=='40496c0e700ff3c4894541bc87dca28c6755b0ed0065cb5a740ec0c0e1583135'
    old=load('tests/fixtures/njs-mcp-toolcall-lab-old.lock.json')
    assert s._transition_digest(old)==row['lock_sha256']
    assert {'vendor/web_ui.py','vendor/node_launch.py'} <= {e['destination'] for e in old['files']}

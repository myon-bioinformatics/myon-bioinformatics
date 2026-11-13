import json
from pathlib import Path
import tempfile
import unittest
from tools.unified_operations import view_data, write_workspace, collect_main_runs, snapshot_comparison

class UnifiedOperationsTests(unittest.TestCase):
    def test_closed_migration_joins_same_identity_without_mutating_captured_evidence(self):
        snap, ev = self.values()
        ev['records'][1]['events'] = [{'type': 'ci', 'head_sha': 'old', 'conclusion': 'success'}]
        snap['pswb_migration'] = {'checked_at': 'now', 'rows': [
            {'repository': 'demo', 'number': 2, 'url': 'https://github.com/demo/demo/pull/2',
             'checked_at': 'now', 'open': False, 'merged': True, 'head_sha': 'new',
             'main_sha': 'main', 'state': 'ブロッカー', 'runs': {'head': {'runs': [
                 {'id': 7, 'name': 'CI', 'head_sha': 'new', 'run_attempt': 2,
                  'workflow_id': 4, 'event': 'pull_request', 'status': 'completed',
                  'conclusion': 'failure', 'html_url': 'https://github.com/demo/demo/actions/runs/7'}]}}}]}
        d = view_data(snap, ev, {'weeks': []}, [])
        matches = [r for r in d['records'] if r['id'] == 'demo#2']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['github_state'], 'closed')
        self.assertEqual(matches[0]['events'][-1]['attempt'], 2)
        self.assertTrue(matches[0]['events'][0]['historical'])
        self.assertNotIn('historical', ev['records'][1]['events'][0])
        self.assertEqual(d['plan']['order'], [])

    def test_failed_migration_refresh_preserves_metadata_and_marks_incomplete(self):
        snap, ev = self.values()
        snap['pswb_migration'] = {'rows': [{'repository': 'demo', 'number': 2,
            'url': 'https://github.com/demo/demo/pull/2', 'complete': False,
            'head_sha': 'partial', 'state': '調査中'}]}
        d = view_data(snap, ev, {'weeks': []}, [])
        self.assertNotEqual(d['records'][1].get('head_sha'), 'partial')
        self.assertFalse(d['records'][1]['migration']['complete'])

    def test_consecutive_snapshots_separate_absence_counts_and_metadata_noise(self):
        def record(n, **values):
            return dict(id=f'r#{n}', repository='r', number=n, kind='pr',
                        title='PR', head_sha='h', ci='COMPLETE', **values)
        previous = {'generated_at': '2026-11-12T00:00:00Z', 'repositories': ['r', 'unmonitored'],
                    'records': [record(1), record(2), dict(id='hidden#1', repository='unmonitored')],
                    'integrations': [{'repository': 'r', 'branch': 'main', 'head_sha': 'm'}],
                    'repository_counts': [{'repository': 'r', 'issues': 3, 'pull_requests': 2,
                                           'open_issues_count': 5, 'get_issues': 5}]}
        current = {'generated_at': '2026-11-13T00:00:00Z', 'repositories': ['r'],
                   'records': [record(1, metadata_checked='new'), record(3)],
                   'integrations': previous['integrations'],
                   'repository_counts': [{'repository': 'r', 'issues': 2, 'pull_requests': 2,
                                          'open_issues_count': 4, 'get_issues': 4}]}
        result = snapshot_comparison(current, previous)
        self.assertEqual([r['id'] for r in result['added']], ['r#3'])
        self.assertEqual([r['id'] for r in result['absent']], ['r#2'])
        self.assertNotIn('merged', result['absent'][0])
        self.assertEqual(result['changed'], [])
        self.assertEqual(result['counts'][0]['changes']['issues'], {'before': 3, 'after': 2})
        self.assertEqual(result['open_prs'], {'before': 2, 'after': 2})
        current['records'][0]['head_sha'] = 'new-head'
        current['records'][0]['ci'] = 'FAILURE'
        changes = snapshot_comparison(current, previous)['changed'][0]['changes']
        self.assertEqual(set(changes), {'head_sha', 'ci'})

    def test_first_publication_has_no_inferred_additions(self):
        result = snapshot_comparison({'generated_at': 'now', 'records': [{'id': 'r#1'}]}, None)
        self.assertFalse(result['available'])
        self.assertEqual(result['added'], [])

    def test_main_runs_separate_events_old_sha_and_bot_jobs(self):
        snap={'owner':'demo','integrations':[{'repository':'r','branch':'main','head_sha':'a'}]}
        def run(number, event='push', sha='a', name='CI'):
            return dict(id=number,workflow_id=1,event=event,run_number=number,run_attempt=1,
                        head_sha=sha,name=name,conclusion='failure',status='completed',
                        html_url='https://github.com/demo/r/actions/runs/'+str(number),
                        updated_at='2026-11-13',actor={'login':'dependabot[bot]' if name=='Dependabot' else 'user'})
        def fetch(url):
            if '/actions/runs?' in url:return {'workflow_runs':[run(1),run(2),run(3,'schedule','old'),run(4,'dynamic',name='Dependabot')]}
            if '/git/ref/' in url:return {'object':{'sha':'a'}}
            return {'total_count':1,'jobs':[{'id':9,'name':'harvest','html_url':'https://github.com/demo/r/actions/runs/2/job/9',
                    'conclusion':'failure','steps':[{'name':'Open PR','conclusion':'failure'}]}]}
        result=collect_main_runs(snap,fetch);rows={r['run_id']:r for r in result['runs']}
        self.assertEqual(set(rows),{2,3,4})
        self.assertFalse(rows[3]['current_main']);self.assertEqual(rows[3]['jobs'],[])
        self.assertTrue(rows[4]['bot']);self.assertTrue(rows[2]['jobs_complete'])
        self.assertEqual(rows[2]['jobs'][0]['failed_steps'],['Open PR'])
    def test_main_move_and_fetch_failure_are_not_success(self):
        snap={'owner':'demo','integrations':[{'repository':'r','branch':'main','head_sha':'a'}]}
        self.assertEqual(collect_main_runs(snap,lambda url: (_ for _ in ()).throw(RuntimeError()))['errors'],
                         [{'repository':'r','error':'RuntimeError'}])
        def fetch(url):
            if '/git/ref/' in url:return {'object':{'sha':'b'}}
            return {'workflow_runs':[dict(id=1,workflow_id=1,event='push',run_number=1,run_attempt=1,
                head_sha='a',name='CI',conclusion='failure',status='completed',html_url='https://github.com/demo/r',
                updated_at='2026-11-13')]}
        row=collect_main_runs(snap,fetch)['runs'][0]
        self.assertFalse(row['main_stable']);self.assertFalse(row['current_main'])
    def values(self):
        snap={'generated_at':'2026-11-13T00:00:00Z','owner':'demo','records':[{'id':'demo#2'}],'integrations':[{'repository':'demo','head_sha':'a'*40}]}
        evidence={'records':[{'id':'demo#1','repository':'demo','number':1,'kind':'pr','github_state':'open','head_ref':'old','base_ref':'main','events':[]},{'id':'demo#2','repository':'demo','number':2,'kind':'pr','github_state':'open','head_ref':'new','base_ref':'main','events':[]}], 'repository_counts':[{'repository':'demo','open_issues_count':3,'issues':2,'pull_requests':1}],'changes':[]}
        return snap,evidence
    def test_absence_is_not_closure_and_counts_are_separate(self):
        snap,ev=self.values();d=view_data(snap,ev,{'weeks':[]},[])
        self.assertTrue(d['records'][0]['inventory_absent']);self.assertEqual(d['records'][0]['github_state'],'open')
        self.assertEqual(d['plan']['order'],['demo#2'])
        c=d['counts'][0];self.assertEqual((c['open_issues_count'],c['issues'],c['pull_requests']),(3,2,1))
        self.assertEqual(d['source']['parent_main'],'a'*40);self.assertNotIn('inventory_absent',ev['records'][0])
    def test_cycle_is_blocked(self):
        snap,ev=self.values();snap['records'].append({'id':'demo#1'});ev['records'][0]['base_ref']='new';ev['records'][1]['base_ref']='old'
        d=view_data(snap,ev,{'weeks':[]},[]);self.assertTrue(d['plan']['cycle']);self.assertEqual(d['plan']['order'],[])
    def test_script_injection_is_escaped(self):
        snap,ev=self.values();ev['records'][1]['title']='</script><script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);ui=root/'site/ui';ui.mkdir(parents=True);stage=root/'out';stage.mkdir()
            (ui/'unified.template.html').write_text('<script type="application/json">/*__DATA__*/</script>')
            (ui/'operations-data.json').write_text('[]')
            for n in ('shared-ui.js','unified.css','unified-model.js','unified-app.js'):(ui/n).write_text('')
            write_workspace(snap,ev,{'weeks':[]},stage,root)
            self.assertNotIn('</script><script>',(stage/'workspace.html').read_text())
            self.assertEqual(json.loads((stage/'unified-data.json').read_text())['records'][1]['title'],ev['records'][1]['title'])
if __name__=='__main__':unittest.main()


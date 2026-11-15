import json
from pathlib import Path
import tempfile
import unittest
from scripts.unified_operations import view_data, write_workspace, collect_main_runs, snapshot_comparison, collect_repository_counts

from scripts.unified_operations import refresh_evidence


class CurrentRestObservationTests(unittest.TestCase):
    def test_current_run_attempt_jobs_and_reviews_join_without_old_success_fallback(self):
        def fetch(path):
            if path.endswith('/pulls/1'):
                return {'head': {'sha': 'head'}, 'state': 'open'}
            if '/reviews?' in path:
                return [{'id': 2, 'user': {'login': 'reviewer'}, 'submitted_at': 'today',
                         'commit_id': 'head', 'state': 'CHANGES_REQUESTED', 'html_url': 'https://github.com/o/r/pull/1'}]
            if '/comments?' in path:
                return []
            if '/actions/runs?' in path:
                run = dict(id=3, name='CI', head_sha='head', workflow_id=7, event='push', status='completed',
                           conclusion='success', updated_at='today', html_url='https://github.com/o/r/actions/runs/3')
                return {'total_count': 2, 'workflow_runs': [dict(run, run_attempt=1, conclusion='failure'), dict(run, run_attempt=2)]}
            self.assertIn('/runs/3/attempts/2/jobs?', path)
            return {'total_count': 1, 'jobs': [dict(id=4, name='test', status='completed', conclusion='success',
                                                  html_url='https://github.com/o/r/actions/runs/3/job/4')]}
        record = {'id': 'r#1', 'repository': 'r', 'number': 1, 'kind': 'pr', 'head_sha': 'head',
                  'github_state': 'open', 'events': [{'type': 'ci', 'head_sha': 'old', 'conclusion': 'success'}]}
        snap = {'owner': 'o', 'records': [{'id': 'r#1'}]}
        result = refresh_evidence({'records': [record]}, snap, fetch)['records'][0]
        events = result['events']
        self.assertTrue(events[0]['historical'])
        self.assertEqual([(e['run_id'], e['attempt']) for e in events if e['type'] == 'ci' and not e.get('historical')], [(3, 2)])
        self.assertEqual([(e['run_id'], e['attempt'], e['job_id'], e['head_sha']) for e in events if e['type'] == 'job'], [(3, 2, 4, 'head')])
        self.assertEqual(result['review_coverage']['head_sha'], 'head')
        def moved(path):
            if path.endswith('/pulls/1'):
                return {'head': {'sha': 'changed'}, 'state': 'open'}
            return fetch(path)
        result = refresh_evidence({'records': [record]}, snap, moved)['records'][0]
        self.assertEqual(result['collection_error'], 'ValueError')
        self.assertNotIn('current_collection', result)
        self.assertNotIn('review_coverage', result)

class UnifiedOperationsTests(unittest.TestCase):
    def test_open_pr_inventory_survives_empty_active_view(self):
        for numbers in ([], [17, 53, 211]):
            with self.subTest(numbers=numbers):
                snap = {'owner': 'demo', 'repositories': ['r'], 'records': [],
                        'generated_at': '2026-11-15T00:00:00Z',
                        'integrations': [{'repository': 'r', 'head_sha': 'main'}]}
                calls = []
                def fetch(path):
                    calls.append(path)
                    if '/pulls?' in path:
                        return [{'number': n} for n in reversed(numbers)]
                    return [] if '/issues?' in path else {'open_issues_count': 999}
                counts = collect_repository_counts(snap, fetch)
                result = view_data(snap, {'records': [], 'repository_counts': counts}, {}, [])
                export = result['aoi_integration']
                self.assertEqual(export['records'], [])
                self.assertEqual(export['open_pr_inventory'], [dict(repository='demo/r',
                    checked_at=counts[0]['checked'], complete=True, open_pr_numbers=numbers)])
                self.assertEqual(len(calls), 3)  # Export reuses inventory, never fetches again.

    def test_open_pulls_remain_independent_of_failed_issue_or_metadata_lookup(self):
        snap = {'repositories': ['r'], 'integrations': [{'repository': 'r', 'head_sha': 'main'}]}
        for failure in ('issues', 'metadata'):
            with self.subTest(failure=failure):
                def fetch(path):
                    if '/pulls?' in path:
                        return [{'number': 43}]
                    if (failure == 'issues') == ('/issues?' in path):
                        raise OSError('unavailable')
                    return [] if '/issues?' in path else {'open_issues_count': 1}
                row, = collect_repository_counts(snap, fetch)
                self.assertFalse(row['complete'])  # Existing aggregate count contract stays strict.
                self.assertIsNone(row['pull_requests'])
                self.assertTrue(row['pulls_complete'])
                self.assertEqual(row['open_pr_numbers'], [43])

    def test_partial_or_invalid_pull_inventory_is_unknown_not_zero(self):
        snap = {'repositories': ['r'], 'integrations': [{'repository': 'r', 'head_sha': 'main'}]}
        for mode in ('partial', 'duplicate', 'zero', 'bool', 'missing'):
            with self.subTest(mode=mode):
                def fetch(path):
                    if '/pulls?' in path:
                        if mode == 'partial':
                            if path.endswith('page=1'):
                                return [{'number': n} for n in range(1, 101)]
                            raise OSError('page 2 unavailable')
                        return {'duplicate': [{'number': 1}, {'number': 1}],
                                'zero': [{'number': 0}], 'bool': [{'number': True}],
                                'missing': [{}]}[mode]
                    return [] if '/issues?' in path else {'open_issues_count': 0}
                row, = collect_repository_counts(snap, fetch)
                self.assertFalse(row['complete'])
                self.assertFalse(row['pulls_complete'])
                self.assertIsNone(row['open_pr_numbers'])

    def test_rest_counts_paginate_issues_and_pulls_separately(self):
        snap = {'owner': 'demo', 'repositories': ['r'],
                'integrations': [{'repository': 'r', 'head_sha': 'main'}]}
        def fetch(path):
            if path == '/repos/demo/r':
                return {'open_issues_count': 102}
            if '/issues?' in path:
                return ([{'number': n} for n in range(100)] if path.endswith('page=1')
                        else [{'number': 100, 'pull_request': {}}, {'number': 101, 'pull_request': {}}])
            return [{'number': 100}, {'number': 101}]
        row, = collect_repository_counts(snap, fetch)
        self.assertEqual((row['open_issues_count'], row['get_issues'], row['issues'], row['pull_requests']),
                         (102, 102, 100, 2))
        self.assertTrue(row['complete'])
        self.assertEqual(len(row['sources']), 4)

    def test_partial_rest_inventory_does_not_promote_counts(self):
        snap = {'repositories': ['r'], 'integrations': [{'repository': 'r', 'head_sha': 'main'}]}
        def fetch(path):
            if '/pulls?' in path:
                raise OSError('unavailable')
            return [] if '/issues?' in path else {'open_issues_count': 3}
        row, = collect_repository_counts(snap, fetch)
        self.assertFalse(row['complete'])
        self.assertIsNone(row['issues'])
        self.assertIsNone(row['pull_requests'])

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
            (ui/'unified.template.html').write_text('<script type="application/json">/*__DATA__*/</script><script>/*__RESEARCH__*/</script>')
            (ui/'research-audit.json').write_text(json.dumps({'note': '</script><script>alert(2)</script>'}))
            (ui/'research-audit.js').write_text('/* research module */')
            (ui/'operations-data.json').write_text('[]')
            for n in ('shared-ui.js','unified.css','unified-model.js','unified-app.js'):(ui/n).write_text('')
            write_workspace(snap,ev,{'weeks':[]},stage,root)
            self.assertNotIn('</script><script>alert', (stage/'workspace.html').read_text())
            self.assertIn('/* research module */', (stage/'workspace.html').read_text())
            self.assertEqual(json.loads((stage/'unified-data.json').read_text())['research']['note'], '</script><script>alert(2)</script>')
            self.assertEqual(json.loads((stage/'unified-data.json').read_text())['records'][1]['title'],ev['records'][1]['title'])
if __name__=='__main__':unittest.main()




class OntologyExportTests(unittest.TestCase):
    def test_inventory_missing_or_partial_is_not_inferred_from_counts_or_records(self):
        snap, evidence = self.values()
        self.assertNotIn('open_pr_inventory', view_data(snap, evidence, {}, [])['aoi_integration'])
        for row in ({'complete': True, 'pull_requests': 0},
                    {'complete': False, 'pulls_complete': False, 'open_pr_numbers': [2]}):
            evidence['repository_counts'] = [dict(row, repository='Demo', checked='2026-11-15T00:00:00Z')]
            inventory, = view_data(snap, evidence, {}, [])['aoi_integration']['open_pr_inventory']
            self.assertFalse(inventory['complete'])
            self.assertIsNone(inventory['open_pr_numbers'])

    def values(self):
        snap = {'generated_at': '2026-11-15T00:00:00Z', 'owner': 'myon-bioinformatics',
                'records': [{'id': 'Demo#2', 'head_sha': 'a'*40, 'changed_files': ['module.py'],
                             'files_head': 'a'*40, 'depends_on': ['Demo#99']}],
                'integrations': [{'repository': 'Demo', 'branch': 'main', 'head_sha': 'b'*40}]}
        record = {'id': 'Demo#2', 'repository': 'Demo', 'number': 2, 'kind': 'pr',
                  'github_state': 'open', 'head_sha': 'a'*40, 'base_sha': 'c'*40,
                  'base_ref': 'main', 'head_ref': 'feature', 'draft': False, 'mergeable': None,
                  'future_field': {'uninterpreted': '保持'}, 'events': []}
        return snap, {'records': [record]}

    def test_reuses_current_files_and_config_dependencies_preserving_unknown(self):
        snap, evidence = self.values()
        result = view_data(snap, evidence, {'weeks': []}, [])
        row, = result['aoi_integration']['records']
        self.assertEqual(row['files'], ['module.py'])
        self.assertEqual(row['dependencies'], ['myon-bioinformatics/Demo#99'])
        self.assertIsNone(row['mergeable']); self.assertIsNone(row['contained_in_main'])
        self.assertEqual(row['source_record']['future_field']['uninterpreted'], '保持')
        self.assertEqual(result['plan']['order'], [])
        self.assertEqual(result['plan']['blocked'], ['Demo#2'])
        self.assertNotIn('changed_files', evidence['records'][0])

    def test_missing_run_sha_attempt_are_not_filled_with_pr_head(self):
        snap, evidence = self.values()
        evidence['records'][0]['events'] = [dict(type='ci', run_id=10, name='CI', conclusion='success')]
        row, = view_data(snap, evidence, {'weeks': []}, [])['aoi_integration']['records']
        self.assertIsNone(row['runs'][0]['head_sha'])
        self.assertIsNone(row['runs'][0]['run_attempt'])

    def test_jobs_require_exact_run_attempt_and_sha(self):
        snap, evidence = self.values()
        record = evidence['records'][0]
        record['events'] = [dict(type='ci', run_id=10, attempt=2, head_sha='a'*40),
                            dict(type='job', run_id=10, attempt=1, head_sha='a'*40, job_id=21),
                            dict(type='job', run_id=10, attempt=2, head_sha='b'*40, job_id=22),
                            dict(type='job', run_id=10, attempt=2, head_sha='a'*40, job_id=23)]
        row, = view_data(snap, evidence, {'weeks': []}, [])['aoi_integration']['records']
        self.assertEqual([j['id'] for j in row['runs'][0]['jobs']], [23])
        self.assertTrue(any('job対象SHA' in g['reason'] for g in row['gaps']))

    def test_check_run_is_not_actions_run_and_stale_files_are_unknown(self):
        snap, evidence = self.values(); snap['records'][0]['files_head'] = 'old'
        evidence['records'][0]['events'] = [dict(type='ci', id=5, head_sha='a'*40)]
        row, = view_data(snap, evidence, {'weeks': []}, [])['aoi_integration']['records']
        self.assertIsNone(row['files']); self.assertEqual(row['runs'], [])
        self.assertTrue(any('run ID' in g['reason'] for g in row['gaps']))

    def test_no_merge_completion_or_closed_state_inferred_from_absence(self):
        snap, evidence = self.values(); evidence['records'][0]['local'] = {'state': '完了'}
        row, = view_data(snap, evidence, {'weeks': []}, [])['aoi_integration']['records']
        self.assertIsNone(row['merged']); self.assertEqual(row['completion_records'], [])
        snap['records'] = []
        self.assertEqual(view_data(snap, evidence, {'weeks': []}, [])['aoi_integration']['records'], [])

    def test_independent_component_survives_cycle_and_missing(self):
        snap, evidence = self.values()
        evidence['records'] = [dict(evidence['records'][0], id=f'Demo#{n}', number=n,
                                    head_ref=None, base_ref=None) for n in range(1, 6)]
        deps = {1: ['Demo#2'], 2: ['Demo#1'], 3: ['Demo#1'], 4: ['Demo#99'], 5: []}
        snap['records'] = [dict(id=f'Demo#{n}', head_sha='a'*40, depends_on=deps[n]) for n in deps]
        result = view_data(snap, evidence, {'weeks': []}, [])
        self.assertEqual(result['plan']['order'], ['Demo#5'])
        self.assertEqual(result['plan']['blocked'], [f'Demo#{n}' for n in range(1, 5)])
        self.assertTrue(result['plan']['cycle'])

    def test_design_is_in_existing_audit_view_and_download_is_same_observation(self):
        app = (Path(__file__).resolve().parents[1] / 'site/ui/unified-app.js').read_text()
        self.assertIn('return html+convergenceBoard()+ontologyDesign()', app)
        self.assertIn("download('ontautology-snapshot.json',data.aoi_integration)", app)



class ConvergenceInventoryTests(unittest.TestCase):
    def test_open_membership_does_not_replace_independent_repository_facts(self):
        from scripts.unified_operations import ontology_export
        snap = self.data()
        before = ontology_export(snap, [], {})['inventory']
        counts = [dict(repository='Demo', checked=snap['generated_at'],
                       pulls_complete=True, open_pr_numbers=[])]
        result = ontology_export(snap, [], {}, counts)
        self.assertEqual(result['inventory'], before)
        self.assertEqual(result['open_pr_inventory'], [dict(repository='myon-bioinformatics/Demo',
            checked_at=snap['generated_at'], complete=True, open_pr_numbers=[])])

    def data(self):
        snapshot={'owner':'myon-bioinformatics','generated_at':'2026-11-15T00:00:00Z',
                  'repositories':['Demo'],'records':[],
                  'integrations':[{'repository':'Demo','branch':'main','head_sha':'a'*40}],
                  'main_workflows':{'runs':[dict(repository='Demo',run_id=1,attempt=2,name='CI',
                                                current_main=True,head_sha='a'*40,conclusion='failure',
                                                status='completed',jobs=[dict(id=2,name='test',conclusion='failure')])]}}
        return snapshot

    def test_zero_open_pr_still_exports_exact_main_and_run_attempt_job(self):
        from scripts.unified_operations import ontology_export
        snap=self.data();result=ontology_export(snap,[],{})
        self.assertEqual(result['records'],[])
        repo,=result['inventory'];self.assertEqual(repo['repo'],'myon-bioinformatics/Demo')
        self.assertEqual(repo['main'],'a'*40);self.assertEqual(repo['state'],'ブロッカー')
        run,=repo['main_runs'];self.assertEqual((run['id'],run['run_attempt'],run['jobs'][0]['id']),(1,2,2))
        self.assertTrue(any('未検証' in reason for reason in repo['gaps']))

    def test_old_or_moving_main_run_is_not_attached_to_current_inventory(self):
        from scripts.unified_operations import ontology_export
        for update in ({'head_sha':'b'*40},{'current_main':False}):
            snap=self.data();snap['main_workflows']['runs'][0].update(update)
            repo,=ontology_export(snap,[],{})['inventory']
            self.assertEqual(repo['main_runs'],[])
            self.assertEqual(repo['state'],'調査中')

    def test_only_observed_pins_and_pure_issues_are_exported(self):
        from scripts.unified_operations import ontology_export
        snap=self.data();snap['records']=[{'id':'Demo#i3'}]
        snap['vendor_pins']=[dict(repository='Demo',source_repository='other/source',commit='c'*40,destination='vendor/source.py')]
        issues=[dict(id='Demo#i3',repository='Demo',kind='issue',number=3,title='Issue',url='https://github.com/myon-bioinformatics/Demo/issues/3')]
        repo,=ontology_export(snap,issues,{})['inventory']
        self.assertEqual(len(repo['issues']),1);self.assertEqual(repo['pin_records'][0]['commit'],'c'*40)
        self.assertNotIn('artifacts',repo['main_runs'][0])


class TransferRegistryTests(unittest.TestCase):
    def test_registry_uses_observed_otl_sha_and_survives_flutter_absence(self):
        import base64
        from scripts.unified_operations import collect_transfer_registry, ontology_export
        doc={'schema':'integration-audit/3','captured_at':'2026-11-15T01:00:00Z','records':[],
             'transfers':[{'id':'flutter-feature','state':'調査中'}],
             'transfer_scope':{'source_repository':'myon-bioinformatics/flutter_navigation_basic','inventory_complete':False}}
        snapshot={'owner':'myon-bioinformatics','generated_at':'2026-11-16T01:00:00Z','records':[],
                  'repositories':['OnTautoLogy'],'integrations':[{'repository':'OnTautoLogy','branch':'main','head_sha':'a'*40}]}
        def fetch(path):
            self.assertTrue(path.endswith('?ref='+'a'*40))
            self.assertNotIn('/flutter_navigation_basic/',path)
            return {'sha':'blob','content':base64.b64encode(json.dumps(doc).encode()).decode()}
        snapshot['transfer_registry']=collect_transfer_registry(snapshot,fetch)
        result=ontology_export(snapshot,[],{})
        self.assertEqual(result['records'],[])
        self.assertEqual(result['transfers'],doc['transfers'])
        self.assertFalse(result['transfer_scope']['inventory_complete'])
        self.assertEqual(result['transfer_scope']['registry_sha'],'a'*40)

    def test_unavailable_or_invalid_registry_is_not_reported_as_no_pending(self):
        from scripts.unified_operations import collect_transfer_registry, ontology_export
        snapshot={'generated_at':'2026-11-15T01:00:00Z','records':[],'integrations':[]}
        result=collect_transfer_registry(snapshot,lambda _:self.fail('must not fetch mutable main'))
        self.assertEqual(result['status'],'未取得')
        snapshot['transfer_registry']=result
        self.assertEqual(ontology_export(snapshot,[],{})['transfer_scope']['registry_status'],'未取得')
        snapshot['integrations']=[{'repository':'OnTautoLogy','branch':'main','head_sha':'a'*40}]
        def unavailable(_):raise RuntimeError('missing registry')
        self.assertEqual(collect_transfer_registry(snapshot,unavailable)['status'],'未取得')

"""Publication contracts: incomplete data never replaces the deployed artifact."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import ci_status as ci
from tools import build_audit_pages as build


def snapshot(at='2026-11-08T00:00:00+00:00'):
    return {'schema':'parent-audit/1', 'generated_at':at, 'owner':ci.OWNER, 'repositories':['Demo'],
            'ignored_checks':[], 'records':[{'id':'Demo#1','repository':'Demo','number':1,'kind':'pr',
            'url':'https://github.com/myon-bioinformatics/Demo/pull/1','title':'test <script>',
            'head_sha':'a'*40,'head_ref':'feat','base_ref':'main','ci':'COMPLETE','checks':[],
            'review':0,'review_complete':True,'state':'','notes':'','draft':False,'depends_on':[],'conflict':False}],
            'summary':{'repositories':1,'pull_requests':1,'issues':0,'ci_failed':0,'conflicts':0,'stacked':0,'done':0,'conventions':0},
            'errors':[], 'truncated':[], 'conventions':[], 'changes':{'new':[],'gone':[],'changed':[]}}


class PublicationTests(unittest.TestCase):
    def test_404_only_is_optional_and_other_http_errors_stop_build(self):
        for code in (404,403,429,503):
            with self.subTest(code=code), patch.object(build.urllib.request,'urlopen',side_effect=urllib.error.HTTPError('https://example.test',code,'error',None,None)):
                if code == 404:
                    self.assertIsNone(build.previous_json('https://example.test/data.json'))
                else:
                    with self.assertRaises(urllib.error.HTTPError):build.previous_json('https://example.test/data.json')

    def test_corrupt_or_oversized_previous_is_rejected(self):
        for body in (b'<html>error</html>', b'[]', b'x'*101):
            response=io.BytesIO(body)
            with patch.object(build.urllib.request,'urlopen',return_value=response), patch.object(build,'MAX_BYTES',100):
                with self.assertRaises(ValueError):build.previous_json('https://example.test/data.json')

    def test_complete_zero_open_prs_is_valid_but_empty_inventory_is_not(self):
        snap=snapshot();snap['records']=[]
        build.ensure_complete(snap)
        snap['repositories']=[]
        with self.assertRaises(ValueError):build.ensure_complete(snap)

    def test_incomplete_checks_reviews_lists_and_errors_keep_existing_output(self):
        for field,value in [('errors',[{'error':'RateLimit'}]),('truncated',['Demo']),('ci','UNKNOWN'),('review_complete',False)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'dist';out.mkdir();(out/'index.html').write_text('good deployment')
                snap=snapshot()
                if field in ('errors','truncated'):snap[field]=value
                else:snap['records'][0][field]=value
                with patch.object(build,'public_repositories',return_value=['Demo']), patch.object(ci,'audit',return_value=snap):
                    with self.assertRaises(ValueError):build.build(out)
                self.assertEqual((out/'index.html').read_text(),'good deployment')
                self.assertFalse((out/'data.json').exists())

    def test_invalid_previous_schema_preserves_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'dist';out.mkdir();(out/'index.html').write_text('old')
            before=Path(tmp)/'before.json';before.write_text('{"items":[]}')
            with self.assertRaises(ValueError):build.build(out,previous=str(before))
            self.assertEqual((out/'index.html').read_text(),'old')

    def test_integration_summary_preserves_accessible_scroll_dashboard(self):
        from html.parser import HTMLParser
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.rows = []
            def handle_starttag(self, tag, attrs):
                self.rows.append((tag, dict(attrs)))
        html = build.observatory_page()
        self.assertFalse((Path(__file__).resolve().parents[1] / "site/ui/index.html").exists())   # assembled, not hand-written
        self.assertEqual(html.count('data-view="history"'), 3)                                    # side, top, footer: one VIEWS list
        self.assertIn('<body data-ui-theme="green">', html)
        self.assertEqual(html, build.observatory_page(theme=build.THEME))
        parser = Elements()
        parser.feed(html)
        ids = [attrs.get("id") for _, attrs in parser.rows if "id" in attrs]
        self.assertEqual(ids.count("integration-summary"), 1)
        self.assertLess(ids.index("integration-summary"), ids.index("content"))
        content = next(attrs for _, attrs in parser.rows if attrs.get("id") == "content")
        self.assertEqual(content["tabindex"], "-1")
        labels = {attrs.get("data-scroll-label") for _, attrs in parser.rows}
        self.assertTrue({"上部ナビゲーション", "監査の操作", "フッターナビゲーション"} <= labels)
        self.assertIn('class="skip-link" href="#content"', html)
        self.assertIn('href="pswb-shell.css"', html)
        self.assertIn('src="scroll-controls.js"', html)

    def test_success_builds_relocatable_ui_report_data_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'dist'
            with patch.object(build,'public_repositories',return_value=['Demo']),patch.object(ci,'audit',return_value=snapshot()) as audit:
                build.build(out)
            self.assertTrue(audit.call_args.kwargs['strict'])
            for path in ('index.html','app.js','style.css','audit-model.js','scroll-controls.js','pswb-shell.css','data.json','report.html','history.json','pages-source.zip','.nojekyll'):
                self.assertTrue((out/path).is_file(),path)
            html=(out/'index.html').read_text()
            self.assertIn('src="audit-model.js"',html)
            self.assertIn('href="component-ops/"', html)
            for name in ('index.html', 'app.js', 'evidence.json'):
                self.assertEqual((out/'component-ops'/name).read_bytes(), (build.ROOT/'site/component-ops'/name).read_bytes())
            self.assertNotIn('src="/',html)
            ui = ci._load_web_ui()
            self.assertEqual((out/'pswb-shell.css').read_text(), ui.css_source('tokens.css') + ui.css_source('shell.css'))
            self.assertEqual(html, build.observatory_page())
            self.assertIn(f'body[data-ui-theme="{build.THEME}"]', (out/'pswb-shell.css').read_text())      # the page's palette is PSWB's
            style = (out/'style.css').read_text()
            self.assertIn('--green:var(--ui-accent)', style)
            self.assertNotIn(':root{--ink', style)
            import zipfile
            with zipfile.ZipFile(out/'pages-source.zip') as archive:
                self.assertEqual(archive.read('site/ui/pswb-shell.css'), (out/'pswb-shell.css').read_bytes())
                self.assertEqual(archive.read('site/component-ops/index.html'), (out/'component-ops/index.html').read_bytes())
            self.assertIn('&lt;script&gt;',(out/'report.html').read_text())
            self.assertEqual(json.loads((out/'data.json').read_text())['schema'],'parent-audit/1')
            self.assertEqual(len(json.loads((out/'history.json').read_text())['weeks']),1)

    def test_fleet_section_is_published_with_the_snapshot_and_never_blocks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'dist'
            seen = []
            report = {'schema': 'vendor-fleet-report/1', 'status': 'unavailable', 'error': 'OSError'}
            with patch.object(build, 'public_repositories', return_value=['Demo']), patch.object(ci, 'audit', return_value=snapshot()):
                build.build(out, fleet=lambda names, before: seen.append((names, before)) or report)
            self.assertEqual(seen, [(['Demo'], None)])
            self.assertEqual(json.loads((out / 'data.json').read_text())['fleet'], report)
            self.assertIn('取得できませんでした', (out / 'report.html').read_text())
        import vendor_fleet_plan
        with patch.object(vendor_fleet_plan, 'report', side_effect=OSError('offline')):
            self.assertEqual(build.fleet_report(['Demo'], None), report)
        full = {'schema': 'vendor-fleet-report/1', 'generated_at': '2026-11-09T00:00:00+00:00', 'facts': [{'key': 'k'}],
                'targets': {'repositories': [{'repository': 'app', 'category': 'target', 'repository_id': 1, 'revision': 'a' * 40}]},
                'graph': {'edges': [{'kind': 'vendor', 'source': 'tool:tool.py', 'target': 'app:vendor/tool.py',
                                     'lock': 'app:vendor.lock.json', 'pin': 'b' * 40, 'blob': 'c' * 40}], 'kinds': {'vendor': 1}}}
        section, graph = build.split_fleet(full)
        self.assertNotIn('edges', section['graph'])
        self.assertNotIn('facts', section)
        self.assertEqual(section['graph']['edges_omitted'], 1)
        self.assertTrue(any(g.get('vd') == ['tool/tool.py'] for g in graph['@graph']))
        self.assertEqual(build.split_fleet(report), (report, None))

    def test_renderer_failure_preserves_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'dist';out.mkdir();(out/'index.html').write_text('old')
            with patch.object(build,'public_repositories',return_value=['Demo']),patch.object(ci,'audit',return_value=snapshot()),patch.object(ci,'render_audit_html',side_effect=RuntimeError('bad render')):
                with self.assertRaises(RuntimeError):build.build(out)
            self.assertEqual((out/'index.html').read_text(),'old')

    def test_first_good_week_is_retained_and_jst_monday_rolls_week(self):
        first=snapshot('2026-11-08T14:59:00+00:00') # Sunday 23:59 JST
        hist=build.weekly_history(first,None)
        same=copy.deepcopy(first);same['records'][0]['head_sha']='b'*40
        self.assertEqual(build.weekly_history(same,hist),hist)
        next_week=snapshot('2026-11-08T15:00:00+00:00') # Monday 00:00 JST
        after=build.weekly_history(next_week,hist)
        self.assertEqual([x['week'] for x in after['weeks']],['2026-W45','2026-W46'])
        self.assertEqual(hist['weeks'][0]['snapshot']['records'][0]['head_sha'],'a'*40)

    def test_history_is_bounded_to_latest_thirteen_weeks(self):
        hist={'schema':'parent-audit-history/1','weeks':[{'week':f'2026-W{x:02d}','at':'old','snapshot':snapshot()} for x in range(1,45)]}
        result=build.weekly_history(snapshot(),hist)
        self.assertEqual(len(result['weeks']),13)
        self.assertEqual(result['weeks'][-1]['week'],'2026-W45')
        self.assertEqual(len(hist['weeks']),44)

    def test_scope_uses_public_inventory_and_never_names_unavailable_repos(self):
        with patch.object(ci.ghi,'owner_repositories',return_value=[{'name':'Demo'}]) as inventory:
            self.assertEqual(build.public_repositories(),['Demo'])
            self.assertTrue(inventory.call_args.kwargs['include_forks'])
            with self.assertRaisesRegex(ValueError,'^requested scope includes an unavailable repository$'):
                build.public_repositories(['hidden-private-name'])


class StrictObservationTests(unittest.TestCase):
    def collect(self, *, head_changed=False, checks_complete=True, reviews_complete=True, issues_truncated=False, statuses=None, files=None, file_count=None, base_changed=False):
        pull={'number':1,'title':'test','draft':False,'head_sha':'z'*40,'head_ref':'old','base_ref':'main','updated_at':'t','url':'https://github.com/myon-bioinformatics/Demo/pull/1'}
        detail={'state':'open','head':{'sha':'a'*40,'ref':'feat'},'base':{'ref':'main','sha':'c'*40},'mergeable':True,'changed_files':len(files or []) if file_count is None else file_count}
        final=copy.deepcopy(detail)
        if head_changed:final['head']['sha']='b'*40
        if base_changed:final['base']['sha']='d'*40
        with patch.object(ci.ghi,'pull_requests',return_value={'pull_requests':[pull],'truncated':False}),patch.object(ci.ghi,'issues',return_value={'issues':[],'truncated':issues_truncated}),patch.object(ci,'get',side_effect=[detail,final]),patch.object(ci.ghi,'checks_for_sha',return_value={'complete':checks_complete,'checks':[]}) as checks,patch.object(ci.ghi,'pages',side_effect=lambda path: (files or []) if path.endswith('/files') else (statuses or [])),patch.object(ci.ghi,'review_threads',return_value={'complete':reviews_complete,'threads':[{'is_resolved':False}]}):
            result=ci.audit_repository('Demo',strict=True)
            self.assertEqual(checks.call_args.args[1],'a'*40,'use detail head, not the outdated listing head')
            return result

    def test_reobserves_detail_head_and_counts_actual_unresolved_threads(self):
        rows,partial=self.collect()
        self.assertFalse(partial);self.assertEqual(rows[0]['head_sha'],'a'*40)
        self.assertEqual(rows[0]['review'],1);self.assertTrue(rows[0]['review_complete'])

    def test_head_move_or_incomplete_check_review_observation_is_rejected(self):
        for kwargs in ({'head_changed':True},{'checks_complete':False},{'reviews_complete':False}):
            with self.subTest(kwargs=kwargs),self.assertRaises(RuntimeError):self.collect(**kwargs)

    def test_issue_pagination_is_not_silently_ignored(self):
        self.assertTrue(self.collect(issues_truncated=True)[1])

    def test_legacy_status_failure_and_retry_latest_context_are_aggregated(self):
        rows,_=self.collect(statuses=[{'id':2,'context':'external','state':'failure','target_url':'https://github.com/test'}])
        self.assertEqual(rows[0]['ci'],'FAILED');self.assertEqual(rows[0]['checks'][0]['url'],'https://github.com/test')
        rows,_=self.collect(statuses=[{'id':3,'context':'external','state':'success'},{'id':2,'context':'external','state':'failure'}])
        self.assertEqual(rows[0]['ci'],'COMPLETE')

    def test_changed_files_have_exact_head_and_base_and_missing_page_rejects(self):
        rows,_=self.collect(files=[{'filename':'.github/workflows/ci.yml'},{'filename':'app.py'}])
        self.assertEqual(rows[0]['changed_files'],['.github/workflows/ci.yml','app.py'])
        self.assertEqual(rows[0]['files_head'],rows[0]['head_sha'])
        self.assertEqual(rows[0]['base_sha'],'c'*40)
        for args in ({'file_count':2,'files':[{'filename':'app.py'}]}, {'base_changed':True}):
            with self.subTest(args=args),self.assertRaises(RuntimeError):self.collect(**args)

    def test_truncated_workflow_tree_stops_publication(self):
        with patch.object(ci,'audit_repository',return_value=([],False)),patch.object(ci,'conventions_repository',return_value=([],True)):
            snap=ci.audit(['Demo'],strict=True)
        self.assertEqual(snap['truncated'],['Demo'])
        with self.assertRaises(ValueError):build.ensure_complete(snap)


import unittest
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("audit_integration_plan", Path(__file__).resolve().parents[1] / "site/ui/integration_plan.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
calculate = module.calculate


def pr(n,base='main',head=None,**extra):
    return dict(id=f'A#{n}',kind='pr',repository='A',base_ref=base,head_ref=head or f'b{n}',**extra)


class PlanTests(unittest.TestCase):
    def test_stack(self):
        p=calculate({'records':[pr(2,base='b1'),pr(1)]})
        self.assertEqual(p['order'],['A#1','A#2'])

    def test_cycle_and_independent_work(self):
        p=calculate({'records':[pr(1,depends_on=['A#2']),pr(2,depends_on=['A#1']),pr(3)]})
        self.assertTrue(p['cycle']);self.assertEqual(p['order'],['A#3'])
        self.assertEqual(p['blocked'],['A#1','A#2'])

    def test_missing_dependency_propagates(self):
        p=calculate({'records':[pr(1,depends_on=['missing#2']),pr(2,base='b1')]})
        self.assertEqual(p['unsafe_missing'],['A#1','A#2'])

    def test_merged_dependency_and_draft_alias(self):
        p=calculate({'records':[pr(1,merged=True),pr(2)],'drafts':{'A#2':{'dependencies':['A#1']}}})
        self.assertEqual(p['order'],['A#2']);self.assertEqual(p['waiting'],{})

    def test_same_branch_in_other_repository_is_not_dependency(self):
        r=pr(2,base='b1');r['repository']='B';r['id']='B#2'
        self.assertEqual(calculate({'records':[pr(1),r]})['waiting'],{})

class ClosedPlanTests(unittest.TestCase):
    def test_closed_unmerged_is_not_scheduled(self):
        p=calculate({'records':[pr(1,github_state='closed'),pr(2,depends_on=['A#1']),pr(3)]})
        self.assertNotIn('A#1',p['order'])
        self.assertEqual(p['unsafe_missing'],['A#2'])
        self.assertEqual(p['actionable_order'],['A#3'])

    def test_old_merged_branch_is_not_inferred_as_dependency(self):
        p=calculate({'records':[pr(1,merged=True),pr(2,base='b1')]})
        self.assertEqual(p['waiting'],{})
        self.assertEqual(p['order'],['A#2'])

class TaskPlanTests(unittest.TestCase):
    def test_explicit_tasks_do_not_infer_dependencies_from_missing_branch_names(self):
        result=calculate({'records':[pr(1)],'dependency_tasks':[
            {'id':'pin-update','kind':'pin-update','depends_on':['A#1']},
            {'id':'independent-task','kind':'task','depends_on':[]}]})
        self.assertFalse(result['cycle'])
        self.assertEqual(result['edges']['independent-task'],[])
        self.assertLess(result['order'].index('A#1'),result['order'].index('pin-update'))

if __name__ == '__main__':
    unittest.main()

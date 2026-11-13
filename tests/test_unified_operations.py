import json
from pathlib import Path
import tempfile
import unittest
from tools.unified_operations import view_data, write_workspace

class UnifiedOperationsTests(unittest.TestCase):
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

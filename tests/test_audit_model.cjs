const {test}=require('node:test');
const assert=require('node:assert/strict');
const m=require('../site/ui/audit-model.js');
const url='https://github.com/myon-bioinformatics/Aoi/pull/167';
const observation={id:'Aoi#167',repository:'Aoi',number:167,kind:'pr',url,title:'<script>',head_sha:'a'.repeat(40),ci:'COMPLETE',review:0,review_complete:true,checks:[],state:''};
test('canonical parent snapshot retains complete zero reviews and exact head',()=>{
 const [r]=m.normalize({records:[observation],generated_at:'2026-11-08T00:00:00Z'});
 assert.equal(r.id,url);assert.equal(r.head,'a'.repeat(40));assert.equal(r.review,0);assert.equal(r.ci,'成功');assert.equal(r.state,'調査中');
 assert.equal(r.checked,'2026-11-08T00:00:00Z');
});
test('unknown reviews never become zero and failed check evidence is exposed',()=>{
 const [r]=m.normalize({records:[{...observation,review_complete:false,ci:'FAILED',checks:[{name:'test',conclusion:'failure',url:'https://github.com/check'}]}]});
 assert.equal(r.review,null);assert.match(r.failure,/test https/);assert.equal(r.ci,'失敗');
});
test('drafts preserve user fields but cannot overwrite observed CI/head/date',()=>{
 const r=m.normalize({records:[observation]})[0];
 const result=m.mergeRecord(r,{[url]:{state:'完了',notes:'keep',head:'old',ci:'失敗',checked:'old',priority:'1'}});
 assert.equal(result.ci,'成功');assert.equal(result.head,r.head);assert.equal(result.notes,'keep');assert.equal(result.priority,'1');assert.equal(result.state,'完了');
});
test('parent short record IDs can migrate without changing observation identity',()=>{
 const r=m.normalize({records:[observation]})[0];assert.equal(m.mergeRecord(r,{'Aoi#167':{state:'done'}}).state,'完了');
});
test('legacy Sites arrays remain importable',()=>{
 assert.equal(m.normalize([{id:url,repo:'Aoi',number:167,type:'PR',ci:'成功'}])[0].repo,'Aoi');
});
test('malformed URLs, duplicate rows and invalid records are rejected atomically',()=>{
 assert.throws(()=>m.normalize({records:[{...observation,url:'javascript:alert(1)'}]}));
 assert.throws(()=>m.normalize({records:[observation,observation]}));
 assert.throws(()=>m.importBundle({records:{[url]:{notes:3}}}));
 assert.throws(()=>m.importBundle({records:{'__proto__#bad':{notes:'x'}}}));
 assert.deepEqual(m.importBundle({records:{[url]:{notes:'safe',head:'forged'}}}),{[url]:{notes:'safe'}});
});
test('completion evidence survives disappearing from open list without becoming current CI',()=>{
 const item=m.normalize({records:[observation]})[0];
 const archive={[url]:item},drafts={[url]:{state:'完了',notes:'accepted'}};
 assert.deepEqual(m.completedRecords([item],archive,drafts),[]);
 const result=m.completedRecords([],archive,drafts);
 assert.equal(result.length,1);assert.equal(result[0].archived,true);assert.equal(result[0].head,item.head);
 assert.deepEqual(m.completedRecords([],archive,{[url]:{state:'対応中'}}),[]);
});
test('review and CI failures stay first even with a manually lowered priority',()=>{
 assert.equal(m.priority({ci:'失敗',review:0,priority:'3'}),1);
 assert.equal(m.priority({ci:'成功',review:1,priority:'3'}),1);
 assert.equal(m.priority({ci:'成功',review:0,labels:['bug']}),2);
});
test('dependency notes round-trip without overwriting observed date or topology',()=>{
 const r=m.normalize({records:[{...observation,depends_on:['Aoi#166']}],dependency_plan:{order:['Aoi#166','Aoi#167'],waiting:{'Aoi#167':['Aoi#166']}}})[0];
 assert.deepEqual(r.dependencies,['Aoi#166']);assert.equal(r.integration_rank,2);
 const bundle=m.importBundle({records:{[url]:{dependencies:['other#1'],conflicts:'file overlap',order:'after check',noted_at:'2026-11-09',checked:'fake'}}});
 const result=m.mergeRecord(r,bundle);
 assert.equal(result.checked,r.checked);assert.equal(result.noted_at,'2026-11-09');
 assert.deepEqual(result.observed_dependencies,['Aoi#166']);assert.deepEqual(result.dependencies,['other#1']);
 assert.throws(()=>m.importBundle({records:{[url]:{dependencies:[3]}}}));
});
test('weekly comparison excludes timestamps and work notes but detects review and base changes',()=>{
 const prev=[{id:url,head:'a',ci:'成功',review:0,base:'main',checked:'old'}];
 assert.equal(m.diff([{...prev[0],checked:'new',notes:'new'}],prev).changed.length,0);
 assert.equal(m.diff([{...prev[0],review:1}],prev).changed.length,1);
 assert.equal(m.diff([{...prev[0],base:'stack'}],prev).changed.length,1);
 assert.equal(m.diff([],prev).gone.length,1);
});
test('previous week baseline uses JST Monday and ignores current-week/manual snapshots',()=>{
 const weeks=[{at:'2026-11-08T14:59:00Z',week:'2026-W45'},{at:'2026-11-08T15:00:00Z',week:'2026-W46'}];
 assert.equal(m.weekBaseline(weeks,'2026-11-09T03:00:00Z').week,'2026-W45');
 assert.equal(m.weekBaseline([weeks[1]],'2026-11-09T03:00:00Z'),undefined);
});

test('actual conflicts cannot be demoted by a manual priority',()=>{
 assert.equal(m.priority({mergeable:false,ci:'成功',review:0,priority:'3'}),1);
});
test('revalidation evidence round-trips while observed hashes stay immutable',()=>{
 const r=m.normalize({records:[observation]})[0];
 const draft=m.importBundle({records:{[url]:{revalidated_head:'abc',revalidated_base:'def',revalidated_at:'2026-11-09',revalidation_notes:'check URL',base_sha:'forged'}}});
 const merged=m.mergeRecord(r,draft);
 assert.equal(merged.revalidated_head,'abc');assert.notEqual(merged.base_sha,'forged');
});
test('overlap only compares known file inventories in the same repository',()=>{
 const fs=require('node:fs'),vm=require('node:vm');const context=vm.createContext({});
 vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../site/ui/integration.js'),'utf8'),context);
 assert.deepEqual(Array.from(context.pairOverlap({repo:'A',changed_files:['a','.github/workflows/ci.yml']},{repo:'A',changed_files:['.github/workflows/ci.yml','b']})),['.github/workflows/ci.yml']);
 assert.equal(context.pairOverlap({repo:'A',changed_files:[]},{repo:'B',changed_files:[]}),null);
 assert.equal(context.pairOverlap({repo:'A'},{repo:'A',changed_files:[]}),null);
 assert.equal(context.revalidation({revalidated_at:'today',head:'a',base_sha:'b',revalidated_head:'a',revalidated_base:'b'}),'同一head/baseの確認記録あり');
 assert.match(context.revalidation({revalidated_at:'today',head:'a',base_sha:'c',revalidated_head:'a',revalidated_base:'b'}),/再検証必要/);
 assert.match(context.revalidation({revalidated_at:'today',head:'a'}),/未取得/);
});

require('./test_github_evidence.cjs');


test('legacy work states migrate without changing GitHub status',()=>{for(const [before,after] of Object.entries({'未着手':'調査中','対応中':'着手中','ブロック':'ブロッカー','統合済み':'完了'})){const r=m.normalize({records:[{...observation,state:before,github_state:'open'}]})[0];assert.equal(r.state,after);assert.equal(r.github_state,'open')}});

require('./test_workspace.cjs');

test('unified evidence coverage expires when the PR head moves',()=>{
 const u=require('../site/ui/unified-model.js'),head='a'.repeat(40),old='b'.repeat(40);
 const r={kind:'pr',head_sha:head,main_sha:head,metadata_checked:'2026-11-13',comment_coverage:{},events:[],github_state:'open',review_coverage:{head_sha:old},current_collection:{head_sha:old}};
 assert(u.summarize(r).gaps.includes('レビュー網羅'));
 assert(u.summarize(r).gaps.includes('run/attempt/job再取得'));
});
test('unified CI keeps workflow events distinct',()=>{
 const u=require('../site/ui/unified-model.js'),head='a'.repeat(40);
 const s=u.summarize({kind:'pr',head_sha:head,github_state:'open',events:[
  {type:'ci',head_sha:head,workflow_id:1,event:'push',run_number:2,status:'completed',conclusion:'success'},
  {type:'ci',head_sha:head,workflow_id:1,event:'schedule',run_number:1,status:'completed',conclusion:'failure'}]});
 assert.equal(s.effective.length,2);assert(s.flags.includes('CI失敗'));
});

test('closed migration blockers remain ahead of ordinary open PRs',()=>{
 const u=require('../site/ui/unified-model.js');
 const r={kind:'pr',github_state:'closed',merged:true,events:[],migration:{state:'ブロッカー'}};
 assert.equal(u.summarize(r).priority,0);
 assert.equal(u.summarize(r).open,false);
 assert.ok(u.summarize(r).flags.includes('統合ブロッカー'));
});

test('approval waiting requires an explicit requirement and comments do not dismiss a review',()=>{
 const u=require('../site/ui/unified-model.js'),r={kind:'pr',head_sha:'head',github_state:'open',events:[]};
 assert.ok(!u.summarize(r).flags.includes('承認待ち'));
 assert.ok(u.summarize({...r,approval_required:true}).flags.includes('承認待ち'));
 const events=[{type:'review',actor:'a',state:'APPROVED',review_sha:'head',at:'2026-11-12'},
               {type:'review',actor:'a',state:'COMMENTED',review_sha:'head',at:'2026-11-13'}];
 assert.ok(u.summarize({...r,approval_required:true,events}).approved);
 assert.ok(!u.summarize({...r,approval_required:true,events}).flags.includes('承認待ち'));
});

function compactionContext(saved={}) {
 const fs=require('node:fs'),vm=require('node:vm'),nodes=new Map();
 const get=s=>{if(!nodes.has(s))nodes.set(s,{value:'',innerHTML:'',textContent:'',dispatchEvent(){}});return nodes.get(s);};
 const ctx=vm.createContext({document:{querySelector:get},localStorage:{getItem:k=>saved[k]??null},Intl,Date,Event:class{}});
 vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../site/ui/compaction.js'),'utf8'),ctx);
 return {run:code=>vm.runInContext(code,ctx),nodes};
}
test('compaction keeps legacy records without treating old ratios as current measurements',()=>{
 const legacy={id:'old',date:'2026-11-14',title:'constraint',type:'モデル別結果',status:'有効',enabled:'8/10',disabled:'9/10',declined:'2/12',detail:'keep',url:'https://example.test/evidence'};
 const c=compactionContext({'compaction-lab-v1':JSON.stringify([legacy])});
 assert.equal(c.run('records.length'),1);
 assert.equal(c.run('records[0].legacyEnabled'),'8/10');
 assert.equal(c.run('records[0].legacyDisabled'),'9/10');
 assert.equal(c.run('records[0].legacyDeclined'),'2/12');
 assert.equal(c.run('records[0].regression'),'keep');
 assert.equal(c.run('records[0].source'),legacy.url);
 assert.equal(c.run('records[0].correct'),'');
 assert.match(c.run('pairReport(records)'),/比較可能.*ありません/);
 assert.equal(c.run('validate(records[0])'),true);
 const current=compactionContext({'compaction-lab-v1':JSON.stringify([legacy]),'compaction-lab-v2':'[]'});
 assert.equal(current.run('records.length'),0); // no resurrecting deleted v2 rows
});
test('compaction excludes unmeasured and disabled runs from numerical claims',()=>{
 const c=compactionContext();
 c.run("var sample=normalize({id:'x',name:'case',date:'2026-11-14',mode:'未実測'})");
 assert.equal(c.run('validate(sample)'),true);
 assert.equal(c.run("validate({...sample,correct:'0'})"),false);
 assert.equal(c.run("validate({...sample,mode:'有効',execution:'エラー',correct:'0',total:'1'})"),false);
 assert.equal(c.run("validate({...sample,mode:'有効',execution:'完了',correct:'3',total:'2'})"),false);
 assert.equal(c.run("validate({...sample,mode:'無効',execution:'完了',declined:'0',attempted:'1'})"),false);
 assert.equal(c.run("validate({...sample,name:{bad:1}})"),false);
 assert.equal(c.run("pct('','1')"),'—');
 assert.match(c.run("pct('0','1')"),/0\.0%/);
 assert.equal(c.run("link('javascript:alert(1)')"),'');
 assert.equal(c.run("esc('<script>')"),'&lt;script&gt;');
});
test('compaction only compares paired cases with complete matching conditions',()=>{
 const c=compactionContext();
 c.run("var same=Object.fromEntries(identity.map(k=>[k,'fixed']));var off={...normalize({id:'off',date:'2026-11-14',name:'case',mode:'無効'}),...same,execution:'完了',correct:'1',total:'1'};var on={...off,id:'on',mode:'有効',correct:'0'}");
 assert.match(c.run('pairReport([on,off])'),/回帰候補/);
 assert.doesNotMatch(c.run("pairReport([{...on,tokenizer:'other'},off])"),/正解数差/);
 assert.doesNotMatch(c.run("pairReport([{...on,caseId:''},off])"),/正解数差/);
 assert.doesNotMatch(c.run("pairReport([{...on,total:'2'},off])"),/正解数差/);
});

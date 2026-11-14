const {test}=require('node:test');
const assert=require('node:assert/strict');
const compaction=require('../site/ui/compaction-review.js');
test('compaction automatically migrates old storage without mutating its recovery copy',()=>{
 const old=JSON.stringify([{id:'old',date:'2026-11-14',type:'保留事項',status:'未測定',title:'retain'}]);
 const store={getItem:k=>k==='compaction-lab-v1'?old:null};
 const migrated=compaction.loadStored(store);
 assert.equal(migrated.migrated,true);assert.equal(migrated.records[0].title,'retain');
 assert.equal(store.getItem('compaction-lab-v1'),old);
 const current={getItem:k=>k==='compaction-lab-v2'?JSON.stringify({version:2,records:[]}):old};
 assert.deepEqual(compaction.loadStored(current),{records:[],migrated:false});
 assert.throws(()=>compaction.loadStored({getItem:()=>'{bad json'}));
});
test('compaction measurements keep unavailable/error separate and validate denominators',()=>{
 const r={id:'measure',date:'2026-11-14T10:28',type:'モデル別結果',execution:'完了',mode:'有効',title:'case',correct:'1',judged:'2'};
 assert.equal(compaction.validate(r).correct,'1');
 for(const update of [{correct:'3'},{correct:'1.5'},{execution:'未実行'},{execution:'エラー'},{declines:'3',attempts:'2'},{url:'javascript:alert(1)'},{mode:'不明'}])assert.throws(()=>compaction.validate({...r,...update}));
});
test('compaction v1 retains aggregate values as legacy evidence without inventing comparable measurements',()=>{
 const r=compaction.legacy({id:'old',date:'2026-11-14',type:'モデル別結果',status:'辞退',title:'old',enabled:'8/10',disabled:'9/10',declined:'2/12'});
 assert.equal(r.mode,'辞退');assert.equal(r.execution,'完了');assert.equal(r.correct,'');
 assert.match(r.detail,/8\/10/);assert.match(r.detail,/9\/10/);assert.match(r.detail,/2\/12/);
 const failed=compaction.legacy({id:'error',date:'2026-11-14',type:'回帰',status:'失敗',title:'error'});
 assert.equal(failed.execution,'エラー');assert.equal(failed.mode,'対象外');
 assert.throws(()=>compaction.legacy({id:'unsafe',date:'2026-11-14',title:'bad',url:'data:text/html,bad'}));
});
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

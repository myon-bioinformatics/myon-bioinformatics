const {test}=require('node:test');
const assert=require('node:assert/strict');
const compaction=require('../site/ui/compaction-review.js');
test('convergence uses authoritative membership instead of missing detail records',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),M=require('../site/ui/unified-model.js');
 const source=fs.readFileSync(path.join(__dirname,'../site/ui/unified-app.js'),'utf8');
 const start=source.indexOf('function convergenceBoard()'),end=source.indexOf('\ndocument.addEventListener',start+1);
 const cases=[
  [{complete:true,open_pr_numbers:[]},'0',false],
  [{complete:false,open_pr_numbers:null},'未確認',false],
  [{complete:true,open_pr_numbers:[301]},'1',true],
  [null,'未確認',false]
 ];
 for(const [row,count,missing] of cases){
  const snap={records:[],inventory:[],captured_at:'2026-11-15T00:00:00Z'};
  if(row)snap.open_pr_inventory=[{repository:'o/r',...row}];
  const context={M,data:{aoi_integration:snap,plan:{cycle:[],blocked:[]}},convergenceFocus:'',E:M.esc,
   absorptionBoard:()=>'',badge:s=>s,observed:s=>s,table:(h,s)=>s,link:(url,label)=>label};
  vm.createContext(context);vm.runInContext(source.slice(start,end)+'\noutput=convergenceBoard();',context);
  assert.ok(context.output.includes('open PR '+count),context.output);
  assert.equal(context.output.includes('証跡未取得'),missing);
  if(count==='未確認'||missing){assert.ok(context.output.includes('依存評価は未確認'));assert.ok(!context.output.includes('循環: 対象なし'))}
 }
 const stale={repository:'o/r',number:1,github_state:'open'};
 assert.equal(M.openMembership({records:[stale],inventory:[],open_pr_inventory:[{repository:'o/r',complete:false,open_pr_numbers:null}]}).open.length,0);
 assert.equal(M.openMembership({records:[],inventory:[],open_pr_inventory:[null]}).count,null);
 assert.equal(M.openMembership({records:[stale],inventory:[],open_pr_inventory:[{repository:'o/r',complete:true,open_pr_numbers:[]}]}).open.length,0);
 assert.equal(M.openMembership({records:[],inventory:[{repo:'o/missing'}],open_pr_inventory:[{repository:'o/r',complete:true,open_pr_numbers:[]}]}).count,null);
});
test('compaction keeps #284 rate display on legacy counts while zero denominators stay unavailable',()=>{
 assert.equal(compaction.legacyRate('8/10'),'8/10 (80.00%)');
 assert.equal(compaction.legacyRate('0/0'),'0/0 (分母ゼロ)');
 assert.equal(compaction.legacyRate('9/8'),'9/8 (不正な件数)');
 assert.equal(compaction.legacyRate(''),'未測定');
});
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

// --- GitHub evidence model (site/ui/evidence-model.js): top-level checks ---
{
const assert=require('node:assert/strict');const {summarize,validate}=require('../site/ui/evidence-model.js');
const head='a'.repeat(40),old='b'.repeat(40),base={kind:'pr',head_sha:head,github_state:'open',events:[]};
assert.equal(summarize({...base,events:[{type:'manual_report',conclusion:'success',head_sha:head}]}).ci,'未確認');
assert.equal(summarize({...base,events:[{type:'ci',conclusion:'success',status:'completed',head_sha:old}]}).ci,'未確認');
assert.equal(summarize({...base,events:[{type:'review',state:'APPROVED',review_sha:old}]}).approved,false);
assert.equal(summarize({...base,expected_sha:old}).expected,false);
assert.equal(summarize({...base,events:[{type:'ci',name:'copilot reviewer',conclusion:'failure',head_sha:head}]}).bot.length,1);
assert.equal(summarize({...base,events:[{type:'supersession_claim',text:'superseded'}]}).open,true);
assert.ok(summarize({...base,merged:true,merge_commit_sha:old,events:[{type:'post_merge',head_sha:old,conclusion:'failure'}]}).gaps.includes('マージ後確認なし'));
assert.throws(()=>validate({schema:'github-evidence/1',records:[{id:'a',repository:'r',number:1,events:[]},{id:'a',repository:'r',number:1,events:[]}]}));
validate(JSON.parse(require('node:zlib').gunzipSync(require('node:fs').readFileSync(require('node:path').join(__dirname,'../site/evidence/observations.json.gz')))));assert.equal(summarize({...base,events:[{type:'ci',name:'CI',run_id:1,attempt:1,head_sha:head,status:'completed',conclusion:'failure'},{type:'ci',name:'CI',run_id:1,attempt:2,head_sha:head,status:'completed',conclusion:'success'}]}).failed.length,0);console.log('10 evidence checks passed');

assert.equal(summarize({...base,events:[{type:'ci',run_id:1,attempt:1,head_sha:head,conclusion:'failure',status:'completed',superseded:true},{type:'ci',run_id:2,attempt:1,head_sha:head,conclusion:'success',status:'completed'}]}).failed.length,0);
assert.equal(summarize({...base,events:[{type:'review',actor:'reviewer',at:'2026-11-11',state:'APPROVED',review_sha:head},{type:'review',actor:'reviewer',at:'2026-11-12',state:'DISMISSED',review_sha:head}]}).approved,false);
assert.equal(summarize({...base,mergeable:false}).priority,0);
console.log('3 supersession / dismissed review / conflict checks passed');

assert.equal(summarize({...base,events:[{type:'ci',head_sha:head,status:'completed',conclusion:'failure',historical:true}]}).failed.length,0);
}


test('legacy work states migrate without changing GitHub status',()=>{for(const [before,after] of Object.entries({'未着手':'調査中','対応中':'着手中','ブロック':'ブロッカー','統合済み':'完了'})){const r=m.normalize({records:[{...observation,state:before,github_state:'open'}]})[0];assert.equal(r.state,after);assert.equal(r.github_state,'open')}});

// --- workspace store and pages (site/ui/workspace.js) ---
{
const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('workspace states and completion share one store',()=>{
 const data=new Map(),context={window:{},localStorage:{getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../site/ui/workspace.js'),'utf8'),context);
 const W=context.window.Workspace;
 for(const s of W.states){W.update('ops:test',s,'next');assert.equal(W.read().records['ops:test'].state,s)}
 assert.throws(()=>W.update('ops:test','作業中','next'));
 assert.equal(W.read().records['ops:test'].completion.note,'next');
});
test('workspace pages keep local assets and source pins',()=>{
 const root=path.join(__dirname,'../site/ui');
 for(const file of ['workspace.html','operations.html','records.html'])for(const m of fs.readFileSync(path.join(root,file),'utf8').matchAll(/(?:src|href)="([^"#]+)"/g)){
  if(m[1]==='index.html#dependencies'||/^(https?:|mailto:)/.test(m[1]))continue;
  assert.ok(fs.existsSync(path.resolve(root,m[1].split('#')[0])),`${file}: ${m[1]}`);
 }
 const ops=JSON.parse(fs.readFileSync(path.join(root,'operations-data.json')));
 assert.equal(new Set(ops.map(x=>x.id)).size,ops.length);
 assert.ok(ops.every(x=>typeof x.execution==='string'&&x.execution.length>0&&/^[a-f0-9]{40}$/.test(x.source_sha)));
});
}

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

test('compaction work state is independent of measurements and old v2 records remain readable',()=>{
 const r={id:'pending',date:'2026-11-14T10:28',type:'保留事項',execution:'未実行',mode:'対象外',title:'case'};
 assert.equal(compaction.validate(r).workState,'調査中');
 assert.equal(compaction.validate({...r,workState:'ブロッカー'}).execution,'未実行');
 assert.equal(compaction.validate({...r,workState:'完了'}).mode,'対象外');
 assert.throws(()=>compaction.validate({...r,workState:'unknown'}));
 const loaded=compaction.loadStored({getItem:k=>k==='compaction-lab-v2'?JSON.stringify({version:2,records:[r]}):null});
 assert.equal(loaded.records[0].workState,'調査中');
});

test('convergence uses current head latest attempt without dropping historical evidence',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),M=require('../site/ui/unified-model.js');
 const source=fs.readFileSync(path.join(__dirname,'../site/ui/unified-app.js'),'utf8');
 const start=source.indexOf('function convergenceBoard()'),end=source.indexOf('\ndocument.addEventListener',start+1);
 const head='a'.repeat(40),old='b'.repeat(40);
 const run=(id,attempt,sha,conclusion)=>({id,run_attempt:attempt,head_sha:sha,workflow_id:1,event:'pull_request',name:'test',status:'completed',conclusion});
 const cases=[
  [[run(1,1,old,'failure'),run(2,1,head,'success')],false],
  [[run(2,1,head,'failure'),run(2,2,head,'success')],false],
  [[run(2,1,head,'failure'),run(3,1,head,'success')],false],
  [[run(2,1,head,'success'),run(2,2,head,'failure')],true],
  [[run(2,1,head,'cancelled')],true]
 ];
 for(const [runs,failed] of cases){
  const record={repository:'o/r',number:1,head_sha:head,state:'調査中',runs,files:[],dependencies:[],gaps:[]};
  const snap={records:[record],inventory:[],open_pr_inventory:[{repository:'o/r',complete:true,open_pr_numbers:[1]}]};
  const context={M,data:{aoi_integration:snap,plan:{cycle:[],blocked:[]}},convergenceFocus:'',E:M.esc,
   absorptionBoard:()=>'',badge:s=>s,observed:s=>s,table:(h,s)=>s,link:(url,label)=>label};
  vm.createContext(context);vm.runInContext(source.slice(start,end)+'\noutput=convergenceBoard();',context);
  assert.equal(context.output.includes('CI失敗'),failed);
  assert.equal(context.output.includes('修正後に同じ対象SHAで再実行'),failed);
  assert.ok(context.output.includes('関連する観測一覧 '+(runs.length+1)+'件'));
 }
});

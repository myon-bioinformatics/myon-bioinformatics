const {test}=require('node:test');
const assert=require('node:assert/strict');
const m=require('../site/ui/audit-model.js');
const url='https://github.com/myon-bioinformatics/Aoi/pull/167';
const observation={id:'Aoi#167',repository:'Aoi',number:167,kind:'pr',url,title:'<script>',head_sha:'a'.repeat(40),ci:'COMPLETE',review:0,review_complete:true,checks:[],state:''};
test('canonical parent snapshot retains complete zero reviews and exact head',()=>{
 const [r]=m.normalize({records:[observation],generated_at:'2026-11-08T00:00:00Z'});
 assert.equal(r.id,url);assert.equal(r.head,'a'.repeat(40));assert.equal(r.review,0);assert.equal(r.ci,'成功');assert.equal(r.state,'未着手');
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

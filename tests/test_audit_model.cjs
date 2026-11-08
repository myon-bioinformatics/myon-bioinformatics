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

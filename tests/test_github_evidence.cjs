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

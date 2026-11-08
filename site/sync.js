/* GitHub observations never own the user's audit fields. */
(function(root){
const fields=['title','body','head','base','branch','mergeable','runs','githubState','ci','checked','observedAt','review','threads','reviewNote','freshness','syncError'];
function merge(row, observation){const out={...row};for(const k of fields)if(k in observation)out[k]=observation[k];return out}
function classify(runs,statuses){const all=[...runs.map(x=>x.status==='completed'?x.conclusion:'pending'),...statuses.map(x=>x.state)];return !all.length?'不明':all.some(x=>['failure','error','timed_out','action_required','startup_failure'].includes(x))?'失敗':all.some(x=>['pending','queued','in_progress'].includes(x))?'実行中':all.every(x=>x==='success')?'成功':'要確認'}
async function pages(api,path,key){let out=[];for(let page=1;;page++){const d=await api(path+(path.includes('?')?'&':'?')+'per_page=100&page='+page);const batch=key?d[key]:d;if(!Array.isArray(batch))throw Error('API response shape');out.push(...batch);if(batch.length<100)return out}}
async function observe(api,row){const base='repos/myon-bioinformatics/'+encodeURIComponent(row.repo);const p=await api(base+(row.type==='PR'?'/pulls/':'/issues/')+row.number);let observation={title:p.title,body:p.body||'',githubState:p.merged_at?'merged':p.state};if(row.type==='PR'){
const sha=p.head.sha;const checks=await pages(api,base+'/commits/'+sha+'/check-runs?filter=latest','check_runs');const statuses=await pages(api,base+'/commits/'+sha+'/statuses');const latest=new Map();for(const s of statuses)if(!latest.has(s.context))latest.set(s.context,s);
// Verify the head again so a moving PR cannot be paired with another head's CI.
const verify=await api(base+'/pulls/'+row.number);if(verify.head.sha!==sha)throw Error('head changed during refresh; retry');
observation={...observation,head:sha,base:p.base.ref,branch:p.head.ref,mergeable:p.mergeable,runs:checks.map(x=>({id:x.id,name:x.name,status:x.status,conclusion:x.conclusion,html_url:x.html_url,head_sha:x.head_sha})),ci:classify(checks,[...latest.values()]),review:null,threads:null,reviewNote:'未解決レビューはGitHubで確認'};
}return {...observation,checked:new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Tokyo'}),observedAt:new Date().toISOString(),freshness:'verified',syncError:''}}
const api={merge,classify,pages,observe};if(typeof module!=='undefined')module.exports=api;else root.AuditSync=api;
})(globalThis);

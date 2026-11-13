(function(root){'use strict';
const shared=typeof module!=='undefined'?require('./shared-ui.js'):root.SharedUI;
const {states,esc}=shared;
function summarize(r){
 const all=r.events||[],runs=all.filter(e=>e.type==='ci'&&!e.historical&&e.head_sha&&e.head_sha===r.head_sha),latest=new Map();
 for(const e of runs){const key=JSON.stringify([e.workflow_id||e.name,e.event||'']),old=latest.get(key);if(!old||Number(e.run_number||e.run_id||0)>Number(old.run_number||old.run_id||0)||Number(e.run_number||e.run_id||0)===Number(old.run_number||old.run_id||0)&&Number(e.attempt||0)>Number(old.attempt||0))latest.set(key,e)}
 const effective=[...latest.values()],bad=new Set(['failure','timed_out','cancelled','action_required','startup_failure']),failed=effective.filter(e=>bad.has(e.conclusion));
 const reviews=new Map();for(const e of all.filter(e=>e.type==='review'&&['APPROVED','CHANGES_REQUESTED','DISMISSED'].includes(e.state)).sort((a,b)=>(b.at||'').localeCompare(a.at||'')))if(!reviews.has(e.actor))reviews.set(e.actor,e);
 const approved=[...reviews.values()].some(e=>e.state==='APPROVED'&&e.review_sha===r.head_sha),changesRequested=[...reviews.values()].some(e=>e.state==='CHANGES_REQUESTED');
 const gaps=[];if(!r.metadata_checked)gaps.push('status sync');if(!r.comment_coverage)gaps.push('コメント網羅');
 if(r.kind==='pr'){if(!effective.length)gaps.push('現head CI');if(!r.review_coverage||r.review_coverage.head_sha&&r.review_coverage.head_sha!==r.head_sha)gaps.push('レビュー網羅');if(!r.required_checks_verified)gaps.push('required checks');if(!r.expected_sha)gaps.push('期待SHA');if(!r.main_sha)gaps.push('main SHA');if(!r.current_collection||r.current_collection.head_sha&&r.current_collection.head_sha!==r.head_sha)gaps.push('run/attempt/job再取得');}
 const open=r.github_state==='open'&&!r.inventory_absent,failedSha=!!r.expected_sha&&r.expected_sha!==r.head_sha;
 const flags=[];if(open){if(failed.length)flags.push('CI失敗');if(r.mergeable===false)flags.push('競合');if(r.review>0||changesRequested)flags.push('未解決レビュー');if(gaps.length||failedSha)flags.push('証跡不足');if(r.kind==='pr'&&r.approval_required===true&&!approved)flags.push('承認待ち');if(r.kind==='pr')flags.push('未クローズPR');}
 if(r.migration?.state==='ブロッカー')flags.push('統合ブロッカー');
 const priority=flags.some(x=>['CI失敗','競合','未解決レビュー','統合ブロッカー'].includes(x))?0:flags.includes('証跡不足')?1:open?2:3;
 let ci=failed.length?'失敗':effective.some(e=>e.status!=='completed')?'実行中':effective.length&&effective.every(e=>e.conclusion==='success')?'成功（観測分）':effective.length?'skip等を含む':'未確認';
 return {open,approved,changesRequested,gaps,flags,priority,ci,effective,next:failed.length?'失敗jobとheadを照合':r.mergeable===false?'重複ファイルと依存順を確認':r.review>0||changesRequested?'レビュー対象SHAと未解決内容を確認':gaps.length?'不足証跡を収集':!approved&&r.kind==='pr'?'現headのレビューを確認':'状態とclosureを再確認'};
}
function validate(d){if(!d||d.schema!=='integrated-operations/1'||!Array.isArray(d.records)||!Array.isArray(d.counts)||!Array.isArray(d.operations))throw Error('integrated-operations/1 のJSONが必要です');const ids=new Set();for(const r of d.records){if(!r||typeof r.id!=='string'||ids.has(r.id)||typeof r.repository!=='string'||!Number.isInteger(r.number)||!Array.isArray(r.events))throw Error('記録ID・repo・番号・eventsを確認');ids.add(r.id)}return d}
function diff(a,b){const old=new Map(a.records.map(r=>[r.id,r])),now=new Map(b.records.map(r=>[r.id,r]));return [...new Set([...old.keys(),...now.keys()])].flatMap(id=>{const x=old.get(id),y=now.get(id);return JSON.stringify(x)!==JSON.stringify(y)?[{id,before:x?.state||'—',after:y?.state||'—'}]:[]})}
const api={states,esc,summarize,validate,diff};if(typeof module!=='undefined')module.exports=api;root.UnifiedModel=api;
})(typeof window==='undefined'?globalThis:window);

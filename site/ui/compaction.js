'use strict';
const key='compaction-lab-v2', $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const numeric=['correct','total','declined','attempted','before','after'];
const identity=['corpus','revision','tokenizer','template','context','runtime','code','seed','caseId','model','category'];
const modes=['有効','無効','辞退','未実測'];
const pct=(a,b)=>a!==''&&a!=null&&b!==''&&b!=null&&+b>0?`${esc(a)}/${esc(b)} (${(100*+a/+b).toFixed(1)}%)`:'—';
const link=url=>typeof url==='string'&&/^https?:\/\//i.test(url)?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">証跡 ↗</a>`:'';
function legacyRatio(value){const m=/^\s*(\d+)\s*\/\s*(\d+)\s*$/.exec(value||'');return m&&+m[2]>0&&+m[1]<=+m[2]?pct(m[1],m[2]):esc(value||'—');}
function normalize(r){return {...Object.fromEntries([...numeric,...identity].map(k=>[k,''])),...r,mode:r.mode==='未測定'?'未実測':r.mode,kind:r.kind||'モデル別結果',workState:r.workState||'調査中',execution:r.execution||'未記録（旧形式）'};}
function validate(r){
 if(!r||typeof r!=='object'||Array.isArray(r))return false;
 if(Object.values(r).some(v=>typeof v!=='string'))return false;
 if(!r.id||!r.name||!/^\d{4}-\d{2}-\d{2}$/.test(r.date)||!modes.includes(r.mode))return false;
 if(!['調査中','着手中','完了','保留','ブロッカー'].includes(r.workState))return false;
 if(numeric.some(k=>r[k]!==''&&(!/^\d+$/.test(r[k])||!Number.isSafeInteger(+r[k]))))return false;
 if((r.correct!==''&&r.total!==''&&+r.correct>+r.total)||(r.declined!==''&&r.attempted!==''&&+r.declined>+r.attempted))return false;
 if(r.mode==='未実測'&&numeric.some(k=>r[k]!==''))return false;
 if(r.mode==='無効'&&(r.declined!==''||r.attempted!==''))return false;
 if(numeric.some(k=>r[k]!=='')&&r.execution!=='完了'&&!r.execution.startsWith('未記録'))return false;
 return true;
}
function migrateLegacy(r){
 if(typeof r.title!=='string')return normalize(r);
 return normalize({id:r.id,date:r.date,name:r.title,model:r.model||'',category:r.category||'',kind:r.type||'測定ケース',mode:modes.includes(r.status)?r.status:'未実測',execution:'未記録（旧形式）',regression:r.detail||'',source:r.url||'',legacyEnabled:r.enabled||'',legacyDisabled:r.disabled||'',legacyDeclined:r.declined||'',reason:'旧形式から引継ぎ。比較条件・実行状態は要確認'});
}
let records=[];
try{const saved=localStorage.getItem(key);let v=JSON.parse(saved===null?(localStorage.getItem('compaction-lab-v1')||'[]'):saved);if(Array.isArray(v))records=v.filter(r=>r&&typeof r.date==='string'&&typeof (r.title||r.name)==='string'&&typeof r.id==='string').map(migrateLegacy);}catch{$('#message').textContent='保存データを読めません。元のデータを保持しているため、JSONを確認してください。';}
function selected(){return records.filter(r=>(!$('#filter').value||r.category===$('#filter').value)&&(!$('#from').value||r.date>=$('#from').value)&&(!$('#until').value||r.date<=$('#until').value)).sort((a,b)=>b.date.localeCompare(a.date));}
function pairReport(rows){
 const groups=new Map();let missing=0;
 for(const r of rows){if(!['有効','無効'].includes(r.mode)||r.execution!=='完了')continue;if(identity.some(k=>r[k]==='')){missing++;continue;}const sig=JSON.stringify(identity.map(k=>r[k]));const group=groups.get(sig)||[];group.push(r);groups.set(sig,group);}
 let out=[];
 for(const group of groups.values()){const on=group.find(r=>r.mode==='有効'),off=group.find(r=>r.mode==='無効');if(!on||!off)continue;let result='比較不能：正解数・同一の分母が必要';if(on.total!==''&&off.total!==''&&+on.total>0&&on.total===off.total&&on.correct!==''&&off.correct!==''){const d=+on.correct-+off.correct;result=`正解数差 ${d>0?'+':''}${d}。${d<0?'回帰候補：失敗したケースIDを確認':'集計上の悪化なし：個別回帰は未判定'}`;}out.push(`<p><b>${esc(on.caseId)} / ${esc(on.model)}</b> · 有効 ${esc(on.date)} vs 無効 ${esc(off.date)}<br>${esc(result)} · ${link(on.source)} ${link(off.source)}</p>`);}
 return `${out.join('')||'<p>比較可能な有効／無効ペアはありません。</p>'}<p class="muted">条件不足で比較除外：${missing}件。各条件の最新記録を比較。過去の記録は時系列に保持。</p>`;
}
function render(){
 const rows=selected();
 $('#rows').innerHTML=rows.length?rows.map(r=>`<tr data-status="${esc(r.mode)}"><td>${esc(r.date)}<br><b>${esc(r.name)}</b><br><span class="pill">${esc(r.category)}</span><br>${esc(r.kind)}</td><td>${esc(r.model||'未実測')}<br>${esc(r.revision||'revision未記録')}<details><summary>測定条件</summary><p>${identity.map(k=>`${esc(k)}: ${esc(r[k]||'未記録')}`).join('<br>')}</p></details></td><td><span class="badge ${({'有効':'on','無効':'off','辞退':'decline','未実測':'unmeasured'})[r.mode]}">${esc(r.mode)}</span><br>${esc(r.execution)}<br>作業: ${esc(r.workState)}<br>${esc(r.setting||'設定未記録')}<br>${esc(r.reason||'理由未記録')}</td><td>${pct(r.correct,r.total)}</td><td>${pct(r.declined,r.attempted)}</td><td>${esc(r.before===''?'—':r.before)} → ${esc(r.after===''?'—':r.after)}</td><td>${esc(r.regression||'—')}<br>${r.legacyEnabled||r.legacyDisabled||r.legacyDeclined?`旧記録（比較未検証）: 有効 ${legacyRatio(r.legacyEnabled)} / 無効 ${legacyRatio(r.legacyDisabled)} / 辞退 ${legacyRatio(r.legacyDeclined)}<br>`:''}${link(r.source)}</td></tr>`).join(''):'';
 $('#emptyRows').hidden=rows.length>0;
 $('#pairs').innerHTML=pairReport(rows);
 $('#events').innerHTML=`<article><span class="pill">2026-11-14 · 確認結果</span><h3>固定応答と対象経路を分離</h3><p>core・CPU-liteの回帰と限定GGUF実験を確認。対象UI→proxy→モデル、実multi-turn/MCP、WASM/WebGPUは未実測。安全性仕様書は計測範囲を修正済み。#281のPages公開成功も確認。</p></article><article><span class="pill">既報 · 測定日は未紐付け</span><h3>限定GGUFの結果と回帰</h3><p>#122の集計ではexact-matchは改善、回帰10件と合成攻撃の悪化も報告。独立評価と元artifactへの紐付けが次の手順。</p></article>`+rows.map(r=>`<article><span class="pill">${esc(r.date)} · ${esc(r.kind)} · ${esc(r.mode)}</span><h3>${esc(r.name)}</h3><p>${esc(r.regression||'回帰・保留事項は未記録')}<br><b>次:</b> ${esc(r.next||'未記録')}<br>${link(r.source)}</p><button type="button" data-delete="${esc(r.id)}">この記録を削除</button></article>`).join('');
 document.querySelector('[data-ui-filter]').dispatchEvent(new Event('input'));
}
function save(){try{localStorage.setItem(key,JSON.stringify(records));render();$('#message').textContent='ブラウザに保存しました。';}catch{render();$('#message').textContent='ブラウザ保存に失敗。JSONを書き出してください。';}}
$('#form').onsubmit=e=>{e.preventDefault();let r=normalize({...Object.fromEntries(new FormData(e.target)),id:crypto.randomUUID()});if(!validate(r)){$('#message').textContent='件数・分母・状態を確認。未実測は数値なし、数値ありは実行完了、無効は辞退集計なし。';return;}records.push(r);save();e.target.reset();setDate();};
['#filter','#from','#until'].forEach(s=>$(s).onchange=render);
$('#events').onclick=e=>{const id=e.target.dataset.delete;if(id&&confirm('この記録を削除しますか？')){records=records.filter(r=>r.id!==id);save();}};
$('#export').onclick=()=>{let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({version:2,records,criteria:document.querySelector('[data-ui-draft="criteria"]').value},null,2)],{type:'application/json'}));a.download='compaction-records.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
$('#import').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>2_000_000)throw Error();let v=JSON.parse(await f.text());if(![1,2].includes(v.version)||!Array.isArray(v.records)||v.records.length>5000)throw Error();const next=v.records.map(migrateLegacy);if(!next.every(validate)||new Set(next.map(r=>r.id)).size!==next.length||v.criteria!=null&&typeof v.criteria!=='string')throw Error();if(!confirm('現在の記録をJSONで置き換えますか？'))return;records=next;if(typeof v.criteria==='string'){let c=document.querySelector('[data-ui-draft="criteria"]');c.value=v.criteria;c.dispatchEvent(new Event('input'));}save();}catch{$('#message').textContent='JSONの形式・件数・状態を確認してください。現在の記録は保持しました。';}e.target.value='';};
function setDate(){document.querySelector('[name=date]').value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}setDate();render();

'use strict';
const key='compaction-lab-v2', esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const types=['測定ケース','モデル別結果','回帰','保留事項','次アクション'], executions=['未実行','完了','エラー','判定不能'], modes=['対象外','有効','無効','辞退','適用不要'];
const workStates=['調査中','着手中','完了','保留','ブロッカー'];
const fields=['date','workState','type','execution','mode','title','caseSet','category','dataset','model','revision','tokenizer','template','context','runtime','config','correct','judged','declines','attempts','tokens','regressions','detail','url'];
const numbers=['context','correct','judged','declines','attempts','tokens'];
const comparison=['caseSet','category','dataset','model','revision','tokenizer','template','context','runtime','config'];
const seeds=[
{id:'source-116',date:'2026-10-12T18:41',type:'測定ケース',execution:'完了',mode:'対象外',title:'#116 保守的coreを統合',detail:'API状態はmerged。本文のunmergedは当時の記録。過去userを削除しない、tool provenanceはout-of-band。決定的CPU-liteの証跡を生成モデル保証に使わない。',url:'https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/116',source:true},
{id:'source-118',date:'2026-10-13T09:57',type:'測定ケース',execution:'完了',mode:'対象外',title:'#118 mockの完全messages JSON文字数計測を統合',detail:'tool引数を含む文字数を計測。token数・実モデル精度ではない。',url:'https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/118',source:true},
{id:'source-119',date:'2026-10-13T10:27',type:'回帰',execution:'完了',mode:'対象外',title:'#119 限定GGUF基盤を統合 · 品質回帰あり',model:'SmolLM2-135M-Instruct Q4_K_M',category:'Aoi公開判定QA',regressions:'P2,P13,P19,P23,P56,P61,P92,P93,P98,P113',detail:'既存レビュー記載：全114件は58→83正解、35改善・10回帰。class 1は10/16→5/16、攻撃は2/2→1/2。今回の元JSON再集計なし。実MCP tool-choiceは未測定。',url:'https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/119#pullrequestreview-5495831630',source:true},
{id:'source-196',date:'2026-11-09T14:27',type:'保留事項',execution:'完了',mode:'対象外',title:'#196 手動GGUF workflowを削除',detail:'mainのtreeでcompaction-gguf-benchmark.ymlが存在しないことを確認。過去のrun成功と現行CIを区別し、古い実行手順を更新する候補。',url:'https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/196',source:true},
{id:'source-281',date:'2026-11-14T07:13',type:'次アクション',execution:'完了',mode:'対象外',title:'#281 既存Pages公開成功を確認',detail:'07:04 JSTにマージ。Pages runは07:13 JSTにsuccess。run head 2bd850f559c3efe68ddc3a70c0943ee4fbb627ff。このrunの対象は#281。後続更新の公開状態とは分離。',url:'https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/43516192279',source:true},
{id:'source-213',date:'2026-11-14T10:23',type:'保留事項',execution:'完了',mode:'対象外',title:'#213 安全性仕様の説明差分を解消',detail:'mock文字数と限定GGUF証跡、未実装のproxy・ユーザー制約を残す要約、未実測の実multi-turn/MCP比較を区別。GGUF実行手順の古いworkflow案内は残る。',url:'https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/213',source:true},
{id:'source-next',date:'2026-11-14T10:28',type:'次アクション',execution:'未実行',mode:'対象外',title:'説明差分・元成果物・実経路を順に確認',detail:'安全性仕様は#213対応済み → GGUF手順の差分確認 → #119の元JSON/JUnitを再集計 → 条件固定 → 実multi-turn/MCP比較 → 閾値・holdout確定。推論再実行なし。',url:'https://github.com/myon-bioinformatics/mcp-toolcall-lab/issues/115',source:true}
];
let records=[], editing=null, storageMessage='';
function validate(r){
 if(!r||typeof r!=='object'||Array.isArray(r))throw Error('記録がオブジェクトではありません');
 if(typeof r.id!=='string'||!r.id||r.id.length>200)throw Error('記録IDが不正です');
 const out={id:r.id};
 for(const f of fields){if(r[f]!==undefined&&typeof r[f]!=='string')throw Error(f+' は文字列で指定してください');out[f]=r[f]||'';if(out[f].length>(f==='detail'?8000:1000))throw Error('入力が長すぎます');}
 out.workState=out.workState||'調査中';if(!workStates.includes(out.workState))throw Error('作業状態が不正です');
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(out.date)||Number.isNaN(Date.parse(out.date+'+09:00')))throw Error('日時が不正です');
 if(!out.title.trim()||!types.includes(out.type)||!executions.includes(out.execution)||!modes.includes(out.mode))throw Error('タイトル・種別・状態が不正です');
 if(out.url&&!/^https?:\/\//i.test(out.url))throw Error('証跡はhttp/https URLで指定してください');
 if(out.url){try{new URL(out.url)}catch{throw Error('証跡URLが不正です')}}
 for(const f of numbers)if(out[f]!==''&&(!/^\d+$/.test(out[f])||!Number.isSafeInteger(Number(out[f]))||(f==='context'&&Number(out[f])<1)))throw Error('数値は0以上の整数で指定してください');
 if(out.correct!==''&&(out.judged===''||+out.correct>+out.judged))throw Error('正解数は判定可能数以下にしてください');
 if(out.declines!==''&&(out.attempts===''||+out.declines>+out.attempts))throw Error('辞退数は圧縮試行数以下にしてください');
 if(out.execution!=='完了'&&['correct','judged','declines','attempts','tokens'].some(f=>out[f]!==''))throw Error('測定値は実行完了の記録に入力してください');
 return out;
}
function legacyRate(value){
 const text=String(value||'未測定'), m=text.match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);
 if(!m)return text;
 const a=Number(m[1]),b=Number(m[2]);
 return text+(a>b?' (不正な件数)':b===0?' (分母ゼロ)':` (${(100*a/b).toFixed(2)}%)`);
}
function legacy(r){
 if(!r||typeof r.date!=='string'||typeof r.title!=='string')throw Error('v1記録が不正です');
 const status=r.status, mode=modes.includes(status)?status:'対象外';
 const execution=['有効','無効','辞退','完了'].includes(status)?'完了':status==='失敗'?'エラー':status==='判定不能'?'判定不能':'未実行';
 return validate({id:r.id||('legacy-'+Math.random().toString(36).slice(2)),date:r.date.includes('T')?r.date.slice(0,16):r.date+'T00:00',type:types.includes(r.type)?r.type:'保留事項',execution,mode,title:r.title,model:r.model||'',category:r.category||'',url:r.url||'',detail:(r.detail||'')+'\n[v1移行・比較条件の再確認待ち] 有効:'+legacyRate(r.enabled)+' / 無効:'+legacyRate(r.disabled)+' / 辞退:'+legacyRate(r.declined)});
}
function loadStored(storage){
 const raw=storage.getItem(key);
 if(raw!==null){const saved=JSON.parse(raw);if(saved.version!==2||!Array.isArray(saved.records))throw Error('invalid saved records');return {records:saved.records.map(validate),migrated:false};}
 const old=JSON.parse(storage.getItem('compaction-lab-v1')||'[]');if(!Array.isArray(old))throw Error('invalid legacy records');
 return {records:old.map(legacy),migrated:old.length>0};
}
if(typeof module!=='undefined')module.exports={validate,legacy,loadStored,legacyRate};
if(typeof document!=='undefined'){
const $=s=>document.querySelector(s), form=$('#form');
let migrated=false;
try{({records,migrated}=loadStored(localStorage));}catch{storageMessage='保存記録を読めません。空の作業記録で開始しました。JSONから復元できます。'}
function persist(){try{localStorage.setItem(key,JSON.stringify({version:2,records}));return true}catch{$('#message').textContent='ブラウザ保存に失敗。記録は画面内に保持しています。JSONを書き出してください。';return false}}
function href(url,label='証跡 ↗'){return url&&/^https?:\/\//i.test(url)?`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`:''}
function pct(a,b){return a!==''&&b!==''&&a!==undefined&&b!==undefined?`${esc(a)}/${esc(b)} (${+b>0?(100*+a/+b).toFixed(2)+'%':'分母ゼロ'})`:'未測定'}
function tab(id){if(!document.getElementById(id)?.classList.contains('view'))id='overview';document.querySelectorAll('.view').forEach(v=>v.classList.toggle('hide',v.id!==id));document.querySelectorAll('[data-view]').forEach(b=>{const selected=b.dataset.view===id;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1});history.replaceState(null,'','#'+id)}
function render(){
 const filter=$('#filter').value, query=$('#search').value.toLowerCase(), order=$('#order').value;
 const arr=[...seeds,...records].filter(r=>(filter==='すべて'||r.type===filter)&&[r.title,r.detail,r.model,r.category,r.caseSet,r.regressions].join(' ').toLowerCase().includes(query)).sort((a,b)=>(order==='desc'?-1:1)*a.date.localeCompare(b.date));
 $('#entries').innerHTML=arr.length?arr.map(r=>`<article class="entry"><small class="muted">${esc(r.date.replace('T',' '))} JST · ${esc(r.type)} · ${esc(r.execution)} · ${esc(r.mode)} · 作業:${esc(r.workState||'調査中')}</small><h3>${esc(r.title)}</h3><p class="muted">${esc(r.model||'')} ${esc(r.category||'')}</p><p style="white-space:pre-wrap">${esc(r.detail||'')}</p>${r.regressions?`<p>回帰ID: ${esc(r.regressions)}</p>`:''}${href(r.url)} ${r.source?'<span class="tag">読取証跡</span>':`<button data-edit="${esc(r.id)}">編集</button> <button data-delete="${esc(r.id)}">削除</button>`}</article>`).join(''):'<p>一致する記録はありません。</p>';
 const rows=records.filter(r=>r.type==='モデル別結果'), groups=new Map(), unpaired=[];
 for(const r of rows){if(!comparison.every(f=>r[f]!==''&&r[f]!==undefined)||r.execution!=='完了'||!['有効','無効','辞退'].includes(r.mode)){unpaired.push(r);continue}const k=JSON.stringify(comparison.map(f=>r[f]));if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r)}
 let html='';for(const group of groups.values()){
 const r=group[0], byMode={};group.sort((a,b)=>a.date.localeCompare(b.date)).forEach(x=>byMode[x.mode]=x);
 const on=byMode['有効'],off=byMode['無効'],declined=byMode['辞退'];
 let delta='ペア不足';if(on&&off&&+on.judged>0&&+off.judged>0){delta=((100*on.correct/on.judged)-(100*off.correct/off.judged)).toFixed(2)+'ポイント（集計精度差）';if(on.correct===''||off.correct==='')delta='測定値不足'}
 const rates=group.filter(x=>x.declines!==''&&x.attempts!=='').map(x=>`${esc(x.mode)}: ${pct(x.declines,x.attempts)}`).join('<br>')||'未測定';
 html+=`<section><h3>${esc(r.model)} / ${esc(r.category)}</h3><p class="sub">${comparison.map(f=>`${f}: ${esc(r[f])}`).join(' · ')}</p><div class="scroll" data-ui-scroll="table"><table><thead><tr><th>無効</th><th>有効</th><th>辞退 / 試行</th><th>品質差・回帰</th></tr></thead><tbody><tr><td>${off?pct(off.correct,off.judged):'未測定'}</td><td>${on?pct(on.correct,on.judged):'未測定'}</td><td>${rates}</td><td>${esc(delta)}<br>回帰ID: ${esc(on?.regressions||'未登録')}</td></tr></tbody></table></div><p class="sub">同じ条件・モードの複数記録は最新を表示。全履歴は時系列に保持。</p><p>${[off,on,declined].filter(Boolean).map(x=>href(x.url,x.mode+'の証跡 ↗')).join('　')}</p></section>`;
 }
 if(unpaired.length)html+='<h3>条件不足・未完了（比較対象外）</h3>'+unpaired.map(r=>`<p><b>${esc(r.title)}</b> · ${esc(r.execution)} / ${esc(r.mode)} · ${esc(r.model||'モデル未登録')}<br><span class="sub">不足: ${esc(comparison.filter(f=>!r[f]).join(', ')||'完了状態・比較モードを確認')}</span></p>`).join('');
 $('#resultRows').innerHTML=html||'<p class="muted">追加の実測記録はありません。時系列から測定条件と結果を追加してください。</p>';
}
const nowJST=()=>new Date(Date.now()+9*3600000).toISOString().slice(0,16);
function resetForm(){form.reset();form.elements.date.value=nowJST();editing=null;form.querySelector('button').textContent='記録する'}
form.onsubmit=e=>{e.preventDefault();try{const obj=Object.fromEntries(new FormData(form));obj.id=editing||('record-'+Date.now()+'-'+Math.random().toString(36).slice(2));const r=validate(obj);records=records.filter(x=>x.id!==r.id).concat([r]);const ok=persist();render();resetForm();if(ok)$('#message').textContent='記録しました。'}catch(e){$('#message').textContent=e.message}};
$('#entries').onclick=e=>{const id=e.target.dataset.delete, edit=e.target.dataset.edit;if(id&&confirm('この作業記録を削除しますか？')){records=records.filter(r=>r.id!==id);persist();render()}if(edit){const r=records.find(r=>r.id===edit);if(!r)return;fields.forEach(f=>form.elements[f].value=r[f]||'');editing=edit;form.querySelector('button').textContent='更新する';form.scrollIntoView({behavior:'smooth',block:'start'})}};
$('#export').onclick=()=>{const blob=new Blob([JSON.stringify({version:2,timezone:'Asia/Tokyo',exported_at:new Date().toISOString(),snapshot:{parent:'914412450b8d51b99033ec3d4739601906dd95e4',lab:'586a6a0ffb4de72163d344e3dc7ac5433b696aec'},records},null,2)],{type:'application/json'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='compaction-records-v2.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);$('#message').textContent='作業記録を書き出しました。読取証跡はHTMLに内蔵されています。'};
$('#import').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>2e6)throw Error('JSONは2MB以下にしてください');const d=JSON.parse(await f.text());if(![1,2].includes(d.version)||!Array.isArray(d.records)||d.records.length>2000)throw Error('対応するv1/v2 JSONではありません');const imported=d.records.map(d.version===1?legacy:validate);if(new Set(imported.map(r=>r.id)).size!==imported.length)throw Error('JSON内の記録IDが重複しています');if(imported.some(r=>seeds.some(s=>s.id===r.id)))throw Error('読取証跡と重複するIDです');const merged=new Map(records.map(r=>[r.id,r]));if(imported.some(r=>merged.has(r.id))&&!confirm('同じIDの作業記録を読み込み内容で更新しますか？'))return;for(const r of imported)merged.set(r.id,r);records=[...merged.values()];const ok=persist();render();if(ok)$('#message').textContent=`${imported.length}件を読み込みました。`+(d.version===1?' v1の値は条件再確認待ちです。':'');}catch(err){$('#message').textContent='読み込み失敗: '+err.message}finally{e.target.value=''}};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>tab(b.dataset.view));document.querySelectorAll('[data-go]').forEach(b=>b.onclick=e=>{e.preventDefault();tab(b.dataset.go)});
$('nav').onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const buttons=[...document.querySelectorAll('[data-view]')],i=buttons.indexOf(document.activeElement);const n=e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[n].focus();tab(buttons[n].dataset.view)};
window.addEventListener('hashchange',()=>tab(location.hash.slice(1)));
['#filter','#order','#search'].forEach(s=>$(s).addEventListener(s==='#search'?'input':'change',render));
resetForm();render();tab(location.hash.slice(1));$('#message').textContent=storageMessage;if(migrated&&persist())$('#message').textContent='旧画面の記録を移行しました。元の保存値は保持しています。';
}

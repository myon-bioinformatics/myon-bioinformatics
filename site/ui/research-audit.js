(function(root){'use strict';
let researchQuery='',researchStatus='',researchPending=true,researchCase='E25';
root.ResearchAudit={mount(data,helpers){
if(!data.research){helpers.target.innerHTML='<p>研究監査データ未取得。保存済みJSONの時刻を確認してください。</p>';return}
const DATA=data,{esc,badge,link,date,download}=helpers,STATES=root.SharedUI.states;
const $=selector=>document.querySelector(selector);
const table=(headers,rows)=>helpers.table(headers,rows.join(''));
const cell=(v,label)=>`<div><small>${esc(label)}</small><div class="mono">${esc(v)}</div></div>`;
function research(){
 const a=DATA.research,rank={'ブロッカー':0,'調査中':1,'着手中':2,'保留':3,'完了':4};
 const cases=a.cases.filter(c=>(!researchPending||c.state!=='完了')&&(!researchStatus||c.state===researchStatus)&&(!researchQuery||JSON.stringify(c).toLowerCase().includes(researchQuery.toLowerCase()))).sort((x,y)=>rank[x.state]-rank[y.state]||x.id.localeCompare(y.id));
 
 $('#view').innerHTML=`<div class="panel"><div class="eyebrow">AOI / RESEARCH AUDIT</div><h3>研究登録・未解釈入力</h3><p>ケースごとに判定・根拠・導入・検証を分けて追跡。共通のPR・Issue・CI証跡は1件に集約。</p><div class="tools">${badge('登録監査 6件・未完了','warn')}${badge('Q5 現main再検証待ち','warn')}${badge('外部変更 0件')}</div><p>研究監査の取得: ${date(a.observed_at)}。親子監査は ${date(DATA.built_at)} のビルド観測です。研究ケースの再実行は含みません。</p></div>
 <div class="panel"><h3>⚠ #42：当時と現在を区別</h3><p><b>2026-10-16の検証記録：</b>実装・同一head検証済み。Draft・未マージ・main未導入。</p><p><b>保存済み監査時点のGitHub確認：</b>#42はclosed / Draft / 未マージ。後継#46は10月17日にマージ済み。#42のheadは現在のmainの祖先で、原文/span保持コードも存在します。「main未導入」を現在の状態としては扱いません。</p><p>${link(a.sources.pr42.url,'Aoi #42')} · ${link(a.sources.pr46.url,'後継 #46')} · ${DATA.records.some(r=>r.id==='Aoi#i30')?'<button data-detail="Aoi#i30">共通台帳 Issue #30</button>':link(a.sources.issue30.url,'共通台帳 Issue #30')}</p><div class="grid">${cell(a.head_sha,'#42 検証対象head')}${cell(a.main_sha,'研究監査で確認したmain')}</div><p>旧head CI：CI / Links success。現main：pytest 3.11〜3.15 success（個別check取得）。ケースごとの新規実行・CLI／ログ／受領記録の採取は未実施。</p></div>
 <div class="panel"><h3>🔎 未完了・証跡不足から確認</h3><div class="tools"><label>ケース検索 <input id="researchQuery" value="${esc(researchQuery)}" placeholder="E25・Q5・cache・理由コード"></label><label>状態 <select id="researchStatus"><option value="">すべて</option>${STATES.map(s=>`<option ${researchStatus===s?'selected':''}>${s}</option>`).join('')}</select></label><label><input id="researchPending" type="checkbox" ${researchPending?'checked':''}>未完了のみ</label><button id="researchExport">研究監査JSON</button></div>
 ${table(['ケース / 状態','機械値・契約','証跡不足','次の確認手順'],cases.map(c=>`<tr><td><button data-case="${esc(c.id)}">${esc(c.id)} · ${esc(c.title)}</button><small>${badge(c.state,c.state==='完了'?'good':'warn')}</small></td><td>${esc(c.registration_status||c.parse_status||'未確認')}<small>${esc(c.reason)}</small></td><td>${esc(c.gaps.join(' / '))}</td><td>${esc(c.next)}</td></tr>`))}${!cases.length?'<p class="empty">該当ケースなし。条件を変更してください。</p>':''}</div>
 <div id="researchCaseDetail"></div>
 <div class="panel"><h3>共通証跡 / 同一head・main</h3><p>以下は各ケースから参照する共通記録です。CI成功だけで登録監査や研究の独立確認を完了にしません。</p>${table(['証跡','対象SHA / 範囲','結果・限界'],Object.values(a.sources).map(s=>`<tr><td>${link(s.url,s.title)}</td><td class="mono">${esc(s.sha||'番号で関連付け / SHAなし')}</td><td>${esc(s.result)}</td></tr>`))}<details><summary>現mainの個別checks（${a.checks.length}件）</summary>${table(['check','結果','対象SHA'],a.checks.map(c=>`<tr><td>${link(c.url,c.name)}</td><td>${esc(c.conclusion||c.status)}</td><td class="mono">${esc(c.head_sha)}</td></tr>`))}</details><p>保存済み研究監査のスナップショットです。ビルド時の一般CI成功をケース専用再検証へ読み替えません。PSWBの既存配色・表・横スクロール・JSON書出しを再利用。共通UIの追加APIは不要です。</p></div>`;
 const currentMain=DATA.records.find(r=>r.repository==='Aoi')?.main_sha;
 if(currentMain&&currentMain!==a.main_sha)$('#view').insertAdjacentHTML('afterbegin','<div class="notice">⚠ 研究監査後にAoi mainが変わっています。研究監査main '+esc(a.main_sha)+' / ビルドmain '+esc(currentMain)+'。ケース専用再検証が必要です。</div>');
 const draw=()=>researchDetail(researchCase);
 $('#researchQuery').oninput=e=>{const pos=e.target.selectionStart;researchQuery=e.target.value;research();$('#researchQuery').focus();$('#researchQuery').setSelectionRange(pos,pos)};
 $('#researchStatus').onchange=e=>{researchStatus=e.target.value;research();bind()};$('#researchPending').onchange=e=>{researchPending=e.target.checked;research();bind()};
 $('#researchExport').onclick=()=>download('aoi-research-audit.json',a);
 document.querySelectorAll('[data-case]').forEach(b=>b.onclick=()=>{researchCase=b.dataset.case;draw();$('#researchCaseDetail').scrollIntoView({behavior:'smooth',block:'start'})});draw();
}
function researchDetail(id){
 const a=DATA.research,c=a.cases.find(x=>x.id===id);if(!c)return;
 const related=c.source_ids.map(k=>a.sources[k]);
 const fields=c.registration_status?{'registration_status':c.registration_status,'registration_reason':c.reason,'registration_evidence':c.registration_evidence,'independently_confirmed':c.independently_confirmed,'E/P契約':c.contract,'JSONL・表示・cache整合':c.consistency,'成立率・判例不変':c.invariance}:c.input_contract;
 $('#researchCaseDetail').innerHTML=`<div class="panel" tabindex="-1"><div class="eyebrow">CASE / ${esc(c.id)}</div><h3>${esc(c.title)} ${badge(c.state)}</h3><p>${esc(c.reason)}</p>${table(['監査項目','値・確認範囲'],Object.entries(fields).map(([k,v])=>`<tr><td>${esc(k)}</td><td><pre>${esc(typeof v==='object'?JSON.stringify(v,null,2):v)}</pre></td></tr>`))}<h4>次の確認手順</h4><ol>${c.steps.map(s=>`<li>${esc(s)}</li>`).join('')}</ol><p>参照: ${related.map(s=>link(s.url,s.title)).join(' / ')}</p><h4>時系列比較</h4><label>比較元 <select id="researchFrom">${c.history.map((e,i)=>`<option value="${i}">${esc(e.at)} / ${esc(e.state)}</option>`).join('')}</select></label> <label>比較先 <select id="researchTo">${c.history.map((e,i)=>`<option value="${i}" ${i===c.history.length-1?'selected':''}>${esc(e.at)} / ${esc(e.state)}</option>`).join('')}</select></label><div id="researchDiff"></div><div class="timeline">${c.history.slice().reverse().map(e=>`<article class="event">${badge(e.state)} <b>${esc(e.at)}</b><p>${esc(e.summary)}</p><small>${esc(e.scope)}</small><div class="mono">head ${esc(e.head_sha||'未取得')}<br>main ${esc(e.main_sha||'当時未取得')}</div></article>`).join('')}</div></div>`;
 const diff=()=>{const x=c.history[+$('#researchFrom').value],y=c.history[+$('#researchTo').value];$('#researchDiff').innerHTML=table(['項目','比較元','比較先'],['state','summary','scope','head_sha','main_sha'].map(k=>`<tr><td>${esc(k)}</td><td>${esc(x[k])}</td><td class="${x[k]!==y[k]?'ui-changed':''}">${esc(y[k])}</td></tr>`))};$('#researchFrom').onchange=diff;$('#researchTo').onchange=diff;diff();
}

research();
}};
})(window);

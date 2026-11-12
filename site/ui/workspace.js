'use strict';
window.Workspace=(()=>{
 const key='myon-workspace-v2',states=['調査中','着手中','完了','保留','ブロッカー'];
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function read(){try{return JSON.parse(localStorage.getItem(key))||{records:{},snapshots:[]}}catch{return {records:{},snapshots:[]}}}
 function write(d){localStorage.setItem(key,JSON.stringify(d))}
 function update(id,state,next){if(!states.includes(state))throw Error('状態が不正です');const d=read();const before=d.records[id]||{};d.records[id]={...before,state,next,updated_at:new Date().toISOString(),completion:state==='完了'?{at:new Date().toISOString(),note:next}:before.completion};write(d)}
 function download(name,d){const a=document.createElement('a'),u=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
 function shell(page){document.querySelector('header').innerHTML='<strong>リポジトリ運用</strong><nav>'+[['workspace.html','確認順序'],['evidence.html','検証証跡'],['operations.html','実行手順・責務'],['records.html','完了・週次記録']].map(([u,t])=>`<a href="${u}" ${u===page?'aria-current="page"':''}>${t}</a>`).join('')+'<a href="index.html#dependencies">既存監査・依存</a></nav>';}
 return {key,states,esc,read,write,update,download,shell};
})();

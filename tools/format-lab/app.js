"use strict";
const $=id=>document.getElementById(id), states=["調査中","着手中","完了","保留","ブロッカー"];
const enc=new TextEncoder(), dec=new TextDecoder("utf-8",{fatal:true,ignoreBOM:true});
function encode(s){let raw="";for(const n of enc.encode(s))raw+=String.fromCharCode(n);return btoa(raw)}
function decode(s){if(!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(s))throw Error("Base64形式を確認してください");return dec.decode(Uint8Array.from(atob(s),c=>c.charCodeAt(0)))}
const esc=s=>s.replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function render(s){
 const refs=[];let out=[],para=[],list=false,fence=false,block=[];
 function inline(line){return line.split(/(!?\[[^\]]*\]\([^ ()]+\)|`[^`]+`|\*\*[^*]+\*\*)/g).map(t=>{
 const link=t.match(/^(!?)\[([^\]]*)\]\(([^ ()]+)\)$/);if(link){const [,img,label,url]=link;refs.push({url,img:!!img});if(/^[\u0000-\u0020]|[\\\u0000-\u001f]|^(?!https?:|mailto:)[a-z][a-z\d+.\-]*:/i.test(url))return esc(label);return img?'<img alt="'+esc(label)+'" src="'+esc(url)+'">':'<a href="'+esc(url)+'">'+esc(label)+'</a>'}
 if(t.startsWith("`")&&t.endsWith("`"))return "<code>"+esc(t.slice(1,-1))+"</code>";if(t.startsWith("**")&&t.endsWith("**"))return "<strong>"+esc(t.slice(2,-2))+"</strong>";return esc(t)}).join("")}
 function flush(){if(para.length)out.push("<p>"+inline(para.join("\n"))+"</p>");para=[];if(list)out.push("</ul>");list=false}
 for(const l of s.split(/\r\n|\r|\n/)){if(l.startsWith("```")){if(fence){out.push("<pre><code>"+esc(block.join("\n"))+"</code></pre>");block=[]}else flush();fence=!fence;continue}if(fence){block.push(l);continue}const h=l.match(/^(#{1,6}) (.*)$/);if(h){flush();const n=h[1].length;out.push("<h"+n+">"+inline(h[2])+"</h"+n+">")}else if(/^[-*] /.test(l)){if(!list){flush();out.push("<ul>");list=true}out.push("<li>"+inline(l.slice(2))+"</li>")}else if(!l.trim())flush();else{if(list)flush();para.push(l)}}flush();if(fence)out.push("<pre><code>"+esc(block.join("\n"))+"</code></pre>");return {body:out.join("\n"),refs}}
function make(name,source){return {name,source,base64:encode(source),state:states[0],evidence:{},checks:{}}}
const demo="# Markdown 検証ノート\n\nMarkdown の原本から、変換と復元の流れを確認します。\n\n## 確認項目\n\n- Base64 から原本を復元\n- HTML の生成結果を確認\n- 相対リンクの参照先を検査\n\n## 参照ファイル\n\n[利用ガイド](docs/guide.md)\n";
let docs=[make("verification.md",demo)],idx=0,tab="editor",html="",selectedRefs=[];
const fields=[["command","検証コマンド"],["exit","終了コード"],["stdout","標準出力"],["stderr","標準エラー出力"],["pptx","PPTX計測結果"],["artifact","生成Artifact（ファイル名・バージョン・証跡）"],["pages","Pages URL"],["browser","実ブラウザ・確認日時・結果"],["pageState","Pages公開後の確認状態"],["unknown","未検証事項"],["next","次に確認する項目"],["done","完了条件"]];
function clean(d){if(!d||typeof d.name!=="string"||!d.name.trim()||typeof d.source!=="string"||typeof d.base64!=="string"||!states.includes(d.state))throw Error("ドキュメント形式が不正です");let r=make(d.name,d.source);Object.assign(r,{state:d.state,base64:d.base64});for(const [k] of fields)if(typeof d.evidence?.[k]==="string")r.evidence[k]=d.evidence[k];if(Object.values(formats).includes(d.format))r.format=d.format;if(typeof d.at==="string")r.at=d.at;r.checks=Object.create(null);for(const [u,v] of Object.entries(d.checks||{}))if(states.includes(v?.state)&&typeof v.note==="string")r.checks[u]={state:v.state,note:v.note};return r}
function read(s){const b=JSON.parse(s);if(b.version!==1||!Array.isArray(b.documents)||!b.documents.length)throw Error("version 1 のワークスペースJSONを指定してください");return b.documents.map(clean)}
const pack=()=>JSON.stringify({version:1,documents:docs},null,2);
try{const v=localStorage.getItem("markdown-lab-v1");if(v)docs=read(v)}catch(e){$("notice").textContent="保存データを読み込めませんでした"}
const doc=()=>docs[idx];function save(){try{localStorage.setItem("markdown-lab-v1",pack());$("saved").textContent="●　ローカル保存済み"}catch(e){$("saved").textContent="保存不可・JSONで退避"}}
function select(el,v){el.replaceChildren(...states.map(s=>new Option(s,s)));el.value=v}
function nav(){const n=$("docs");n.replaceChildren();docs.forEach((d,i)=>{const b=document.createElement("button");b.textContent="▤　"+d.name;b.className=i===idx?"active":"";b.onclick=()=>{idx=i;show()};n.append(b)})}
function diff(a,b){if(a===b)return "差分なし";let x=a.split(/(?<=\n)/),y=b.split(/(?<=\n)/),i=0;while(i<x.length&&i<y.length&&x[i]===y[i])i++;while(x.length>i&&y.length>i&&x.at(-1)===y.at(-1)){x.pop();y.pop()}return "変更開始行 "+(i+1)+"\n"+[...x.slice(i).map(l=>"- "+JSON.stringify(l)),...y.slice(i).map(l=>"+ "+JSON.stringify(l))].join("\n")}
function convert(){const d=doc();let s="",valid=true;try{s=decode(d.base64)}catch(e){valid=false;$("decoded").textContent=e.message}
 if(valid)$("decoded").textContent=s;$("diff").textContent=valid?diff(d.source,s):"復元できないため比較保留";$("match").textContent=valid?(s===d.source?"✓　バイト一致":"△　差分あり"):"復元エラー";
 const format=d.format||inferFormat(d.name);$("fl-format").value=format;$("pptx-bench").hidden=format!=="Markdown";$("fl-report").textContent="";const r=format==="Markdown"?render(s):{body:"<pre>"+esc(s)+"</pre>",refs:[]};html=valid?'<!doctype html><html lang="ja"><meta charset="utf-8"><title>'+esc(d.name)+'</title>\n'+r.body+'\n<script type="application/octet-stream" id="markdown-source">'+d.base64+'</script></html>':"";
 $("code").textContent=html;const t=document.createElement("template");t.innerHTML=r.body;for(const img of t.content.querySelectorAll("img"))img.replaceWith(document.createTextNode("▧ "+img.alt));for(const a of t.content.querySelectorAll("a"))a.removeAttribute("href");$("preview").replaceChildren(t.content);$("artifact").disabled=!valid;$("fl-stats").textContent="UTF-8　 ·　"+d.source.split(/\r\n|\r|\n/).length+" 行　 ·　"+enc.encode(d.source).length+" B";
 $("stages").innerHTML=[["≡",format,enc.encode(d.source).length+" B"],["64","Base64",d.base64.length+" chars"],["&lt;/&gt;","HTML",enc.encode(html).length+" B"]].map(([ic,n,k])=>'<div class="stage"><i>'+ic+'</i><div><b>'+n+'</b><small>'+k+'</small></div></div>').join('<span class="arrow">→</span>');selectedRefs=r.refs;$("count").textContent=new Set(r.refs.map(r=>r.url)).size}
function download(name,s,type){const a=document.createElement("a"),url=URL.createObjectURL(new Blob([s],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000)}
function show(){const d=doc();nav();$("title").textContent=d.name;select($("state"),d.state);$("src").value=d.source;$("b64").value=d.base64;convert();switchTab(tab)}
function switchTab(k){tab=k;for(const b of document.querySelectorAll("[data-tab]")){b.className=b.dataset.tab===k?"active":"";$(b.dataset.tab).hidden=b.dataset.tab!==k}if(k==="links")links();if(k==="evidence")records()}
function links(){const area=$("refs");area.replaceChildren();const seen=new Set();for(const {url,img} of selectedRefs){if(seen.has(url))continue;seen.add(url);const d=doc(),row=document.createElement("div"),c=document.createElement("code"),sel=document.createElement("select"),note=document.createElement("input");row.className="ref";c.textContent=(img?"画像":/^(?:[a-z]+:|\/\/)/i.test(url)?"外部リンク":url.startsWith("#")?"ページ内参照":"ファイル参照")+"　"+url;const v=Object.hasOwn(d.checks,url)?d.checks[url]:{};select(sel,v.state||states[0]);sel.setAttribute("aria-label",url+" 確認状態");note.placeholder="解決先・検査結果・証跡";note.setAttribute("aria-label",url+" 検査結果");note.value=v.note||"";sel.onchange=note.oninput=()=>{Object.defineProperty(d.checks,url,{value:{state:sel.value,note:note.value},enumerable:true,configurable:true});save()};row.append(c,sel,note);area.append(row)}if(!seen.size)area.textContent="参照はありません。"}
const sha256Pattern=/^[a-f0-9]{64}$/;
const stageLabels={
 passed:"成功", failed:"失敗", not_run:"未実施", page_count_mismatch:"ページ数不一致"
};
async function sha256(bytes){
 const digest=await crypto.subtle.digest("SHA-256",bytes);
 return Array.from(new Uint8Array(digest),n=>n.toString(16).padStart(2,"0")).join("");
}
function pptxStages(report){
 const hasDeck=Number.isSafeInteger(report.bytes)&&report.bytes>0&&sha256Pattern.test(report.pptx_sha256);
 const inspected=Array.isArray(report.slides)&&report.slides.length>0;
 const pages=report.preview_pages;
 let generation,inspection,rendering;
 if(report.schema==="format-lab-pptx/1"){
  generation=hasDeck?"passed":report.exit_code?"failed":"not_run";
  inspection=hasDeck&&inspected?"passed":"not_run";
  rendering=inspection!=="passed"?"not_run":report.preview_error?"failed":
   Array.isArray(pages)&&pages.length?"passed":"not_run";
 }else if(report.schema==="format-lab-pptx/2"){
  generation=report.generation?.status;
  inspection=report.inspection?.status;
  rendering=report.rendering?.status;
  if(![generation,inspection,rendering].every(status=>Object.hasOwn(stageLabels,status)))throw Error("検証段階の状態が不正です");
 }else throw Error("計測形式が一致しません");

 if((generation==="passed"&&!hasDeck)||(inspection==="passed"&&(generation!=="passed"||!inspected))){
  throw Error("生成物の計測値が不足しています");
 }
 if(rendering==="passed"){
  if(report.exit_code||inspection!=="passed"||(report.schema==="format-lab-pptx/2"&&report.rendering.requested!==true)){
   throw Error("描画の計測結果が矛盾しています");
  }
  if(!Array.isArray(pages)||pages.length!==report.slides.length)rendering="page_count_mismatch";
  else if(!pages.every((entry,index)=>{
   const page=entry?.file?.match?.(/^slide-(\d+)\.png$/);
   return page&&Number(page[1])===index+1&&sha256Pattern.test(entry.sha256);
  }))throw Error("PNGのページ番号またはSHA-256が不正です");
 }
 return {generation,inspection,rendering};
}
function pptxSummary(report,stages){
 const details=stages.inspection==="passed"
  ? " ／ "+report.slides.length+" 枚 ／ "+report.bytes+" B"
  : "";
 return "PPTX生成 "+stageLabels[stages.generation]+" ／ 構造検査 "+stageLabels[stages.inspection]+
  " ／ 最終描画 "+stageLabels[stages.rendering]+details+
  " ／ "+report.elapsed_seconds+" 秒 ／ 終了コード "+report.exit_code;
}
let previewUrls=[], deckSelection=0;
function clearDeck(){
 deckSelection++;
 for(const url of previewUrls)URL.revokeObjectURL(url);
 previewUrls=[];
 $("deck-pages").replaceChildren();
 $("deck-images").value="";
 $("deck-status").textContent="";
}
function deckReport(){
 try{
  const evidence=(doc().evidence.pptx||"").trim();
  return JSON.parse(evidence.startsWith("{")?evidence:evidence.slice(evidence.indexOf("\n")+1));
 }catch{return null}
}
function showDeckReport(){
 clearDeck();
 const report=deckReport();
 $("deck-summary").textContent="";
 $("deck-preview").hidden=true;
 if(!report)return;
 try{
  const stages=pptxStages(report);
  $("deck-summary").textContent=pptxSummary(report,stages);
  if(stages.rendering==="passed"){
   $("deck-preview").hidden=false;
   $("deck-status").textContent="PNG未照合";
  }
 }catch(error){
  $("deck-summary").textContent=error.message;
 }
}
$("deck-images").onchange=async event=>{
 const files=new Map(Array.from(event.target.files,f=>[f.name,f]));
 clearDeck();
 const ticket=deckSelection, report=deckReport(), source=doc().source, urls=[];
 if(!files.size){
  $("deck-status").textContent="PNG未照合";
  return;
 }
 $("deck-status").textContent="PNGを照合中";
 try{
  if(!report||pptxStages(report).rendering!=="passed")throw Error("照合できる描画結果がありません");
  if(await sha256(enc.encode(source))!==report.source_sha256)throw Error("原稿が計測時と異なります");
  const figures=[];
  for(const [index,entry] of report.preview_pages.entries()){
   const file=files.get(entry.file);
   if(!file)throw Error("PNGページ不足　"+entry.file);
   if(await sha256(await file.arrayBuffer())!==entry.sha256)throw Error("PNGのSHA-256不一致　"+entry.file);
   const figure=document.createElement("figure"), image=document.createElement("img"), caption=document.createElement("figcaption");
   image.src=URL.createObjectURL(file);
   urls.push(image.src);
   image.alt="スライド "+(index+1);
   image.style.maxWidth="100%";
   await image.decode().catch(()=>{throw Error("PNGを表示できません　"+entry.file)});
   caption.textContent=(index+1)+" / "+report.preview_pages.length;
   figure.append(caption,image);
   figures.push(figure);
  }
  if(ticket!==deckSelection||source!==doc().source)return;
  previewUrls=urls.splice(0);
  $("deck-pages").replaceChildren(...figures);
  $("deck-status").textContent="全"+figures.length+"ページのPNGを照合済み";
 }catch(error){
  if(ticket===deckSelection)$("deck-status").textContent="PNG照合失敗　"+error.message;
 }finally{
  for(const url of urls)URL.revokeObjectURL(url);
 }
};
function records(){showDeckReport();const area=$("fields");area.replaceChildren();const d=doc();for(const [key,title] of fields){const l=document.createElement("label");l.textContent=title;const el=document.createElement(key==="pageState"?"select":["exit","pages","command"].includes(key)?"input":"textarea");if(key==="pageState")select(el,d.evidence[key]||states[0]);else el.value=d.evidence[key]||"";el.oninput=()=>{d.evidence[key]=el.value;save();if(key==="pptx")showDeckReport()};l.append(el);area.append(l)}}
function changed(){const d=doc();if(Object.values(d.evidence).some(Boolean)||Object.keys(d.checks).length||d.state==="完了"){d.state="調査中";d.evidence.pageState="調査中";for(const v of Object.values(d.checks))v.state="調査中";$("notice").textContent="変換内容を変更しました。以前の証跡を再確認してください。";select($("state"),d.state)}convert();save()}
$("src").oninput=()=>{doc().source=$("src").value;doc().base64=encode(doc().source);$("b64").value=doc().base64;changed()};$("b64").oninput=()=>{doc().base64=$("b64").value;changed()};$("reset").onclick=()=>{doc().base64=encode(doc().source);$("b64").value=doc().base64;changed()};$("state").onchange=()=>{doc().state=$("state").value;save()};for(const b of document.querySelectorAll("[data-tab]"))b.onclick=()=>switchTab(b.dataset.tab);
$("mode").onchange=()=>{$("code").hidden=$("mode").selectedIndex===0;$("preview").hidden=!$("code").hidden};$("artifact").onclick=()=>download(doc().name.replace(/\.md$/i,"")+".html",html,"text/html");$("exp").onclick=()=>download("markdown-workspace.json",pack(),"application/json");$("imp").onclick=()=>$("json").click();$("json").onchange=async e=>{try{const incoming=read(await e.target.files[0].text());if(!confirm("現在のドキュメントを読み込んだJSONで置き換えますか？"))return;docs=incoming;idx=0;save();show()}catch(err){$("notice").textContent=err.message}finally{e.target.value=""}};
$("add").onclick=()=>{$("name").value="untitled.md";$("fl-file").value="";$("newdoc").showModal()};$("fl-file").onchange=e=>{if(e.target.files[0])$("name").value=e.target.files[0].name};$("newdoc").onclose=async()=>{if($("newdoc").returnValue!=="ok")return;try{const f=$("fl-file").files[0];docs.push(make($("name").value.trim()||"untitled.md",f?dec.decode(await f.arrayBuffer()):""));idx=docs.length-1;tab="editor";save();show();$("src").focus()}catch(err){$("notice").textContent=err.message}};
$("fromhtml").onchange=async e=>{try{const t=document.createElement("template");t.innerHTML=await e.target.files[0].text();const node=t.content.querySelector('script#markdown-source[type="application/octet-stream"]');if(!node)throw Error("原本を同梱したHTMLを選んでください");decode(node.textContent);doc().base64=node.textContent;$("b64").value=doc().base64;changed()}catch(err){$("notice").textContent=err.message}finally{e.target.value=""}};
for(const fmt of new Set(Object.values(formats)))$("fl-format").add(new Option(fmt,fmt));
$("fl-format").onchange=()=>{doc().format=$("fl-format").value;changed()};
$("pptx-source").onclick=()=>download("slides.md",doc().source,"text/markdown;charset=utf-8");
$("pptx-result").onchange=async event=>{
 const input=event.target, target=doc(), source=target.source;
 try{
  const report=JSON.parse(await input.files[0].text());
  if(report.source_sha256!==await sha256(enc.encode(source)))throw Error("原稿が計測時と異なります");
  if(!Number.isInteger(report.exit_code)||!Number.isFinite(report.elapsed_seconds))throw Error("計測値が不正です");
  pptxStages(report);
  if(target!==doc()||target.source!==source)throw Error("原稿が変更されました");
  target.evidence.pptx=JSON.stringify(report,null,2);
  save();switchTab("evidence");$("notice").textContent="原稿のSHA-256を照合して計測結果を保存しました";
 }catch(error){$("notice").textContent=error.message}
 finally{input.value=""}
};
let diagnosticRun=0;
$("inspect").onclick=async()=>{
 const d=doc(),source=d.source,format=$("fl-format").value,ticket=++diagnosticRun;
 let result;
 try{result=await inspectSource(source,format)}catch(error){result="構文エラー　"+error.message}
 if(ticket===diagnosticRun&&d===doc()&&source===d.source&&format===$("fl-format").value)$("fl-report").textContent=result;
};
show();

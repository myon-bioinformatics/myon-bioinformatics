"use strict";
const formats={md:"Markdown",markdown:"Markdown",json:"JSON",yaml:"YAML",yml:"YAML",jsonl:"JSONL",ndjson:"JSONL",xml:"XML / JUnit",py:"Python",toml:"TOML",mmd:"Mermaid",mermaid:"Mermaid",uml:"PlantUML",puml:"PlantUML",ttl:"Turtle",txt:"Text"};
function inferFormat(name){return formats[name.toLowerCase().split(".").pop()]||"Text"}
let yamlParser;
function loadYamlParser(){
 if(!yamlParser){
  yamlParser=new Promise((resolve,reject)=>{
   const script=document.createElement("script");
   script.src="vendor/js-yaml/js-yaml.min.js";
   script.onload=()=>resolve(window.jsyaml);
   script.onerror=()=>{yamlParser=undefined;script.remove();reject(new Error("YAMLパーサーを読み込めませんでした"))};
   document.head.append(script);
  });
 }
 return yamlParser;
}
async function inspectYaml(source){
 let parser;
 try{parser=await loadYamlParser()}catch(error){return "構文未検証　"+error.message}
 try{
  let documents=0;
  parser.loadAll(source,()=>{documents++},{schema:parser.CORE_SCHEMA});
  return "YAML 構文確認済み　"+documents+" 文書";
 }catch(error){
  const mark=error.mark;
  const position=mark?(mark.line+1)+" 行 "+(mark.column+1)+" 列　":"";
  return "YAML 構文エラー　"+position+(error.reason||error.message)+(mark?.snippet?"\n"+mark.snippet:"");
 }
}
async function inspectSource(source,format){
 if(format==="YAML")return inspectYaml(source);
 const lines=source.split(/\r\n|\r|\n/);
 if(format==="JSON"){JSON.parse(source);return "JSON 構文確認済み"}
 if(format==="JSONL"){
  const messages=[];let records=0,checked=0,errors=0;
  for(const [i,line] of lines.entries()){
   if(!line.trim()&&i===lines.length-1)continue;
   try{const obj=JSON.parse(line);records++;
    if(obj&&typeof obj==="object"&&("b64" in obj||"sha256" in obj)){
     if(typeof obj.b64!=="string"||typeof obj.sha256!=="string"||!/^[a-f\d]{64}$/i.test(obj.sha256))throw Error("b64 または sha256 の形式が不正");
     const raw=atob(obj.b64);
     if(btoa(raw)!==obj.b64)throw Error("Base64 が正規形式ではありません");
     const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
     const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),n=>n.toString(16).padStart(2,"0")).join("");
     if(hash!==obj.sha256.toLowerCase())throw Error("SHA-256 不一致　"+(obj.id||""));checked++;
    }
   }catch(e){errors++;if(messages.length<25)messages.push((i+1)+" 行　"+e.message)}
  }
  return "JSON解析 "+records+" 件 ／ SHA-256 一致 "+checked+" 件 ／ エラー "+errors+" 件\n"+messages.join("\n")
 }
 if(format==="XML / JUnit"){
  if(/<!DOCTYPE/i.test(source))return "構文未検証　DOCTYPE を含むXMLは対象外";
  const xml=new DOMParser().parseFromString(source,"application/xml");
  const err=xml.querySelector("parsererror");if(err)return "XML構文エラー\n"+err.textContent.trim();
  if(!["testsuite","testsuites"].includes(xml.documentElement.localName))return "XML 整形式を確認";
  const cases=Array.from(xml.getElementsByTagName("testcase"));const counts={failure:0,error:0,skipped:0},notes=[];
  for(const c of cases){for(const child of c.children){if(Object.hasOwn(counts,child.localName)){counts[child.localName]++;if(child.localName!=="skipped"&&notes.length<25)notes.push(c.getAttribute("name")+"　"+(child.getAttribute("message")||child.textContent).slice(0,400));break}}}
  return "XML 整形式を確認 ／ JUnit 実要素を集計\nテスト "+cases.length+" 件　失敗 "+counts.failure+"　エラー "+counts.error+"　スキップ "+counts.skipped+"\n"+notes.join("\n")
 }
 if(format==="Python"||format==="TOML")return "ブラウザでは構文未検証。ローカルで実行できる静的検査（Python 3.11+）\npython inspect_format.py <ファイル名>";
 if(format==="Markdown")return "Markdown 簡易検査　"+(lines.filter(l=>/^\s*```/.test(l)).length%2?"バッククォートフェンスの終端を確認してください":"バッククォートフェンスは偶数個");
 return format+" 原文表示 ／ 構文未検証";
}

const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('workspace states and completion share one store',()=>{
 const data=new Map(),context={window:{},localStorage:{getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../site/ui/workspace.js'),'utf8'),context);
 const W=context.window.Workspace;
 for(const s of W.states){W.update('ops:test',s,'next');assert.equal(W.read().records['ops:test'].state,s)}
 assert.throws(()=>W.update('ops:test','作業中','next'));
 assert.equal(W.read().records['ops:test'].completion.note,'next');
});
test('workspace pages keep local assets and source pins',()=>{
 const root=path.join(__dirname,'../site/ui');
 for(const file of ['workspace.html','operations.html','records.html'])for(const m of fs.readFileSync(path.join(root,file),'utf8').matchAll(/(?:src|href)="([^"#]+)"/g)){
  if(m[1]==='index.html#dependencies'||/^(https?:|mailto:)/.test(m[1]))continue;
  assert.ok(fs.existsSync(path.resolve(root,m[1].split('#')[0])),`${file}: ${m[1]}`);
 }
 const ops=JSON.parse(fs.readFileSync(path.join(root,'operations-data.json')));
 assert.equal(new Set(ops.map(x=>x.id)).size,ops.length);
 assert.ok(ops.every(x=>typeof x.execution==='string'&&x.execution.length>0&&/^[a-f0-9]{40}$/.test(x.source_sha)));
});

// Pure data/renderer contract tests, independent of a browser or network.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const root=path.resolve(__dirname,'..');
const result=cp.spawnSync(path.join(root,'.venv/bin/python'),['-c',"import sys,json;sys.path.insert(0,'visualizer');from catalog import chapters,lesson;print(json.dumps([lesson(c['id']) for c in chapters()]))"],{cwd:root,encoding:'utf8'});
assert.equal(result.status,0,result.stderr);const lessons=JSON.parse(result.stdout);
const dummy={classList:{add(){},remove(){},toggle(){}},setAttribute(){}};
const context={document:{querySelector:()=>({...dummy}),querySelectorAll:()=>[],addEventListener(){}},console,setTimeout,clearTimeout,setInterval(){}};
vm.createContext(context);
let source=fs.readFileSync(path.join(__dirname,'static/app.js'),'utf8').replace(/init\(\);\s*$/,'');
vm.runInContext(source+'\nglobalThis.api={state,basicDemo,scenarioDemo,colored,explain,lastRequest};',context);
const api=context.api;
test('s01 tool-use/result IDs pair and request history grows',()=>{
  api.state.lesson=lessons[0];api.state.chapter='s01';api.state.scenario='tools';const events=api.basicDemo();
  const req=events.filter(e=>e.kind==='llm_request');assert.equal(JSON.stringify(req.map(e=>e.payload.messages.length)),'[1,3,5]');
  for(const e of req.slice(1)){const messages=e.payload.messages;const assistant=messages.at(-2);const result=messages.at(-1).content[0];assert.equal(result.tool_use_id,assistant.content[1].id);}
  assert.equal(events.at(-1).to,'user');assert.equal(req[0].payload.tools[0].name,'bash');
});
test('s01 direct answer contains no tool execution',()=>{
  api.state.scenario='chat';const events=api.basicDemo();assert.equal(events.filter(e=>e.kind==='llm_request').length,1);assert.equal(events.filter(e=>e.kind==='function_call').length,0);
});
test('all advanced chapters retain their scenarios and tool JSON',()=>{
  for(const lesson of lessons.slice(1)){api.state.lesson=lesson;api.state.chapter=lesson.id;const events=api.scenarioDemo();assert.equal(events.length,lesson.scenario.steps.length);for(const e of events){assert.ok(e.title);assert.ok(e.source.line>=1);if(e.kind==='tool_call')assert.equal(e.payload.type,'tool_use');}}
});
test('payload rendering cannot create HTML from model output',()=>{
  const html=api.colored({text:'</pre><img src=x onerror=alert(1)>'});assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('</pre>'));
});

test('interleaved LLM replies display their matching request context',()=>{
  api.state.mode='live';api.state.events=[
    {kind:'llm_request',call_id:'a',thread:'worker-a',payload:{messages:['a']}},
    {kind:'llm_request',call_id:'b',thread:'worker-b',payload:{messages:['b']}},
    {kind:'llm_response',call_id:'a',thread:'worker-a'}];
  api.state.index=2;assert.equal(api.lastRequest().call_id,'a');
});

'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const actorNames = {user:'用户',agent:'Agent 程序',llm:'LLM',tool:'工具',runtime:'运行时'};
const state = {chapter:'s01',mode:'demo',lesson:null,events:[],index:0,tab:'payload',timer:null,traceName:null,scenario:'tools',generation:0};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = value => JSON.stringify(value, null, 2) ?? 'null';
const clone = value => JSON.parse(JSON.stringify(value));
function colored(value) {
  return esc(pretty(value)).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null|\d+)\b/g,
    (all, quoted, colon) => `<span class="${quoted ? (colon ? 'json-key' : 'json-string') : 'json-num'}">${all}</span>`);
}
function toast(message){ $('#toast').textContent=message; $('#toast').classList.add('visible'); setTimeout(()=>$('#toast').classList.remove('visible'),2500); }
async function getJSON(url){const r=await fetch(url);if(!r.ok)throw new Error(`读取失败 (${r.status})`);return r.json();}
function sourceFor(fragment, preferred){
  const lines=state.lesson.source.split('\n');
  let index=lines.findIndex(line=>line.includes(fragment));
  if(preferred){const f=state.lesson.functions.find(f=>f.name===preferred);if(f)index=f.line-1;}
  return {file:state.lesson.path,line:Math.max(index+1,1)};
}
function basicDemo(){
  const events=[], messages=[];
  const add=(kind,from,to,title,payload,explanation,fragment)=>events.push({id:events.length+1,kind,from,to,title,payload:clone(payload),explanation,source:sourceFor(fragment)});
  const prompt=state.scenario==='chat'?'请介绍你自己。':"创建 hello.py，输出 Hello, World!，然后读取文件验证。";
  const system='You are a coding agent at /example/project. Environment: Unix-like: the bash tool runs the system shell. Use bash to solve tasks. Act, don\'t explain.';
  add('user_input','user','agent','用户提交一个目标',prompt,'用户把自然语言交给程序。此时还没有向 LLM 发送请求。','query = input');
  messages.push({role:'user',content:prompt});
  add('context','agent','agent','写入 messages[]',messages,'Agent 保存对话历史。role=user 表示输入来源；content 可以是文本，也可以是结构化内容块。','history.append');
  const request=()=>add('llm_request','agent','llm','发送完整 LLM 请求',{model:'example-model',system,messages,tools:[state.lesson.schemas.bash],max_tokens:8000},'每次调用都传入当前历史、system 和工具 Schema。此处模型名和目录为教学占位值，不读取 .env。','response = client.messages.create');
  if(state.scenario==='tools'){
    for(const [i,command,output] of [[1,`echo 'print("Hello, World!")' > hello.py`,'(no output)'],[2,'cat hello.py','print("Hello, World!")']]){
      request();
      const block={type:'tool_use',id:`toolu_demo_${i}`,name:'bash',input:{command}};
      const content=[{type:'text',text:i===1?'先创建这个文件。':'读取文件，确认内容。'},block];
      add('llm_response','llm','agent','模型返回工具调用',{role:'assistant',content,stop_reason:'tool_use'},'这是模型生成的结构化指令，还没有执行。name 指定工具；input 必须符合该工具的 input_schema。','tool_calls =');
      messages.push({role:'assistant',content});
      add('context','agent','agent','保存 assistant 消息',messages,'先保存模型原始内容，包括 tool_use.id；后续工具结果用这个 ID 与调用配对。','messages.append({"role": "assistant"');
      add('function_call','agent','tool','执行 run_bash(command)',block.input,'Agent 检查工具调用并运行本地 Python 函数。LLM 本身不会操作终端。','output = run_bash');
      add('function_return','tool','agent','工具返回执行输出',output,'shell 的输出回到 Agent。空输出在这个函数中转换为 (no output)。模型还没看到它。','return out[:50000]');
      const result={type:'tool_result',tool_use_id:block.id,content:output};
      messages.push({role:'user',content:[result]});
      add('context','agent','agent','把 tool_result 加入历史',{appended:messages.at(-1),messages},'工具结果使用 role=user 包装，这是 Anthropic Messages 协议的格式，不代表用户又手动输入了一次。下一轮请求才将它发送给模型。','messages.append({"role": "user", "content": results})');
    }
  }
  request();
  const text=state.scenario==='chat'?'我是一个通过工具帮助你完成任务的编程助手。':'已创建并验证 hello.py，文件会输出 Hello, World!。';
  const content=[{type:'text',text}];
  add('llm_response','llm','agent','模型返回最终文本',{role:'assistant',content,stop_reason:'end_turn'},'此次响应没有 tool_use 内容块。s01 根据内容块判断是否继续，而不只是读取 stop_reason。','if not tool_calls:');
  messages.push({role:'assistant',content});
  add('final','agent','user','退出循环，展示回答',text,'Agent 将文本打印给用户。历史保留在当前进程，下一次输入会接在这段对话之后。','print(block.text)');
  return events;
}
function scenarioDemo(){
  let serial=0; const pending=[];
  return state.lesson.scenario.steps.map((step,index)=>{
    const e={id:index+1,kind:step.type,from:'runtime',to:'agent',title:'运行时事件',payload:step.content,explanation:'仓库教学场景中的关键事件节选。',annotation:step.annotation,source:sourceFor('', 'agent_loop')};
    if(step.type==='user_message'){Object.assign(e,{from:'user',to:'agent',title:'用户提交任务',payload:{role:'user',content:step.content},source:sourceFor('query = input')});}
    else if(step.type==='assistant_text'){Object.assign(e,{from:'llm',to:'agent',title:'模型返回文本（节选）',payload:{role:'assistant',content:[{type:'text',text:step.content}]},explanation:'仅展示场景里的文本片段；同一次响应可能还包含工具调用。不能仅凭这段文本推断循环结束。',source:sourceFor('messages.create')});}
    else if(step.type==='tool_call'){
      let input;try{input=JSON.parse(step.content);}catch{input=step.toolName==='bash'?{command:step.content}:{example:step.content};}
      const id=`toolu_example_${++serial}`;pending.push({id,name:step.toolName});
      Object.assign(e,{from:'llm',to:'agent',title:`提出调用 ${step.toolName}`,toolName:step.toolName,payload:{type:'tool_use',id,name:step.toolName,input},explanation:'模型提出调用，Agent 负责分发。此 ID 为教学配对生成；高级章节场景省略了部分完整请求和中间响应。',source:sourceFor('tool_calls =')});
    }else if(step.type==='tool_result'){
      const p=pending.findLastIndex(p=>p.name===step.toolName);const matched=p>=0?pending.splice(p,1)[0]:null;
      Object.assign(e,{from:'tool',to:'agent',title:`接收 ${step.toolName} 结果`,toolName:step.toolName,payload:{type:'tool_result',tool_use_id:matched?.id??'(节选中未提供调用)',content:step.content},explanation:'工具输出返回宿主，由 Agent 包装为 tool_result。随后通过 role=user 消息送入下一次模型请求；本行展示的是教学表示。',source:sourceFor('', 'execute_tool')});
    }else{
      e.source=sourceFor('',state.chapter==='s17'?'evaluate_after_turn':state.chapter==='s16'?'run_workflow':'trigger_hooks');
      e.explanation=state.lesson.insight+' 本行是运行时事件描述，不是原始 API 数据包。';
    }
    return e;
  });
}
function explain(e){
  if(e.explanation)return e.explanation;
  const map={llm_request:'这是 SDK create() 收到的实际参数快照。messages 是这一时刻的完整输入；system、tools 和 max_tokens 与消息一起提交。密钥不包含在参数展示中。',llm_response:'这是 SDK 返回的实际响应。检查 content 中的 text / tool_use，usage 中的 token 用量，以及 stop_reason。call_id 对应同一次请求。',function_call:'这是章节源码中同步函数的实际入参。嵌套分发函数和工具函数可能分别产生事件；它们不代表重复执行。',function_return:'这是同步函数返回时观测到的值。异常展开也可能表现为 null；请结合随后出现的错误事件与工具结果判断成功与否。',user_input:'用户在终端输入的原始内容，也可能是一次权限确认。实际送给模型的内容，以后续 LLM 请求为准。',input_wait:'原程序正在等待终端输入。请在运行录制命令的终端继续操作。',error:'真实运行捕获到了异常。检查错误信息以及前面的模型参数；401 通常对应密钥，400 可能对应模型名或参数格式。',session_start:'录制器运行原章节入口。原程序仍使用当前工作目录、.env 和工具权限逻辑。',session_end:'主程序已经退出。本行不表示目标一定完成；请结合最后的模型响应、工具输出和运行状态判断。'};
  return map[e.kind]??'真实运行中捕获的可观察事件。';
}
async function selectChapter(chapter){
  pause();const generation=++state.generation;state.chapter=chapter;state.index=0;state.traceName=null;state.events=[];state.lesson=null;
  $$('#chapters button').forEach(b=>b.classList.toggle('selected',b.dataset.chapter===chapter));
  $('#timeline').innerHTML='<div class="empty">正在读取章节…</div>';
  try{
    const lesson=await getJSON(`/api/lesson?chapter=${chapter}`);if(generation!==state.generation)return;
    state.lesson=lesson;
    $('#breadcrumb').textContent=chapter;$('#eyebrow').textContent=`${chapter.toUpperCase()} / ${lesson.scenario.title.toUpperCase()}`;
    $('#title').textContent=lesson.title+'：'+lesson.subtitle;$('#subtitle').textContent='沿着一条消息，理解决策与执行的边界。';$('#insight').textContent=lesson.insight;
    $('#sourcePath').textContent=lesson.path;$('#recordCommand').textContent=`python visualizer/record.py ${chapter}`;
    if(state.mode==='demo')state.events=chapter==='s01'?basicDemo():scenarioDemo();
    else await pollTrace();
    if(generation===state.generation)render();
  }catch(err){toast(err.message);$('#timeline').innerHTML='<div class="empty">章节加载失败，请检查本地服务。</div>';}
}
function setMode(mode){state.mode=mode;$$('[data-mode]').forEach(b=>b.classList.toggle('selected',b.dataset.mode===mode));$('.follow-label').hidden=mode!=='live';selectChapter(state.chapter);}
function jump(index){state.index=Math.max(0,Math.min(index,state.events.length-1));render();$('#timeline .selected')?.scrollIntoView({block:'nearest',behavior:'smooth'});}
function pause(){if(state.timer)clearTimeout(state.timer);state.timer=null;$('#play').textContent='▶ 播放';}
function play(){
  if(state.timer){pause();return;}if(!state.events.length)return;
  if(state.index===state.events.length-1)jump(0);
  $('#play').textContent='Ⅱ 暂停';
  const tick=()=>{if(state.index>=state.events.length-1){pause();return;}jump(state.index+1);state.timer=setTimeout(tick,Number($('#speed').value));};
  state.timer=setTimeout(tick,Number($('#speed').value));
}
function render(){
  const e=state.events[state.index];
  $('#modeLabel').textContent=state.mode==='demo'?(state.chapter==='s01'?'完整循环示例 · 无 API 调用':'教学事件节选 · 非完整录制'):'真实运行 · 每 2 秒同步';
  $('#counter').textContent=`${String(e?state.index+1:0).padStart(2,'0')} / ${String(state.events.length).padStart(2,'0')}`;
  $('#eventCount').textContent=`${state.events.length} EVENTS`;
  $('#scrubber').max=Math.max(0,state.events.length-1);$('#scrubber').value=state.index;
  $('#prev').disabled=!e||state.index===0;$('#next').disabled=!e||state.index===state.events.length-1;$('#play').disabled=!e;$('#export').disabled=!e;
  $('#timeline').innerHTML=state.events.length?state.events.map((event,i)=>`<button class="event ${i===state.index?'selected':i>state.index?'future':''}" data-index="${i}" data-kind="${esc(event.kind)}" aria-current="${i===state.index?'step':'false'}"><span class="event-num">${String(i+1).padStart(2,'0')}</span><span><span class="event-title">${esc(event.title)}</span><span class="event-sub">${esc(actorNames[event.from])} → ${esc(actorNames[event.to])}${event.thread?' · '+esc(event.thread):''}</span></span><span class="event-type">${esc(event.kind)}</span></button>`).join(''):`<div class="empty">还没有本章的真实记录。<br>在项目根目录的终端执行：<code>python visualizer/record.py ${esc(state.chapter)}</code>然后输入问题。网页会自动显示新事件。</div>`;
  $$('#timeline [data-index]').forEach(b=>b.onclick=()=>{pause();jump(Number(b.dataset.index));});
  renderFlow(e);
  $('#stepTag').textContent=e?'STEP '+String(state.index+1).padStart(2,'0'):'WAITING';
  $('#direction').innerHTML=e?`<span>${esc(actorNames[e.from])}</span> → <span>${esc(actorNames[e.to])}</span>`:'';
  $('#eventTitle').textContent=e?.title??'等待一段真实的信息流';
  $('#explanation').textContent=e?explain(e):'在终端运行原章节，网页会实时读取它的录制事件。';
  $('#eventMeta').textContent=e?[e.call_id&&`call_id: ${e.call_id}`,e.thread,e.elapsed!==undefined&&`+${e.elapsed}s`,state.mode==='live'&&state.traceName].filter(Boolean).join(' · '):'';
  renderTab(e);
}
function renderFlow(e){
  const normalized=a=>a==='runtime'?'tool':a;
  $$('.participant').forEach(p=>p.classList.toggle('active',e&&[normalized(e.from),normalized(e.to)].includes(p.dataset.actor)));
  const slots={user:100,agent:300,llm:500,tool:700,runtime:700};
  let shape='';if(e){const a=slots[e.from]??300,b=slots[e.to]??300;shape=a===b?`M ${a-18} 20 C ${a-75} 65 ${a+75} 65 ${a+18} 20`:`M ${a} 14 L ${a} 43 Q ${a} 51 ${a+(b>a?8:-8)} 51 L ${b-(b>a?8:-8)} 51 Q ${b} 51 ${b} 43 L ${b} 14`;}
  $('#flowSvg').innerHTML=`<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#51917b"/></marker></defs>${[100,300,500,700].map(x=>`<line x1="${x}" y1="5" x2="${x}" y2="85" stroke="#e9eeeb" stroke-dasharray="3 5"/>`).join('')}${e?`<path class="flow" d="${shape}" fill="none" stroke="#51917b" stroke-width="2" marker-end="url(#arrow)"/>`:''}`;
  $('#flowCaption').textContent=e?`${actorNames[e.from]} → ${actorNames[e.to]} · ${e.kind}`:'等待终端中的新事件';
}
function lastRequest(){
  const current=state.events[state.index];
  const previous=state.events.slice(0,state.index+1);
  // Parallel replies must use their own request, not the latest global request.
  return previous.findLast(e=>e.kind==='llm_request' &&
    (current?.call_id ? e.call_id===current.call_id :
     state.mode==='live' && current?.thread ? e.thread===current.thread : true));
}
function renderTab(e){
  const box=$('#tabContent');$$('[data-tab]').forEach(b=>{b.classList.toggle('selected',b.dataset.tab===state.tab);b.setAttribute('aria-selected',String(b.dataset.tab===state.tab));});
  if(!state.lesson){box.innerHTML='';return;}
  if(state.tab==='source'){
    const file=e?.source?.file??state.lesson.path;const line=file===state.lesson.path?(e?.source?.line??1):1;
    box.innerHTML=`<div class="code-head"><span>${esc(state.lesson.path)}</span><span>L${line}</span></div>${file!==state.lesson.path?`<p class="tab-note">此事件来自 ${esc(file)}:${e.source.line}；下方显示当前章节源码，可切换到对应章节阅读。</p>`:''}<pre class="source">${state.lesson.source.split('\n').map((s,i)=>`<span class="source-line ${i+1===line?'selected':''}"><span class="line-number">${i+1}</span>${esc(s)}</span>`).join('')}</pre>`;
    const target=box.querySelector('.source-line.selected');if(target)box.scrollTop=target.offsetTop-box.offsetTop-50;
    return;
  }
  if(state.tab==='context'){
    const req=lastRequest();const messages=e?.kind==='context'?(Array.isArray(e.payload)?e.payload:e.payload.messages):req?.payload?.messages;
    box.innerHTML=messages?`<div class="context-stat"><span>${messages.length} 条消息</span><span>${state.mode==='live'?'最近一次请求快照':'教学示例快照'}</span></div><pre class="code-box">${colored(messages)}</pre><p class="tab-note">只展示已经观测到的快照。模型响应在 Agent 追加后，才会出现在后续请求中。真实模式遇到子 Agent 或判断器时，这里显示的是它自己的请求历史。</p>`:`<p class="tab-note">${state.mode==='demo'?'当前教学节选没有提供完整 messages[]。真实录制会保留每次 SDK 请求的上下文。':'目前尚未捕获 LLM 请求。'}</p><pre class="code-box">${colored({model:'MODEL_ID',system:'角色与运行约束',messages:[{role:'user',content:'用户输入'}, {role:'assistant',content:[{type:'tool_use',id:'toolu_1',name:'bash',input:{command:'pwd'}}]}, {role:'user',content:[{type:'tool_result',tool_use_id:'toolu_1',content:'/example/project'}]}],tools:'本章的工具 Schema',max_tokens:8000})}</pre><p class="tab-note">上方仅为协议结构示意，不代表当前场景已经发送了此请求。</p>`;
    return;
  }
  box.innerHTML=`<div class="code-head"><span>${e?'PAYLOAD / JSON':'GET STARTED'}</span>${e?'<button id="copyPayload">复制</button>':''}</div><pre class="code-box">${e?colored(e.payload):esc(`python visualizer/record.py ${state.chapter}`)}</pre>${e?.annotation?`<p class="tab-note">场景原注：${esc(e.annotation)}</p>`:''}`;
  if(e)$('#copyPayload').onclick=async()=>{try{await navigator.clipboard.writeText(pretty(e.payload));toast('已复制消息内容');}catch{toast('浏览器未允许复制，请手动选择内容');}};
  if(state.mode==='demo'&&state.chapter==='s01'){
    box.insertAdjacentHTML('beforeend','<label class="tab-note">切换演示分支 <select id="scenario" class="scenario-select"><option value="tools">创建文件 → 验证 → 回答</option><option value="chat">直接回答（无需工具）</option></select></label>');
    $('#scenario').value=state.scenario;$('#scenario').onchange=ev=>{pause();state.scenario=ev.target.value;state.events=basicDemo();jump(0);};
  }
  const name=e?.toolName??e?.payload?.name;const schema=state.lesson.schemas[name];
  if(schema)box.insertAdjacentHTML('beforeend',`<details class="schema"><summary>${esc(name)} · 从源码提取的工具 Schema</summary><pre class="code-box">${colored(schema)}</pre></details>`);
  box.insertAdjacentHTML('beforeend',`<details class="schema"><summary>本章工具定义 (${Object.keys(state.lesson.schemas).length})</summary><pre class="code-box">${colored(Object.values(state.lesson.schemas))}</pre></details>`);
}
let polling=false;
async function pollTrace(){
  if(state.mode!=='live'||!state.lesson||polling)return;polling=true;
  const chapter=state.chapter,generation=state.generation;
  try{const result=await getJSON(`/api/trace?chapter=${chapter}`);if(state.mode!=='live'||generation!==state.generation)return;
    if(result.name!==state.traceName||result.events.length!==state.events.length){
      const changed=result.name!==state.traceName;state.traceName=result.name;state.events=result.events;
      if($('#follow').checked)state.index=Math.max(0,state.events.length-1);else if(changed)state.index=0;
      state.index=Math.min(state.index,Math.max(0,state.events.length-1));render();
      if($('#follow').checked)$('#timeline .selected')?.scrollIntoView({block:'nearest'});
    }
  }catch(err){toast('无法同步录制，请检查服务是否仍在运行');}finally{polling=false;}
}
$('#play').onclick=play;$('#prev').onclick=()=>{pause();jump(state.index-1);};$('#next').onclick=()=>{pause();jump(state.index+1);};$('#reset').onclick=()=>{pause();jump(0);};$('#scrubber').oninput=e=>{pause();jump(Number(e.target.value));};
$$('[data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));$$('[data-tab]').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab;renderTab(state.events[state.index]);});
$('#helpButton').onclick=()=>$('#help').showModal();$('#closeHelp').onclick=()=>$('#help').close();$('#viewSource').onclick=()=>{state.tab='source';renderTab(state.events[state.index]);$('.detail-panel').scrollIntoView({behavior:'smooth'});};
$('#export').onclick=()=>{const blob=new Blob([pretty({format:'agent-atlas-v1',mode:state.mode,chapter:state.chapter,trace:state.traceName,events:state.events})],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`${state.chapter}-${state.mode}-trace.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
document.addEventListener('keydown',e=>{if($('#help').open||['INPUT','SELECT','TEXTAREA','BUTTON'].includes(e.target.tagName))return;if(e.key==='ArrowRight'){pause();jump(state.index+1);}if(e.key==='ArrowLeft'){pause();jump(state.index-1);}});
async function init(){
  try{const chapters=await getJSON('/api/chapters');const groups={s01:'01 — 基础循环',s05:'02 — 上下文与状态',s10:'03 — 协作与运行时',s15:'04 — 完整系统'};
    $('#chapters').innerHTML=chapters.map(c=>`${groups[c.id]?`<div class="nav-group">${groups[c.id]}</div>`:''}<button data-chapter="${c.id}" title="${esc(c.subtitle)}"><span class="num">${c.id.toUpperCase()}</span>${esc(c.title)}</button>`).join('');
    $$('#chapters button').forEach(b=>b.onclick=()=>selectChapter(b.dataset.chapter));await selectChapter('s01');setInterval(pollTrace,2000);
  }catch(err){toast('无法加载课程，请检查本地服务');}
}
init();

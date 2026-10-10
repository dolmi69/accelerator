const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const base = 'founder/blueprints/django_basic/static/';
const uuid = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const tick = async () => {for (let i=0;i<6;i++) await new Promise(resolve => setImmediate(resolve));};

function shell({failWrite=false}={}) {
  const listeners = {}, calls = [], replies = [], nodes = {}, navigations = [];
  const frame = {contentWindow:{postMessage:message => replies.push(message)}, style:{}};
  const node = id => nodes[id] ||= {textContent:'', value:'', disabled:false, hidden:false,
    options:[], handlers:{}, addEventListener(type, fn){this.handlers[type]=fn;},
    append(option){this.options.push(option);}, replaceChildren(){this.options=[];this.value='';},
    focus(){}, showModal(){this.open=true;}, close(){this.open=false;}};
  node('lab-app-routes').textContent='["/login/","/messages/"]';
  node('app-action-form').querySelector=() => ({value:'actual-csrf-token'});
  const context = {document:{querySelector:() => frame, querySelectorAll:() => [], getElementById:node, createElement:() => ({value:'',textContent:''})},
    window:{addEventListener:(type,fn)=>{listeners[type]=fn;}}, location:{assign(path){navigations.push(path);}}, crypto:{randomUUID:()=>uuid},
    setTimeout:()=>1, clearTimeout(){}, AbortController, innerHeight:800, console,
    fetch:async (path, options) => {
      calls.push({path, options});
      const fixtures={session:{authenticated:true,user:{id:1,username:'alice'},capabilities:{chat:true}},
        recipients:{recipients:[{id:2,username:'bob'}]}, results:{results:[]},
        write:{persisted:true,result:{id:'saved-id'},message_id:10,recipient:{id:2,username:'bob'},chat_url:'/messages/owned-chat/'}};
      const writing=options.method==='POST';
      return {ok:!(writing&&failWrite),json:async()=>writing ? (failWrite ? {error:'Сервер отклонил отправку.',code:'BUSY'} : fixtures.write) :
        path.includes('session') ? fixtures.session : path.includes('recipients') ? fixtures.recipients : fixtures.results};
    }};
  vm.runInNewContext(fs.readFileSync(base+'app-shell.js','utf8'),context);
  const emit = (data, origin='null', source=frame.contentWindow) => listeners.message({data,origin,source});
  const request = (method='chat.shareResult') => emit({type:'cofounder:app-call',id:uuid,method,payload:{title:'Калории',content:'2100 ккал'}});
  const submit = () => node('app-action-form').handlers.submit({preventDefault(){}});
  return {node,calls,replies,emit,request,submit,navigations};
}

test('custom navigation cannot bypass the server route allowlist',()=>{
  const s=shell();
  s.node('lab-app-routes').textContent='[]'; // Editing DOM does not change the captured allowlist.
  s.emit({type:'cofounder:app-navigation',path:'/login/'});
  s.emit({type:'cofounder:app-navigation',path:'https://evil.example/'});
  s.emit({type:'cofounder:app-navigation',path:'/request/'});
  s.emit({type:'cofounder:app-navigation',path:'/messages/'},'http://evil.example');
  assert.deepEqual(s.navigations,['/login/']);
});

test('custom request buttons dispatch navigation through the trusted parent',()=>{
  const clicks=[], messages=[];
  class Element {closest(selector){return selector==='a[data-app-route]' ? {dataset:{appRoute:'/request/'}} : null;}}
  const parent={postMessage:message=>messages.push(message)};
  const window={addEventListener(){}};
  vm.runInNewContext(fs.readFileSync(base+'prototype-bridge.js','utf8'),{
    parent,window,Element,crypto:{randomUUID:()=>uuid},setTimeout:()=>1,clearTimeout(){},
    document:{readyState:'complete',documentElement:{scrollHeight:800},body:{},
      addEventListener(type,fn){if(type==='click') clicks.push(fn);}},
    ResizeObserver:class {observe(){}},console});
  let prevented=false;
  clicks[0]({target:new Element(),preventDefault(){prevented=true;},stopImmediatePropagation(){}});
  assert.equal(prevented,true);
  assert.equal(messages.at(-1).type,'cofounder:app-navigation');
  assert.equal(messages.at(-1).path,'/request/');
});

test('opaque iframe can only request allowlisted actions; other sources and origins cannot call the server',async()=>{
  const s=shell();
  const data={type:'cofounder:app-call',id:uuid,method:'auth.getSession'};
  s.emit(data,'http://evil.example'); s.emit(data,'null',{});
  assert.equal(s.calls.length,0);
  s.emit({...data,method:'readPlatformKey'}); await tick();
  assert.equal(s.calls.length,0); assert.equal(s.replies[0].ok,false);
});

test('sharing requires a recipient and explicit confirmation; success follows the server write',async()=>{
  const s=shell(); s.request(); await tick();
  assert.equal(s.node('app-action-dialog').open,true);
  assert.equal(s.calls.filter(c=>c.options.method==='POST').length,0);
  await s.submit(); assert.equal(s.calls.filter(c=>c.options.method==='POST').length,0);
  s.node('app-recipient').value='2'; await s.submit();
  const write=s.calls.find(c=>c.options.method==='POST');
  assert.equal(write.path,'/app-api/results/share/');
  assert.equal(write.options.headers['X-CSRFToken'],'actual-csrf-token');
  assert.equal(JSON.parse(write.options.body).recipient_id,2);
  assert.equal(s.replies[0].ok,true); assert.equal(s.replies[0].value.message_id,10);
});

test('failed writes do not report success and retries reuse the same nonce',async()=>{
  const s=shell({failWrite:true}); s.request(); await tick(); s.node('app-recipient').value='2';
  await s.submit(); assert.equal(s.replies.length,0); assert.equal(s.node('app-action-dialog').open,true);
  assert.match(s.node('app-action-status').textContent,/отклонил/);
  await s.submit();
  const writes=s.calls.filter(c=>c.options.method==='POST').map(c=>JSON.parse(c.options.body));
  assert.equal(writes.length,2); assert.equal(writes[0].nonce,writes[1].nonce);
});

test('cancelled confirmation never writes a result or message',async()=>{
  const s=shell(); s.request(); await tick(); s.node('app-action-cancel').handlers.click();
  assert.equal(s.calls.filter(c=>c.options.method==='POST').length,0);
  assert.equal(s.replies[0].ok,false); assert.equal(s.replies[0].code,'CANCELLED');
});

test('child bridge ignores forged success events and propagates authentic server errors',async()=>{
  const listeners={},messages=[];
  const parent={postMessage:message=>messages.push(message)};
  const window={addEventListener:(type,fn)=>{listeners[type]=fn;}};
  vm.runInNewContext(fs.readFileSync(base+'prototype-bridge.js','utf8'),{
    parent,window,crypto:{randomUUID:()=>uuid},setTimeout:()=>1,clearTimeout(){},
    document:{readyState:'loading',addEventListener(){}}, console});
  assert.ok(Object.isFrozen(window.BrunoApp));
  let resolved=false;
  const call=window.BrunoApp.chat.shareResult({title:'Калории',content:'2100'}).then(()=>{resolved=true;},error=>error);
  const reply={type:'cofounder:app-result',id:uuid,ok:true,value:{persisted:true}};
  listeners.message({isTrusted:false,source:parent,data:reply});
  listeners.message({isTrusted:true,source:{},data:reply}); await tick(); assert.equal(resolved,false);
  listeners.message({isTrusted:true,source:parent,data:{...reply,ok:false,error:'Войдите в аккаунт',code:'AUTH_REQUIRED'}});
  const failure=await call; assert.equal(failure.code,'AUTH_REQUIRED'); assert.equal(resolved,false);
});

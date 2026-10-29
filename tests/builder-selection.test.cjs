const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const geometry=require('../static/founder/js/free-canvas.js');
const clone=v=>JSON.parse(JSON.stringify(v));
function harness(){
 const window={};vm.runInNewContext(fs.readFileSync(require.resolve('../static/founder/js/free-canvas.js'),'utf8'),{window});
 let id=0,active;const pages=[{id:'p',blocks:[]}],history=[],toasts=[];
 const api={clone,uid:()=>`e${++id}`,theme:()=>({accent:'#147d73',ink:'#122338'}),block:()=>active,device:()=> 'desktop',pages:()=>pages,
 mutate:fn=>{history.push(clone(pages));fn();},esc:x=>x,toast:x=>toasts.push(x),frame:{},inspector:{querySelector:()=>null},config:{assets:[]},refresh:()=>{},selectBlock:id=>active=pages[0].blocks.find(b=>b.id===id)};
 const free=window.BrunoFreeCanvas(api);
 function addBlock(id){const b={id,type:'canvas',props:{height:620,mobileHeight:740,elements:[]}};pages[0].blocks.push(b);active=b;free.current();free.insertClipboard(b,[{type:'text',text:'First'},{type:'text',text:'Second'}],{point:{block:id,device:'desktop',x:50,y:50}},()=>{});return b;}
 const b=addBlock('first');return {free,b,pages,history,toasts,addBlock};
}
function key(key,more={}){return {key,target:{closest:()=>null},preventDefault(){},...more};}
function input(dataset,value,type='text'){return {dataset,value,type,validity:{valid:true}};}
test('group drag clamps a common delta and preserves spacing at the edge',()=>{
 const records=[{box:{x:20,y:30,width:200,height:80},width:390,height:620},{box:{x:250,y:150,width:100,height:80},width:390,height:620}];
 const moved=geometry.moveGroup(records,200,-100);assert.equal(moved[0].x,60);assert.equal(moved[1].x,290);assert.equal(moved[0].y,0);assert.equal(moved[1].y,120);assert.equal(records[0].box.x,20);
});
test('group resize preserves proportions and rejects overflow before mutation',()=>{
 const records=[{box:{x:20,y:20,width:100,height:40,fontSize:20},width:390,height:620},{box:{x:140,y:20,width:100,height:40,fontSize:20},width:390,height:620}];
 const result=geometry.resizeGroup(records,110,20,'se');assert.equal(result[0].width,150);assert.equal(result[1].x,200);assert.equal(result[0].fontSize,30);
 assert.equal(geometry.resizeGroup(records,600,100,'se'),null);
 assert.equal(geometry.intersects({x:0,y:0,width:100,height:100},{x:50,y:50,width:10,height:10}),true);
});
test('multi-selection edits both styles, nudges both in one history step, and leaves phone untouched',()=>{
 const h=harness(),[a,b]=h.b.props.elements,phone=clone(a.mobile);h.free.select(a.id);h.free.select(b.id,{toggle:true});
 assert.equal(h.free.selection().length,2);const before=h.history.length,ax=a.desktop.x,bx=b.desktop.x;
 h.free.keydown(key('ArrowRight',{shiftKey:true}));assert.equal(a.desktop.x,ax+10);assert.equal(b.desktop.x,bx+10);assert.equal(h.history.length,before+1);assert.deepEqual(clone(a.mobile),phone);
 h.free.input(input({freeColor:'color'},'#ff0055'));assert.equal(a.style.color,'#ff0055');assert.equal(b.style.color,'#ff0055');
 h.free.input(input({freeBox:'fontSize'},'24','number'));assert.equal(a.desktop.fontSize,24);assert.equal(b.desktop.fontSize,24);
 h.free.input(input({freeMember:a.id,freeContent:'text'},'Changed first'));h.free.input(input({freeMember:b.id,freeContent:'text'},'Changed second'));assert.equal(a.content.text,'Changed first');assert.equal(b.content.text,'Changed second');assert.equal(h.free.selection().length,2);
});
test('selection spans sections, protects locked elements and duplicates/deletes atomically',()=>{
 const h=harness(),first=h.b.props.elements[0],secondBlock=h.addBlock('second'),second=secondBlock.props.elements[0];
 h.free.select(first.id);h.free.select(second.id,{toggle:true});assert.equal(h.free.selection().length,2);
 second.locked=true;h.free.action('delete');assert.equal(h.pages[0].blocks[0].props.elements.length,2);assert.equal(secondBlock.props.elements.length,2);assert.ok(h.toasts.length);
 h.free.input({...input({freeToggle:'locked'},''),checked:false});h.free.action('duplicate');assert.equal(h.free.selection().length,2);assert.equal(h.pages[0].blocks[0].props.elements.length,3);assert.equal(secondBlock.props.elements.length,3);
 h.free.action('delete');assert.equal(h.pages[0].blocks[0].props.elements.length,2);assert.equal(secondBlock.props.elements.length,2);assert.equal(h.free.selection().length,0);
});
test('select all excludes decorations and hidden elements; clearing and toggle remove selection',()=>{
 const h=harness(),[a,b]=h.b.props.elements;a.locked=true;h.free.keydown(key('a',{metaKey:true}));assert.equal(h.free.selection().length,1);assert.equal(h.free.item().id,b.id);
 h.free.select(b.id,{toggle:true});assert.equal(h.free.selection().length,0);h.free.select(b.id);h.free.clear();assert.equal(h.free.selection().length,0);
});

function pointerHarness(){
 const h=harness(),listeners={},stageListeners={},nodes=new Map(),overlays=[],view={};
 const makeNode=()=>({dataset:{},style:{setProperty(){}},classList:{toggle(){}},append(){},remove(){},querySelector(){return null;},querySelectorAll(){return[];}});
 const canvas={...makeNode(),getBoundingClientRect:()=>({left:0,top:100,width:1120,height:620}),closest:()=>({dataset:{blockId:h.b.id}})};
 for(const e of h.b.props.elements){const n={...makeNode(),dataset:{freeId:e.id},getBoundingClientRect:()=>({left:e.desktop.x,top:e.desktop.y+100,width:e.desktop.width,height:e.desktop.height}),closest:selector=>selector==='[data-free-id]'?n:selector==='.bb-free-canvas'?canvas:null};nodes.set(e.id,n);}
 const doc={body:{...makeNode(),append:n=>overlays.push(n),setPointerCapture(){}},head:{append(){}},createElement:()=>makeNode(),addEventListener:(k,fn)=>(listeners[k]||=[]).push(fn),
 querySelectorAll:selector=>selector==='[data-free-id]'?[...nodes.values()]:selector==='.bb-free-canvas'?[canvas]:[],querySelector:selector=>nodes.get(selector.match(/data-free-id="(.*?)"/)?.[1])||null};
 const stage={closest:()=>null,ownerDocument:doc,scrollTop:0,append:n=>overlays.push(n),setPointerCapture(){},getBoundingClientRect:()=>({left:250,top:200}),addEventListener:(k,fn)=>(stageListeners[k]||=[]).push(fn)};
 // Rebuild the controller with the same actual document and event surface.
 const window={};vm.runInNewContext(fs.readFileSync(require.resolve('../static/founder/js/free-canvas.js'),'utf8'),{window});let free;
 const api={clone,esc:x=>x,uid:()=>String(Math.random()),block:()=>h.b,page:()=>h.pages[0],pages:()=>h.pages,device:()=> 'desktop',theme:()=>({accent:'#147d73'}),config:{assets:[]},stage,frame:{dataset:{scale:'.6'},getBoundingClientRect:()=>({left:300,top:200})},inspector:{querySelector:()=>null},refresh:()=>free.updateSelection(),selectBlock(){},toast(){},mutate:fn=>{h.history.push(clone(h.pages));fn();free.updateSelection();}};
 free=window.BrunoFreeCanvas(api);free.bind(doc,false);
 const blank={id:'',closest:selector=>selector==='.bb-free-canvas'?canvas:null};
 function fire(type,x,y,{external=false,target=blank,...more}={}){const ev={target,button:0,pointerId:1,clientX:x,clientY:y,preventDefault(){},stopImmediatePropagation(){this.stopped=true;},...more};for(const fn of (external?stageListeners:listeners)[type]||[])fn(ev);return ev;}
 return {...h,free,fire,nodes,stage};
}
test('a marquee selects actual hit elements, then dragging either moves the whole group once',()=>{
 const h=pointerHarness(),[a,b]=h.b.props.elements,initial=clone([a.desktop,b.desktop]),phone=clone([a.mobile,b.mobile]);
 h.fire('pointerdown',10,120);h.fire('pointermove',700,300);h.fire('pointerup',700,300);assert.equal(h.free.selection().length,2);
 const before=h.history.length;
 h.fire('pointerdown',100,170,{target:h.nodes.get(a.id)});h.fire('pointermove',130,190,{target:h.nodes.get(a.id),altKey:true});h.fire('pointerup',130,190);
 assert.equal(h.history.length,before+1);assert.equal(a.desktop.x,initial[0].x+30);assert.equal(b.desktop.x,initial[1].x+30);assert.equal(a.desktop.y,initial[0].y+20);assert.equal(b.desktop.y,initial[1].y+20);assert.deepEqual(clone([a.mobile,b.mobile]),phone);
});
test('marquee can start in the scaled outer gutter, ignores locked backgrounds and suppresses its final click',()=>{
 const h=pointerHarness();h.b.props.elements[0].locked=true;
 h.fire('pointerdown',288,260,{external:true,target:h.stage});h.fire('pointermove',750,440,{external:true,target:h.stage});h.fire('pointerup',750,440,{external:true,target:h.stage});
 assert.equal(h.free.selection().length,1);assert.equal(h.free.selection()[0],h.b.props.elements[1].id);
 const click=h.fire('click',750,440,{external:true,target:h.stage});assert.equal(click.stopped,true);
});

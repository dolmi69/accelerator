const test=require('node:test');
const assert=require('node:assert/strict');
const clipboard=require('../static/founder/js/builder-clipboard.js');
const geometry=require('../static/founder/js/free-canvas.js');
test('clipboard prioritizes actual image bytes over accompanying web descriptions',()=>{
 const file={name:'clipboard.png',type:'image/png',size:1234};
 assert.deepEqual(clipboard.classify({images:[file],text:'https://example.com/'}),{type:'image',files:[file]});
});
test('HTTPS and www become buttons; ordinary text and multiline URLs stay intact',()=>{
 assert.deepEqual(clipboard.classify({text:'www.example.com/path'}),{type:'button',url:'https://www.example.com/path',text:'example.com'});
 assert.equal(clipboard.classify({text:'https://example.com/search?q=hello'}).url,'https://example.com/search?q=hello');
 assert.deepEqual(clipboard.classify({text:'Первая строка\r\nВторая строка'}),{type:'text',text:'Первая строка\nВторая строка'});
 assert.equal(clipboard.classify({text:'https://one.test\nhttps://two.test'}).type,'text');
});
test('copied anchor keeps its label and cannot override unrelated selected text',()=>{
 assert.deepEqual(clipboard.classify({text:'О проекте',htmlLink:{text:'О проекте',url:'https://example.com/about'}}),{type:'button',text:'О проекте',url:'https://example.com/about'});
 assert.deepEqual(clipboard.classify({text:'Другой текст',htmlLink:{text:'О проекте',url:'https://example.com/'}}),{type:'text',text:'Другой текст'});
});
test('URI list, page links and contacts are recognized without arbitrary schemes',()=>{
 assert.equal(clipboard.classify({uri:'# Browser comment\nhttps://example.com/'}).type,'button');
 for(const url of ['/about/','#features','mailto:team@example.com','tel:+79991234567'])assert.equal(clipboard.classify({text:url}).url,url);
 for(const url of ['javascript:alert(1)','data:text/html,test','file:///private/file','//evil.test','https://user:secret@example.com','https://example.com/ backslash'])assert.equal(clipboard.classify({text:url}).type,'text');
});
test('empty data is ignored; oversized input is rejected before the site changes',()=>{
 assert.equal(clipboard.classify({text:'  '}),null);
 assert.match(clipboard.classify({text:'x'.repeat(5001)}).error,/5000/);
 assert.match(clipboard.classify({images:[{size:6*1024*1024}]}).error,/5 МБ/);
 assert.match(clipboard.classify({images:Array.from({length:11},()=>({size:1}))}).error,/10/);
});
test('HTML text is accepted only as plain content, never site markup',()=>{
 assert.deepEqual(clipboard.classify({htmlText:'Заголовок\nАбзац'}),{type:'text',text:'Заголовок\nАбзац'});
 assert.deepEqual(clipboard.classify({text:'<script>alert(1)</script>'}),{type:'text',text:'<script>alert(1)</script>'});
});
test('paste geometry preserves image aspect ratio and stays inside both artboards',()=>{
 for(const [width,limit]of [[1120,4000],[390,6000]]){
 const box=geometry.pasteBox('image','',{width:2000,height:1000},width,limit,{x:9000,y:9000});
 assert.ok(box.x+box.width<=width);assert.ok(box.y+box.height+24<=limit);assert.equal(box.width,box.height*2);
 const text=geometry.pasteBox('text','слово '.repeat(800),null,width,limit,{x:0,y:0});assert.ok(text.height<=limit);assert.ok(Number.isInteger(text.height));
 }
});
test('text editing detection preserves ordinary paste in inputs and inline text',()=>{
 assert.equal(clipboard.editing({closest:()=>({})}),true);assert.equal(clipboard.editing({closest:()=>null}),false);
});

test('clipboard reader accepts screenshot bytes from items and file fallback without reading HTML',()=>{
 const file={name:'screenshot.png',type:'image/png',size:321};
 for(const data of [
  {items:[{kind:'file',type:'image/png',getAsFile:()=>file}],files:[]},
  {items:[],files:[file]},
 ]){
  data.getData=type=>{assert.notEqual(type,'text/html');return type==='text/plain'?'browser description':'';};
  assert.deepEqual(clipboard.inspect(data,{}),{type:'image',files:[file]});
 }
});

function canvasHarness(){
 const fs=require('node:fs'),vm=require('node:vm');
 const clone=value=>JSON.parse(JSON.stringify(value));
 const window={};vm.runInNewContext(fs.readFileSync(require.resolve('../static/founder/js/free-canvas.js'),'utf8'),{window});
 const b={id:'canvas',type:'canvas',props:{height:620,mobileHeight:740,elements:[]}};
 const other={id:'other',type:'hero',props:{title:'Do not change'}};
 const pages=[{id:'one',blocks:[b]},{id:'two',blocks:[other]}],history=[];
 let active=b,id=0;
 const api={clone,uid:()=>`element-${++id}`,theme:()=>({accent:'#147d73',ink:'#122338'}),block:()=>active,device:()=> 'desktop',pages:()=>pages,
  mutate:action=>{history.push(clone(pages));action();},esc:x=>x,toast:()=>{},frame:{},inspector:{},config:{assets:[]}};
 return {free:window.BrunoFreeCanvas(api),b,pages,history,clone,setActive:value=>active=value};
}

test('pasted images and links use real element insertion in one undo step, preserving previous content',()=>{
 const h=canvasHarness();
 h.free.insertClipboard(h.b,[{type:'text',text:'Existing content'}],{point:null},()=>{});
 const original=h.clone(h.b.props.elements[0]),originalProps=h.b.props;
 const asset={id:'owned-project-image',name:'screenshot.png',width:2000,height:1000};
 h.free.insertClipboard(h.b,[{type:'image',asset},{type:'button',url:'https://example.com/',text:'Open'}],{point:{block:'canvas',device:'desktop',x:700,y:300}},()=>{});
 assert.equal(h.history.length,2);assert.equal(h.b.props.elements.length,3);
 assert.deepEqual(h.b.props.elements[0],original);assert.equal(originalProps.elements.length,1);
 const [image,button]=h.b.props.elements.slice(1);
 assert.equal(image.content.image,asset.id);assert.equal(image.desktop.x,700);assert.equal(image.desktop.width,420);assert.equal(image.desktop.height,210);
 assert.equal(button.content.link,'https://example.com/');assert.equal(button.style.background,'#147d73');
 for(const e of [image,button])for(const d of ['desktop','mobile']){
  assert.ok(e[d].x+e[d].width<=(d==='mobile'?390:1120));assert.ok(e[d].y+e[d].height<=h.b.props[d==='mobile'?'mobileHeight':'height']);
 }
 assert.equal(h.history.at(-1)[0].blocks[0].props.elements.length,1);
 assert.deepEqual(h.pages[1].blocks[0],{id:'other',type:'hero',props:{title:'Do not change'}});
});

test('delayed paste keeps the originating page and does not select a block on another page',()=>{
 const h=canvasHarness(),other=h.pages[1].blocks[0];h.setActive(other);
 h.free.insertClipboard(h.b,[{type:'image',asset:{id:'owned',width:100,height:50}}],{point:{block:'canvas',device:'mobile',x:50,y:90}},()=>{});
 assert.equal(h.b.props.elements[0].mobile.x,50);assert.equal(h.b.props.elements[0].mobile.y,90);
 assert.equal(h.free.item(),null);assert.equal(h.pages[1].blocks.length,1);assert.equal(other.props.title,'Do not change');
});

test('creating a free block and inserting clipboard content are committed together',()=>{
 const h=canvasHarness(),page=h.pages[1],original=h.clone(page.blocks);
 const newBlock={id:'new-canvas',type:'canvas',props:{height:620,mobileHeight:740,elements:[]}};
 h.free.insertClipboard(newBlock,[{type:'text',text:'New block text'}],{point:null},()=>{page.blocks.push(newBlock);h.setActive(newBlock);});
 assert.equal(h.history.length,1);assert.deepEqual(h.history[0][1].blocks,original);
 assert.equal(page.blocks.length,2);assert.equal(h.free.item().content.text,'New block text');
 assert.deepEqual(page.blocks[0],original[0]);
});

test('unsupported image files explain the accepted formats instead of becoming a URL',()=>{
 const data={items:[{kind:'file',type:'image/svg+xml'}],files:[],getData:()=> 'https://example.com/image.svg'};
 assert.match(clipboard.inspect(data,{}).error,/PNG, JPG или WebP/);
});

const test=require('node:test');
const assert=require('node:assert/strict');
const geometry=require('../static/founder/js/free-canvas.js');
test('drag clamps to artboard, without changing the original box',()=>{
 const box={x:20,y:30,width:120,height:80,fontSize:32,hidden:false};
 assert.deepEqual(geometry.move(box,9999,-90,390,740),{...box,x:270,y:0});assert.equal(box.x,20);
});
test('resize anchors the opposite edge and keeps positive dimensions',()=>{
 const box={x:100,y:80,width:200,height:140};
 assert.deepEqual(geometry.resize(box,30,25,'nw',1120,620),{x:130,y:105,width:170,height:115});
 assert.equal(geometry.resize(box,99999,0,'w',1120,620).width,16);
 assert.equal(geometry.resize(box,99999,99999,'se',1120,620).height,540);
});
test('guides snap centers and edges, far movement remains unchanged',()=>{
 const box={x:204,y:124,width:200,height:100};
 const snapped=geometry.snap(box,[{x:400,y:120,width:200,height:100}],1120,620);
 assert.equal(snapped.box.x,200);assert.equal(snapped.box.y,120);assert.equal(snapped.guides.x,400);
 assert.deepEqual(geometry.snap({x:73,y:87,width:50,height:40},[],1120,620).guides,{});
});
test('mobile layout uses integer coordinates, reading order, and keeps decorations behind content',()=>{
 const element=(id,type,x,y,width,height,fontSize)=>({id,type,desktop:{x,y,width,height,fontSize,hidden:false},mobile:{}});
 const elements=[element('decor','shape',720,150,300,300,20),element('button','button',80,420,220,56,16),element('text','text',80,150,600,211,64),element('photo','image',730,160,280,280,20)];
 const result=geometry.mobileLayout(elements);
 assert.equal(result.boxes.text.y,28);assert.ok(result.boxes.photo.y>result.boxes.text.y+result.boxes.text.height);assert.ok(result.boxes.button.y>result.boxes.photo.y);
 for(const box of Object.values(result.boxes))for(const key of ['x','y','width','height','fontSize'])assert.ok(Number.isInteger(box[key]));
 assert.ok(Number.isInteger(result.height));assert.equal(elements[2].desktop.y,150);
});
test('resizing a rotated element follows its local axes and anchors the opposite edge',()=>{
 const box={x:100,y:80,width:200,height:140};
 assert.deepEqual(geometry.resize(box,0,50,'e',1120,620,90),{x:75,y:105,width:250,height:140});
});

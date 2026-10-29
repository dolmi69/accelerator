/* Free layout: coordinates belong to the document; editor handles never reach a release. */
(() => {
  'use strict';
  const WIDTH={desktop:1120,mobile:390};
  const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  const Geometry={
    pasteBox(type,text,asset,width,limit,point){
      let w,h,fontSize=20;
      if(type==='image'){
        const ratio=Math.min(1,Math.min(420,width-48)/asset.width,Math.min(960,limit-48)/asset.height);
        w=Math.max(8,Math.round(asset.width*ratio));h=Math.max(4,Math.round(asset.height*ratio));
      }else{
        fontSize=type==='button'?16:text.length<90&&!text.includes('\n')?32:20;
        w=Math.min(type==='button'?280:560,width-48);
        const columns=Math.max(1,Math.floor(w/(fontSize*.56))),lines=text.split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil(line.length/columns)),0);
        h=Math.min(limit-48,Math.max(type==='button'?56:48,Math.ceil(lines*fontSize*1.3+(type==='button'?24:8))));
      }
      return {x:Math.round(clamp(point.x,0,width-w)),y:Math.round(clamp(point.y,0,limit-h-24)),width:w,height:h,fontSize,hidden:false};
    },
    mobileLayout(elements){
      const boxes={},content=elements.filter(e=>!['shape','line'].includes(e.type)).sort((a,b)=>a.desktop.y-b.desktop.y||a.desktop.x-b.desktop.x);
      let y=28;
      for(const e of content){const d=e.desktop,w=Math.min(d.width,334),font=Math.min(d.fontSize,36),factor=font/d.fontSize;
        const h=e.type==='text'?Math.max(48,Math.ceil(d.height*factor*Math.max(1,d.width/w*factor))):['image','art'].includes(e.type)?Math.round(d.height*w/d.width):d.height;
        boxes[e.id]={x:28,y,width:w,height:Math.min(h,1200),fontSize:font,hidden:d.hidden};y+=boxes[e.id].height+24;
      }
      if(y>6000)return null;
      const height=Math.max(300,y);
      for(const e of elements.filter(e=>['shape','line'].includes(e.type))){const d=e.desktop,w=Math.max(8,Math.round(d.width*390/1120)),h=Math.min(height,Math.max(4,Math.round(d.height*390/1120)));
        boxes[e.id]={...e.mobile,x:Math.round(clamp(d.x*390/1120,0,390-w)),y:Math.round(clamp(d.y*390/1120,0,height-h)),width:w,height:h,fontSize:Math.min(d.fontSize,36),hidden:d.hidden};
      }
      return {height,boxes};
    },
    move(box,dx,dy,width,height){return {...box,x:Math.round(clamp(box.x+dx,0,width-box.width)),y:Math.round(clamp(box.y+dy,0,height-box.height))};},
    resize(box,dx,dy,handle,width,height,rotation=0){
      if(rotation){
        const angle=rotation*Math.PI/180,c=Math.cos(angle),s=Math.sin(angle),localX=c*dx+s*dy,localY=-s*dx+c*dy;
        const sx=handle.includes('w')?-1:handle.includes('e')?1:0,sy=handle.includes('n')?-1:handle.includes('s')?1:0;
        const w=Math.round(clamp(box.width+sx*localX,16,width)),h=Math.round(clamp(box.height+sy*localY,8,height));
        const shiftX=sx*(w-box.width)/2,shiftY=sy*(h-box.height)/2;
        return {...box,x:Math.round(clamp(box.x+box.width/2+c*shiftX-s*shiftY-w/2,0,width-w)),y:Math.round(clamp(box.y+box.height/2+s*shiftX+c*shiftY-h/2,0,height-h)),width:w,height:h};
      }
      let l=box.x,t=box.y,r=l+box.width,b=t+box.height;
      if(handle.includes('w'))l=clamp(l+dx,0,r-16);
      if(handle.includes('e'))r=clamp(r+dx,l+16,width);
      if(handle.includes('n'))t=clamp(t+dy,0,b-8);
      if(handle.includes('s'))b=clamp(b+dy,t+8,height);
      return {...box,x:Math.round(l),y:Math.round(t),width:Math.round(r-l),height:Math.round(b-t)};
    },
    snap(box,others,width,height,tolerance=6){
      const result={...box},guides={};
      for(const [axis,size,limit]of [['x','width',width],['y','height',height]]){
        const targets=[0,limit/2,limit,...others.flatMap(b=>[b[axis],b[axis]+b[size]/2,b[axis]+b[size]])];
        let best=tolerance+.001,delta=0,target;
        for(const offset of [0,box[size]/2,box[size]])for(const line of targets){const distance=line-box[axis]-offset;if(Math.abs(distance)<best){best=Math.abs(distance);delta=distance;target=line;}}
        if(target!==undefined){result[axis]=Math.round(clamp(box[axis]+delta,0,limit-box[size]));if(Math.abs(result[axis]-box[axis]-delta)<1)guides[axis]=target;}
      }
      return {box:result,guides};
    }
  };
  if(typeof module!=='undefined'&&module.exports)module.exports=Geometry;
  if(typeof window==='undefined')return;
  window.BrunoFreeCanvas=function(api){
    const {esc,uid,clone,mutate,toast,frame,inspector,config}=api;
    const names={text:'Текст',image:'Изображение',button:'Кнопка',shape:'Фигура',line:'Линия',art:'Иллюстрация'};
    const icons={text:'T',image:'▧',button:'↗',shape:'□',line:'—',art:'✳'};
    let selected=null,owner=null,gesture=null,doc=null,readonly=false,lastPoint=null,pendingReveal=null;
    const block=()=>api.block(),device=()=>api.device();
    const active=()=>block()?.type==='canvas';
    const item=()=>active()?block().props.elements.find(e=>e.id===selected):null;
    const height=()=>block().props[device()==='mobile'?'mobileHeight':'height'];
    const baseStyle={color:'#122338',background:'transparent',borderColor:'#147d73',borderWidth:0,radius:0,fontWeight:'500',fontFamily:'inherit',textAlign:'left',opacity:100,rotation:0,objectFit:'cover'};
    function current(){if(owner!==block()?.id){owner=block()?.id;selected=null;}if(selected&&!item())selected=null;}
    function newElement(type){
      const sizes={text:[450,120,48],image:[340,280,20],button:[220,56,16],shape:[260,200,20],line:[320,4,20],art:[340,340,20]},[w,h,fs]=sizes[type];
      const style={...baseStyle};if(['button','shape','line'].includes(type))style.background=documentAccent();
      if(type==='button')Object.assign(style,{color:'#ffffff',radius:8,textAlign:'center',fontWeight:'600'});
      if(type==='shape')style.radius=20;
      return {id:uid(),type,content:{text:type==='text'?'Ваш текст':type==='button'?'Открыть':'',image:'',alt:'',link:'#'},desktop:{x:80,y:80,width:w,height:h,fontSize:fs,hidden:false},mobile:{x:30,y:60,width:Math.min(w,330),height:h,fontSize:Math.min(fs,34),hidden:false},style,locked:false};
    }
    function documentAccent(){return api.theme().accent;}
    function layers(b){
      return `<div class="vf-layers" aria-label="Слои свободного блока">${[...b.props.elements].reverse().map(e=>`<button class="vf-layer ${selected===e.id?'active':''}" data-free-select="${e.id}" data-free-block="${b.id}" title="${esc(names[e.type])}" aria-label="Слой: ${esc((e.content.text||e.content.alt||names[e.type]).slice(0,70))}"><span>${icons[e.type]}</span><span>${esc((e.content.text||e.content.alt||names[e.type]).slice(0,42))}</span><small>${e.locked?'▣':e[device()].hidden?'◌':''}</small></button>`).join('')||'<p>Добавьте первый элемент</p>'}</div>`;
    }
    function select(id){selected=id;owner=block()?.id;api.refresh();}
    function boxInput(key,label,value,options=''){return `<label class="vb-field"><span>${label}</span><input data-free-box="${key}" type="number" value="${value}" step="1" ${options} ${item()?.locked&&key!=='fontSize'?'disabled':''}></label>`;}
    function choice(key,label,value,choices){return `<label class="vb-field"><span>${label}</span><select data-free-style="${key}">${choices.map(([v,l])=>`<option value="${v}" ${v===value?'selected':''}>${l}</option>`).join('')}</select></label>`;}
    function color(key,label,value){return `<label class="vb-field"><span>${label}</span><div class="vb-color-row"><input type="color" data-free-style="${key}" value="${value==='transparent'?'#ffffff':value}" aria-label="${label}"><input data-free-color="${key}" value="${value}" aria-label="${label} HEX"></div></label>`;}
    function inspectorHTML(){
      current();const e=item(),b=block(),mobile=device()==='mobile';
      let html=`<div class="vf-heading"><span class="vf-badge">Свободный блок</span><strong>${mobile?'Телефон · 390 px':'Компьютер · 1120 px'}</strong></div>`;
      html+=`<div class="vf-add-tools" role="group" aria-label="Добавить свободный элемент">${Object.keys(names).map(k=>`<button data-free-add="${k}" title="Добавить: ${names[k]}"><span>${icons[k]}</span>${names[k]}</button>`).join('')}</div>`;
      html+=`<p class="vb-control-note">Перетащите элемент. Потяните за угол, чтобы изменить размер. Двойной клик по тексту — редактировать. Ctrl/⌘V вставит текст, картинку или ссылку.</p>`;
      if(!e){
        html+=`<label class="vb-field"><span>Высота блока на ${mobile?'телефоне':'компьютере'}, px</span><input type="number" min="160" max="${mobile?6000:4000}" data-free-height value="${height()}"></label><button class="vb-btn" data-free-action="mobile-stack">Собрать версию для телефона</button><p class="vb-control-note">Тексты, картинки и кнопки встанут друг под другом. Оформление компьютера сохранится.</p>`;
        html+=`<div class="vb-separator"></div><div class="vf-order"><button class="vb-btn" data-action="block-up">Блок выше</button><button class="vb-btn" data-action="block-down">Блок ниже</button></div><button class="vb-btn" data-action="block-duplicate">Копировать блок</button><button class="vb-btn vb-danger" data-action="block-delete">Удалить блок</button>`;
        return html;
      }
      const box=e[device()];
      html+=`<div class="vf-element-heading"><h2>${names[e.type]}</h2><button data-free-action="deselect" class="vb-icon" aria-label="Настройки свободного блока">×</button></div>`;
      if(['text','button'].includes(e.type))html+=`<label class="vb-field"><span>Текст</span><textarea data-free-content="text" maxlength="5000" rows="3">${esc(e.content.text)}</textarea></label>`;
      if(e.type==='button')html+=`<label class="vb-field"><span>Ссылка</span><input data-free-content="link" value="${esc(e.content.link)}" placeholder="/about/ или https://…" maxlength="1000"></label>`;
      if(e.type==='image'){
        const asset=config.assets.find(a=>a.id===e.content.image);
        html+=`<div class="vf-image-control">${asset?`<img src="${esc(asset.url)}" alt="">`:''}<button class="vb-btn" data-free-action="image">${asset?'Заменить изображение':'Выбрать изображение'}</button></div><label class="vb-field"><span>Описание картинки</span><input data-free-content="alt" value="${esc(e.content.alt)}" maxlength="300"></label>`;
      }
      html+=`<p class="vf-device-note">Положение и размер · ${mobile?'только на телефоне':'только на компьютере'}</p><div class="vf-grid">${boxInput('x','Слева, px',box.x,'min="0"')}${boxInput('y','Сверху, px',box.y,'min="0"')}${boxInput('width','Ширина, px',box.width,'min="16"')}${boxInput('height','Высота, px',box.height,'min="4"')}</div>`;
      html+=`<div class="vf-align" role="group" aria-label="Выравнивание элемента"><button data-free-align="left" title="К левому краю" aria-label="К левому краю">╞</button><button data-free-align="center" title="По центру по горизонтали" aria-label="По центру по горизонтали">↔</button><button data-free-align="right" title="К правому краю" aria-label="К правому краю">╡</button><button data-free-align="top" title="К верхнему краю" aria-label="К верхнему краю">⊤</button><button data-free-align="middle" title="По центру по вертикали" aria-label="По центру по вертикали">↕</button><button data-free-align="bottom" title="К нижнему краю" aria-label="К нижнему краю">⊥</button></div>`;
      if(['text','button'].includes(e.type))html+=boxInput('fontSize','Размер текста, px',box.fontSize,'min="8" max="240"')+color('color','Цвет текста',e.style.color)+choice('fontFamily','Шрифт',e.style.fontFamily,[['inherit','Шрифт сайта'],['modern','Современный'],['serif','С засечками'],['mono','Моноширинный']])+choice('fontWeight','Насыщенность',e.style.fontWeight,[['400','Обычный'],['500','Средний'],['600','Полужирный'],['700','Жирный'],['800','Очень жирный']])+choice('textAlign','Текст',e.style.textAlign,[['left','Слева'],['center','По центру'],['right','Справа']]);
      html+=`<details class="vf-appearance"><summary>Фон, обводка и поворот</summary><div>${color('background','Фон',e.style.background)}<button class="vf-plain" data-free-action="transparent">Убрать фон</button>${color('borderColor','Цвет обводки',e.style.borderColor)}<div class="vf-grid"><label class="vb-field"><span>Обводка, px</span><input type="number" data-free-style="borderWidth" value="${e.style.borderWidth}" min="0" max="20"></label><label class="vb-field"><span>Скругление, px</span><input type="number" data-free-style="radius" value="${e.style.radius}" min="0" max="999"></label><label class="vb-field"><span>Поворот, °</span><input type="number" data-free-style="rotation" value="${e.style.rotation}" min="-180" max="180"></label><label class="vb-field"><span>Непрозрачность, %</span><input type="number" data-free-style="opacity" value="${e.style.opacity}" min="0" max="100"></label></div>${e.type==='image'?choice('objectFit','Картинка',e.style.objectFit,[['cover','Заполнить'],['contain','Вместить целиком']]):''}</div></details>`;
      html+=`<div class="vf-toggles"><label><input type="checkbox" data-free-toggle="locked" ${e.locked?'checked':''}> Закрепить положение</label><label><input type="checkbox" data-free-toggle="hidden" ${box.hidden?'checked':''}> Скрыть на ${mobile?'телефоне':'компьютере'}</label></div><div class="vf-order"><button class="vb-btn" data-free-action="back">На слой ниже</button><button class="vb-btn" data-free-action="front">На слой выше</button></div><div class="vf-order"><button class="vb-btn" data-free-action="duplicate">Копировать</button><button class="vb-btn vb-danger" data-free-action="delete" ${e.locked?'disabled':''}>Удалить</button></div><p class="vb-control-note">Стрелки — сдвиг на 1 px, Shift — на 10. ⌘/Ctrl+D — копия. ⌘/Ctrl+Z — отмена.</p>`;
      return html;
    }
    function toolbar(){return `<div class="vf-floating-tools" role="group" aria-label="Элементы свободного блока">${Object.keys(names).map(k=>`<button data-free-add="${k}"><span>${icons[k]}</span>${names[k]}</button>`).join('')}<button data-free-action="deselect" title="Настроить свободный блок">⚙</button></div>`;}
    function updateSelection(){
      current();if(!doc?.body)return;
      doc.querySelectorAll('.vf-handles,.vf-selection-label').forEach(n=>n.remove());if(readonly)return;
      if(pendingReveal){const pasted=doc.querySelector(`[data-free-id="${pendingReveal}"]`);if(pasted){const section=pasted.closest('[data-block-id]'),scale=Number(frame.dataset.scale||1);api.stage.scrollTop=Math.max(0,(section.offsetTop+pasted.offsetTop)*scale-100);pendingReveal=null;}}
      doc.querySelectorAll('[data-free-id]').forEach(n=>{n.classList.toggle('vf-selected',active()&&n.dataset.freeId===selected);n.classList.toggle('vf-locked',active()&&block().props.elements.find(e=>e.id===n.dataset.freeId)?.locked);});
      const e=item(),el=e&&doc.querySelector(`[data-free-id="${e.id}"]`);if(!el)return;
      const label=doc.createElement('span');label.className='vf-selection-label';label.textContent=`${names[e.type]} · ${e[device()].width} × ${e[device()].height}${e.locked?' · закреплён':''}`;el.append(label);
      if(!e.locked){const handles=doc.createElement('div');handles.className='vf-handles';handles.innerHTML=['nw','n','ne','e','se','s','sw','w'].map(h=>`<span data-free-handle="${h}" class="vf-handle vf-handle-${h}"></span>`).join('');el.append(handles);}
    }
    const EDITOR_CSS=`.bb-free-canvas{outline:1px dashed #147d7325;overflow:clip}.bb-kind-canvas .vb-block-tools{display:none}.vf-floating-bar{position:absolute;top:44px;left:12px;z-index:900}.vf-floating-tools{display:flex;gap:3px;align-items:center;padding:5px;border:1px solid #147d7330;border-radius:8px;background:#fff;box-shadow:0 2px 10px #12233812;font:11px/1.3 system-ui,sans-serif;color:#122338}.vf-floating-tools button{border:0;background:none;border-radius:5px;padding:6px 9px;cursor:pointer;color:inherit;font:inherit}.vf-floating-tools button:hover{background:#147d7310}.vf-floating-tools span{color:#147d73;margin-right:5px}.bb-free-element{cursor:move;touch-action:none;user-select:none;pointer-events:auto!important}.bb-free-element:not(.vf-selected):hover{outline:1px solid #147d7370;outline-offset:1px}.bb-free-element.vf-selected{outline:2px solid #147d73;outline-offset:0}.bb-free-element.vf-locked{cursor:default}.bb-free-copy[contenteditable]{cursor:text;user-select:text;outline:none!important}.vf-handles{position:absolute;inset:0;pointer-events:none}.vf-handle{position:absolute;width:9px;height:9px;border:1.5px solid #147d73;background:#fff;border-radius:2px;pointer-events:auto}.vf-handle-nw{top:-5px;left:-5px;cursor:nwse-resize}.vf-handle-n{top:-5px;left:calc(50% - 4px);cursor:ns-resize}.vf-handle-ne{top:-5px;right:-5px;cursor:nesw-resize}.vf-handle-e{top:calc(50% - 4px);right:-5px;cursor:ew-resize}.vf-handle-se{bottom:-5px;right:-5px;cursor:nwse-resize}.vf-handle-s{bottom:-5px;left:calc(50% - 4px);cursor:ns-resize}.vf-handle-sw{bottom:-5px;left:-5px;cursor:nesw-resize}.vf-handle-w{top:calc(50% - 4px);left:-5px;cursor:ew-resize}.vf-selection-label{position:absolute;bottom:calc(100% + 7px);left:0;background:#147d73;color:#fff;border-radius:4px;padding:4px 6px;white-space:nowrap;font:10px/1.3 system-ui,sans-serif;pointer-events:none;z-index:80}.vf-guide{position:absolute;background:#147d73;pointer-events:none;z-index:999}.vf-guide-x{top:0;bottom:0;width:1px}.vf-guide-y{left:0;right:0;height:1px}.bb-free-copy{pointer-events:none}.bb-free-copy[contenteditable]{pointer-events:auto}.bb-free-canvas[data-free-empty]:after{content:'Добавьте текст, картинку или кнопку в панели справа';position:absolute;top:45%;left:10%;right:10%;text-align:center;font:16px/1.5 system-ui,sans-serif;color:#147d7380;pointer-events:none}.bb-free-element[data-free-d-hidden=true]{display:flex;opacity:.22!important}@media(max-width:650px){.bb-free-element[data-free-d-hidden=true]{display:var(--m-display);opacity:inherit}.bb-free-element[data-free-m-hidden=true]{display:flex;opacity:.22!important}}`;
    function applyBox(el,box){const prefix=device()==='mobile'?'m':'d',w=WIDTH[device()],h=height();for(const[k,v]of [['x',box.x/w*100],['y',box.y/h*100],['w',box.width/w*100],['h',box.height/h*100]])el.style.setProperty(`--${prefix}-${k}`,v+'%');}
    function guides(canvas,lines){canvas.querySelectorAll('.vf-guide').forEach(n=>n.remove());for(const axis of Object.keys(lines)){const n=doc.createElement('div');n.className='vf-guide vf-guide-'+axis;n.style[axis==='x'?'left':'top']=lines[axis]/(axis==='x'?WIDTH[device()]:height())*100+'%';canvas.append(n);}}
    function endGesture(cancel=false){
      if(!gesture)return;const g=gesture;gesture=null;g.canvas.querySelectorAll('.vf-guide').forEach(n=>n.remove());
      if(cancel||!g.moved){applyBox(g.el,g.before);return;}
      mutate(()=>{g.element[g.device]=g.result;},{inspect:true,immediate:true});
    }
    function bind(document,preview){
      doc=document;readonly=preview;if(preview)return;
      const style=doc.createElement('style');style.textContent=EDITOR_CSS;doc.head.append(style);
      doc.querySelectorAll('.bb-free-canvas').forEach(c=>{if(!c.querySelector('[data-free-id]'))c.dataset.freeEmpty='true';});
      doc.addEventListener('pointerdown',event=>{
        const canvas=event.target.closest('.bb-free-canvas');if(!canvas)return;
        const rect=canvas.getBoundingClientRect(),ratio=WIDTH[device()]/rect.width;
        lastPoint={block:canvas.closest('[data-block-id]').dataset.blockId,device:device(),x:Math.round((event.clientX-rect.left)*ratio),y:Math.round((event.clientY-rect.top)*ratio)};
      },true);
      doc.addEventListener('pointerdown',event=>{
        if(event.button!==0||event.target.closest('[contenteditable],.vb-block-tools'))return;
        const el=event.target.closest('[data-free-id]');if(!el)return;
        const bid=el.closest('[data-block-id]').dataset.blockId;if(block()?.id!==bid)api.selectBlock(bid);
        const e=block().props.elements.find(e=>e.id===el.dataset.freeId);selected=e.id;owner=bid;api.refresh();
        if(e.locked)return;
        event.preventDefault();const canvas=el.closest('.bb-free-canvas'),rect=canvas.getBoundingClientRect();
        gesture={element:e,el,canvas,device:device(),before:clone(e[device()]),result:clone(e[device()]),startX:event.clientX,startY:event.clientY,ratio:WIDTH[device()]/rect.width,handle:event.target.dataset.freeHandle||'',moved:false};
        el.setPointerCapture(event.pointerId);
      });
      doc.addEventListener('pointermove',event=>{
        if(!gesture)return;const g=gesture,dx=(event.clientX-g.startX)*g.ratio,dy=(event.clientY-g.startY)*g.ratio;
        if(Math.abs(dx)+Math.abs(dy)<2&&!g.moved)return;g.moved=true;
        if(g.handle){g.result=Geometry.resize(g.before,dx,dy,g.handle,WIDTH[g.device],height(),g.element.style.rotation);}
        else{g.result=Geometry.move(g.before,dx,dy,WIDTH[g.device],height());const targets=block().props.elements.filter(e=>e!==g.element&&!e[g.device].hidden).map(e=>e[g.device]);const snapped=event.altKey?{box:g.result,guides:{}}:Geometry.snap(g.result,targets,WIDTH[g.device],height(),6*g.ratio);g.result=snapped.box;guides(g.canvas,snapped.guides);}
        applyBox(g.el,g.result);for(const key of ['x','y','width','height']){const input=inspector.querySelector(`[data-free-box="${key}"]`);if(input)input.value=g.result[key];}
        const label=g.el.querySelector('.vf-selection-label');if(label)label.textContent=`${g.result.width} × ${g.result.height} · ${g.result.x}, ${g.result.y}`;
      });
      doc.addEventListener('pointerup',()=>endGesture());doc.addEventListener('pointercancel',()=>endGesture(true));
      doc.addEventListener('dblclick',event=>{
        const el=event.target.closest('[data-free-id]'),e=el&&block()?.props.elements?.find(e=>e.id===el.dataset.freeId);if(!e||!['text','button'].includes(e.type))return;
        event.preventDefault();const text=el.querySelector('.bb-free-copy');text.setAttribute('contenteditable','plaintext-only');text.setAttribute('spellcheck','false');text.focus();
        text.oninput=()=>{mutate(()=>{e.content.text=text.innerText.slice(0,5000);},{group:'free-text:'+e.id});const field=inspector.querySelector('[data-free-content=text]');if(field)field.value=e.content.text;};
        text.addEventListener('blur',()=>{text.removeAttribute('contenteditable');api.scheduleRender(0);},{once:true});
      });
    }
    function total(){return api.pages().reduce((n,p)=>n+p.blocks.reduce((n,b)=>n+(b.props.elements?.length||0),0),0);}
    function add(type){
      if(!active())return;if(block().props.elements.length>=60){toast('В блоке уже 60 элементов. Добавьте ещё один свободный блок.');return;}
      if(total()>=250){toast('На сайте уже 250 свободных элементов.');return;}
      const e=newElement(type),n=block().props.elements.length;
      for(const d of ['desktop','mobile']){const h=block().props[d==='mobile'?'mobileHeight':'height'];e[d].y=Math.min(60+(n%8)*24,Math.max(0,h-e[d].height));e[d].height=Math.min(e[d].height,h);}
      mutate(()=>{block().props.elements.push(e);selected=e.id;owner=block().id;},{inspect:true,immediate:true});
      if(type==='image')api.openMedia({block:block().id,element:e.id});
    }
    function pastePoint(){
      if(!active())return null;
      if(lastPoint?.block===block().id&&lastPoint.device===device())return {...lastPoint};
      const selected=item();return {block:block().id,device:device(),x:selected?selected[device()].x+24:48,y:selected?selected[device()].y+24:64};
    }
    function insertClipboard(b,plans,target,commit){
      const props=clone(b.props),elements=[];
      for(const [index,plan]of plans.entries()){
        const e=newElement(plan.type);e.content.text=plan.text||'';
        if(plan.type==='image'){e.content.image=plan.asset.id;e.content.alt=plan.asset.name||'Изображение из буфера';}
        if(plan.type==='button')e.content.link=plan.url;
        if(plan.type==='text')e.style.color=api.theme().ink;
        for(const d of ['desktop','mobile']){
          const key=d==='mobile'?'mobileHeight':'height',limit=d==='mobile'?6000:4000;
          const point=target.point?.block===b.id&&target.point.device===d?{x:target.point.x+index*24,y:target.point.y+index*24}:{x:d==='mobile'?24:48,y:props.elements.length?Math.max(...props.elements.map(e=>e[d].y+e[d].height))+24:48};
          e[d]=Geometry.pasteBox(plan.type,plan.text||'',plan.asset,WIDTH[d],limit,point);
          props[key]=Math.max(props[key],e[d].y+e[d].height+24);
        }
        props.elements.push(e);elements.push(e);
      }
      mutate(()=>{b.props=props;commit();if(api.block()?.id===b.id){selected=elements.at(-1).id;owner=b.id;pendingReveal=selected;lastPoint=null;}},{inspect:true,immediate:true});
    }
    function stackMobile(b){
      const layout=Geometry.mobileLayout(b.props.elements);if(!layout)return false;
      b.props.mobileHeight=layout.height;b.props.elements.forEach(e=>e.mobile=layout.boxes[e.id]);return true;
    }
    function action(name){
      const e=item(),b=block();if(!active())return false;
      if(name==='deselect'){selected=null;api.refresh();return true;}
      if(name==='image'&&e){api.openMedia({block:b.id,element:e.id});return true;}
      if(name==='mobile-stack'){let built=false;mutate(()=>{built=stackMobile(b);},{inspect:true,immediate:true});toast(built?'Версия для телефона собрана. При необходимости поправьте элементы.':'Блок слишком длинный для телефона. Разделите его на несколько блоков.');return true;}
      if(!e)return true;
      if(name==='delete'&&e.locked){toast('Сначала снимите закрепление элемента.');return true;}
      if(name==='duplicate'&&(b.props.elements.length>=60||total()>=250)){toast('В блоке уже 60 элементов.');return true;}
      mutate(()=>{
        const list=b.props.elements,index=list.indexOf(e);
        if(name==='delete'){list.splice(index,1);selected=null;}
        if(name==='duplicate'){const copy=clone(e);copy.id=uid();copy.locked=false;for(const d of ['desktop','mobile'])copy[d]=Geometry.move(copy[d],16,16,WIDTH[d],b.props[d==='mobile'?'mobileHeight':'height']);list.splice(index+1,0,copy);selected=copy.id;}
        if(name==='front'&&index<list.length-1)[list[index],list[index+1]]=[list[index+1],list[index]];
        if(name==='back'&&index>0)[list[index],list[index-1]]=[list[index-1],list[index]];
        if(name==='transparent')e.style.background='transparent';
      },{inspect:true,immediate:true});return true;
    }
    function align(value){const e=item();if(!e||e.locked)return;mutate(()=>{const box=e[device()],w=WIDTH[device()],h=height();if(value==='left')box.x=0;if(value==='center')box.x=Math.round((w-box.width)/2);if(value==='right')box.x=w-box.width;if(value==='top')box.y=0;if(value==='middle')box.y=Math.round((h-box.height)/2);if(value==='bottom')box.y=h-box.height;},{inspect:true});}
    function input(el){
      const d=el.dataset,e=item();if(!active())return;
      if(d.freeHeight!==undefined){const max=device()==='mobile'?6000:4000,n=Number(el.value);if(!el.value||!Number.isInteger(n)||n<160||n>max)return;mutate(()=>{block().props[device()==='mobile'?'mobileHeight':'height']=n;block().props.elements.forEach(i=>{i[device()].height=Math.min(i[device()].height,n);i[device()].y=Math.min(i[device()].y,n-i[device()].height);});},{group:'free-height'});}
      if(!e)return;
      if(d.freeContent)mutate(()=>e.content[d.freeContent]=el.value,{group:'free-content:'+e.id+':'+d.freeContent});
      if(d.freeToggle){mutate(()=>{if(d.freeToggle==='locked')e.locked=el.checked;else e[device()].hidden=el.checked;},{inspect:d.freeToggle==='locked'});}
      if(d.freeBox&&(!e.locked||d.freeBox==='fontSize')){const n=Number(el.value);if(!el.value||!Number.isInteger(n)||!el.validity.valid)return;const box=e[device()],key=d.freeBox,w=WIDTH[device()],h=height();let value=n;
        if(key==='width')value=clamp(n,16,w-box.x);if(key==='height')value=clamp(n,4,h-box.y);if(key==='x')value=clamp(n,0,w-box.width);if(key==='y')value=clamp(n,0,h-box.height);if(key==='fontSize')value=clamp(n,8,240);
        mutate(()=>box[key]=value,{group:'free-box:'+e.id+':'+key});if(value!==n)el.value=value;
      }
      const key=d.freeStyle||d.freeColor;if(key){let value=el.type==='number'?Number(el.value):el.value;if(el.type==='number'&&(!el.value||!Number.isInteger(value)||!el.validity.valid))return;if(d.freeColor&&!/^(#[0-9a-fA-F]{6}|transparent)$/.test(value))return;mutate(()=>e.style[key]=value,{group:'free-style:'+e.id+':'+key});const twin=inspector.querySelector(d.freeColor?`[data-free-style="${key}"]`:`[data-free-color="${key}"]`);if(twin&&value!=='transparent')twin.value=value;}
    }
    function keydown(event){
      if(event.target.closest?.('input,textarea,select,[contenteditable]')||!item())return false;
      const e=item(),step=event.shiftKey?10:1;
      if(event.key==='Escape'){endGesture(true);selected=null;api.refresh();return true;}
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='d'){event.preventDefault();action('duplicate');return true;}
      if(['Delete','Backspace'].includes(event.key)){event.preventDefault();action('delete');return true;}
      if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();if(!e.locked)mutate(()=>{e[device()]=Geometry.move(e[device()],event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0,event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0,WIDTH[device()],height());},{group:'free-nudge:'+e.id});return true;}return false;
    }
    function assignImage(target,id){if(!target?.element)return false;const b=api.pages().flatMap(p=>p.blocks).find(b=>b.id===target.block),e=b?.props.elements.find(e=>e.id===target.element);if(e){mutate(()=>{e.content.image=id;e.content.alt=e.content.alt||config.assets.find(a=>a.id===id)?.name||'';},{inspect:true,immediate:true});}return true;}
    function convert(){
      const b=block();if(!b||b.type==='canvas')return;if(device()!=='desktop'){toast('Для переноса блока сначала откройте вид «Компьютер».');return;}
      const section=frame.contentDocument?.querySelector(`[data-block-id="${b.id}"]`);if(!section)return;
      const bounds=section.getBoundingClientRect(),ratio=1120/bounds.width,elements=[];
      const nodes=[...section.querySelectorAll('h1,h2,h3,p,.bb-brand,.bb-button,.bb-nav-links a,.bb-footer-links a,.bb-photo,.bb-art')].filter(n=>!n.closest('.vb-block-tools'));
      for(const node of nodes.slice(0,60)){
        const rect=node.getBoundingClientRect();if(rect.width<1||rect.height<1)continue;
        const css=frame.contentWindow.getComputedStyle(node),kind=node.matches('img')?'image':node.matches('.bb-button,a')?'button':node.matches('.bb-art')?'art':'text',e=newElement(kind);
        const hex=(v,fallback)=>{const m=v.match(/rgba?\((\d+)[, ]+(\d+)[, ]+(\d+)/);return m?'#'+m.slice(1,4).map(n=>Number(n).toString(16).padStart(2,'0')).join(''):fallback;};
        const w=Math.min(1120,Math.round(rect.width*ratio)),h=Math.min(4000,Math.max(8,Math.round(rect.height*ratio)));
        e.desktop={x:Math.max(0,Math.min(1120-w,Math.round((rect.left-bounds.left)*ratio))),y:Math.max(0,Math.round((rect.top-bounds.top)*ratio)),width:Math.max(16,w),height:h,fontSize:clamp(Math.round(parseFloat(css.fontSize)*ratio),8,240),hidden:false};
        e.content.text=node.innerText.replace(/↗\s*$/,'').trim();e.content.link=node.getAttribute('href')||'#';
        if(kind==='image'){const asset=config.assets.find(a=>node.getAttribute('src')===a.url);if(asset)e.content.image=asset.id;e.content.alt=node.getAttribute('alt')||'';}
        Object.assign(e.style,{color:hex(css.color,'#122338'),background:css.backgroundColor==='rgba(0, 0, 0, 0)'?'transparent':hex(css.backgroundColor,'transparent'),radius:Math.min(999,Math.round(parseFloat(css.borderRadius)||0)),fontWeight:['400','500','600','700','800'].includes(css.fontWeight)?css.fontWeight:'500',textAlign:['left','center','right'].includes(css.textAlign)?css.textAlign:'left'});
        if(kind==='art')e.style.background='transparent';elements.push(e);
      }
      if(!elements.length){toast('В этом блоке нет элементов для переноса. Добавьте свободный блок.');return;}
      if(total()+elements.length>250){toast('На сайте может быть до 250 свободных элементов.');return;}
      const h=Math.min(4000,Math.max(160,Math.ceil(bounds.height*ratio),...elements.map(e=>e.desktop.y+e.desktop.height)));
      for(const e of elements){e.desktop.height=Math.min(e.desktop.height,h);e.desktop.y=Math.min(e.desktop.y,h-e.desktop.height);}
      mutate(()=>{b.type='canvas';b.props={height:h,mobileHeight:740,elements};stackMobile(b);selected=elements[0].id;owner=b.id;},{inspect:true,immediate:true});toast('Блок переведён в свободный режим. ⌘/Ctrl+Z — отменить.');
    }
    return {active,current,item,layers,select,inspectorHTML,toolbar,updateSelection,bind,add,action,align,input,keydown,assignImage,convert,pastePoint,insertClipboard,clear(){selected=null;owner=null;endGesture(true);},cancel(){endGesture(true);}};
  };
})();

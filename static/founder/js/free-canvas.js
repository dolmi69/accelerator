/* Free layout: coordinates belong to the document; editor handles never reach a release. */
(() => {
  'use strict';
  const WIDTH={desktop:1120,mobile:390};
  const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  const Geometry={
    union(boxes){const x=Math.min(...boxes.map(b=>b.x)),y=Math.min(...boxes.map(b=>b.y));return {x,y,width:Math.max(...boxes.map(b=>b.x+b.width))-x,height:Math.max(...boxes.map(b=>b.y+b.height))-y};},
    intersects(a,b){return a.x<=b.x+b.width&&a.x+a.width>=b.x&&a.y<=b.y+b.height&&a.y+a.height>=b.y;},
    moveGroup(records,dx,dy){
      dx=Math.round(clamp(dx,Math.max(...records.map(r=>-r.box.x)),Math.min(...records.map(r=>r.width-r.box.x-r.box.width))));
      dy=Math.round(clamp(dy,Math.max(...records.map(r=>-r.box.y)),Math.min(...records.map(r=>r.height-r.box.y-r.box.height))));
      return records.map(r=>({...r.box,x:r.box.x+dx,y:r.box.y+dy}));
    },
    resizeGroup(records,dx,dy,handle){
      const bounds=this.union(records.map(r=>({...r.box,y:r.box.y+(r.offsetY||0)}))),next=this.resize(bounds,dx,dy,handle,1120,100000);
      const sx=next.width/bounds.width,sy=next.height/bounds.height;
      const boxes=records.map(r=>({...r.box,x:Math.round(next.x+(r.box.x-bounds.x)*sx),y:Math.round(next.y+(r.box.y+(r.offsetY||0)-bounds.y)*sy-(r.offsetY||0)),width:Math.max(8,Math.round(r.box.width*sx)),height:Math.max(4,Math.round(r.box.height*sy)),fontSize:clamp(Math.round(r.box.fontSize*Math.min(sx,sy)),8,240)}));
      return boxes.every((b,i)=>b.x>=0&&b.y>=0&&b.x+b.width<=records[i].width&&b.y+b.height<=records[i].height)?boxes:null;
    },
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
    const names={text:'Текст',image:'Изображение',button:'Кнопка',shape:'Фигура',line:'Линия',art:'Иллюстрация',faq:'Вопрос и ответ'};
    const icons={text:'T',image:'▧',button:'↗',shape:'□',line:'—',art:'✳',faq:'?'};
    let selected=null,selectedIds=new Set(),owner=null,gesture=null,doc=null,readonly=false,lastPoint=null,pendingReveal=null;
    const block=()=>api.block(),device=()=>api.device();
    const active=()=>block()?.type==='canvas';
    const item=()=>active()?block().props.elements.find(e=>e.id===selected):null;
    const currentPage=()=>api.page?.()||api.pages().find(p=>p.blocks.includes(block()));
    const members=()=>currentPage()?.blocks.filter(b=>b.type==='canvas').flatMap(b=>b.props.elements.filter(e=>selectedIds.has(e.id)).map(e=>({b,e})))||[];
    function choose(ids){selectedIds=new Set(ids);selected=ids.at(-1)||null;const ref=members().find(r=>r.e.id===selected);owner=ref?.b.id||block()?.id;if(ref&&block()?.id!==ref.b.id)api.selectBlock(ref.b.id);}
    const height=()=>block().props[device()==='mobile'?'mobileHeight':'height'];
    const baseStyle={color:'#122338',background:'transparent',borderColor:'#147d73',borderWidth:0,radius:0,fontWeight:'500',fontFamily:'inherit',textAlign:'left',opacity:100,rotation:0,objectFit:'cover'};
    function current(){if(owner!==block()?.id){owner=block()?.id;selected=null;selectedIds.clear();}const valid=new Set(members().map(r=>r.e.id));selectedIds=new Set([...selectedIds].filter(id=>valid.has(id)));if(selected&&!selectedIds.has(selected))selected=[...selectedIds].at(-1)||null;}
    function newElement(type){
      const sizes={text:[450,120,48],image:[340,280,20],button:[220,56,16],shape:[260,200,20],line:[320,4,20],art:[340,340,20],faq:[560,180,16]},[w,h,fs]=sizes[type];
      const style={...baseStyle};if(['button','shape','line'].includes(type))style.background=documentAccent();
      if(type==='button')Object.assign(style,{color:'#ffffff',radius:8,textAlign:'center',fontWeight:'600'});
      if(type==='shape')style.radius=20;
      return {id:uid(),type,content:{text:type==='text'?'Ваш текст':type==='button'?'Открыть':type==='faq'?'Ваш вопрос':'',image:'',alt:type==='faq'?'Напишите ответ на вопрос.':'',link:'#'},desktop:{x:80,y:80,width:w,height:h,fontSize:fs,hidden:false},mobile:{x:30,y:60,width:Math.min(w,330),height:h,fontSize:Math.min(fs,34),hidden:false},style,locked:false};
    }
    function documentAccent(){return api.theme().accent;}
    function layers(b){
      return `<div class="vf-layers" aria-label="Слои свободного блока">${[...b.props.elements].reverse().map(e=>`<button class="vf-layer ${selectedIds.has(e.id)?'active':''}" data-free-select="${e.id}" data-free-block="${b.id}" title="${esc(names[e.type])}" aria-label="Слой: ${esc((e.content.text||e.content.alt||names[e.type]).slice(0,70))}"><span>${icons[e.type]}</span><span>${esc((e.content.text||e.content.alt||names[e.type]).slice(0,42))}</span><small>${e.locked?'▣':e[device()].hidden?'◌':''}</small></button>`).join('')||'<p>Добавьте первый элемент</p>'}</div>`;
    }
    function select(id,{toggle=false}={}){const ids=toggle?new Set(selectedIds):new Set();if(toggle&&ids.has(id))ids.delete(id);else ids.add(id);choose([...ids]);api.refresh();}
    function boxInput(key,label,value,options=''){return `<label class="vb-field"><span>${label}</span><input data-free-box="${key}" type="number" value="${value}" step="1" ${options} ${item()?.locked&&key!=='fontSize'?'disabled':''}></label>`;}
    function choice(key,label,value,choices){return `<label class="vb-field"><span>${label}</span><select data-free-style="${key}">${choices.map(([v,l])=>`<option value="${v}" ${v===value?'selected':''}>${l}</option>`).join('')}</select></label>`;}
    function color(key,label,value){return `<label class="vb-field"><span>${label}</span><div class="vb-color-row"><input type="color" data-free-style="${key}" value="${value==='transparent'?'#ffffff':value}" aria-label="${label}"><input data-free-color="${key}" value="${value}" aria-label="${label} HEX"></div></label>`;}
    function records(){return members().map(({b,e})=>{const el=doc?.querySelector(`[data-free-id="${e.id}"]`),canvas=el?.closest('.bb-free-canvas');return {b,e,el,box:clone(e[device()]),width:WIDTH[device()],height:b.props[device()==='mobile'?'mobileHeight':'height'],offsetY:canvas?.getBoundingClientRect().top||0};});}
    function groupBounds(list=records()){return Geometry.union(list.map(r=>({...r.box,y:r.box.y+r.offsetY})));}
    function groupInspector(){
      const list=members(),texts=list.filter(r=>['text','button','faq'].includes(r.e.type)),bounds=groupBounds();
      const common=(items,get)=>{const values=items.map(get);return values.every(v=>v===values[0])?values[0]:'';};
      const mixedColor=(key,label)=>{const v=common(list,r=>r.e.style[key]);return `<label class="vb-field"><span>${label}</span><div class="vb-color-row"><input type="color" data-free-style="${key}" value="${v&&v!=='transparent'?v:'#122338'}" aria-label="${label}"><input data-free-color="${key}" value="${esc(v)}" placeholder="Разные значения" aria-label="${label} HEX"></div></label>`;};
      let html=`<div class="vf-element-heading"><h2>Выбрано элементов: ${list.length}</h2><button class="vb-icon" data-free-action="deselect" aria-label="Снять выделение элементов">×</button></div><p class="vb-control-note">Перетаскивайте выделенные элементы вместе. Shift+клик добавляет или убирает элемент. Оформление ниже применяется ко всему выделению.</p>`;
      html+=`<p class="vf-device-note">Общее положение и размер · ${device()==='mobile'?'телефон':'компьютер'}</p><div class="vf-grid">${boxInput('x','Слева, px',bounds.x,'min="0"')}${boxInput('y','Сверху, px',bounds.y,'min="0"')}${boxInput('width','Ширина группы, px',bounds.width,'min="16"')}${boxInput('height','Высота группы, px',bounds.height,'min="8"')}</div>`;
      if(texts.length){
        html+=boxInput('fontSize','Размер текста у выбранных, px',common(texts,r=>r.e[device()].fontSize),'min="8" max="240"')+mixedColor('color','Цвет текста')+choice('fontFamily','Шрифт',common(texts,r=>r.e.style.fontFamily),[['','Разные значения'],['inherit','Шрифт сайта'],['modern','Современный'],['serif','С засечками'],['mono','Моноширинный']])+choice('fontWeight','Насыщенность',common(texts,r=>r.e.style.fontWeight),[['','Разные значения'],['400','Обычный'],['500','Средний'],['600','Полужирный'],['700','Жирный'],['800','Очень жирный']]);
        html+=`<details class="vf-appearance" open><summary>Тексты выбранных элементов</summary><div>${texts.map((r,i)=>`<label class="vb-field"><span>Текст ${i+1}</span><textarea aria-label="Текст ${i+1}" data-free-member="${r.e.id}" data-free-content="text" maxlength="5000" rows="2">${esc(r.e.content.text)}</textarea></label>`).join('')}</div></details>`;
      }
      html+=`<details class="vf-appearance"><summary>Общее оформление</summary><div>${mixedColor('background','Фон')}<button class="vf-plain" data-free-action="transparent">Убрать фон у выбранных</button>${mixedColor('borderColor','Цвет обводки')}<div class="vf-grid">${[['borderWidth','Обводка, px',0,20],['radius','Скругление, px',0,999],['opacity','Непрозрачность, %',0,100],['rotation','Поворот, °',-180,180]].map(([key,label,min,max])=>`<label class="vb-field"><span>${label}</span><input type="number" data-free-style="${key}" value="${common(list,r=>r.e.style[key])}" placeholder="Разные" min="${min}" max="${max}"></label>`).join('')}</div></div></details>`;
      html+=`<div class="vf-toggles"><label><input type="checkbox" data-free-toggle="locked" ${list.every(r=>r.e.locked)?'checked':''}> Закрепить все</label><label><input type="checkbox" data-free-toggle="hidden" ${list.every(r=>r.e[device()].hidden)?'checked':''}> Скрыть все на этом экране</label></div><div class="vf-order"><button class="vb-btn" data-free-action="back">На задний план</button><button class="vb-btn" data-free-action="front">На передний план</button></div><div class="vf-order"><button class="vb-btn" data-free-action="duplicate">Копировать выбранные</button><button class="vb-btn vb-danger" data-free-action="delete">Удалить выбранные</button></div>`;
      return html;
    }
    function inspectorHTML(){
      current();const e=item(),b=block(),mobile=device()==='mobile';
      let html=`<div class="vf-heading"><span class="vf-badge">${esc(config.catalog?.[b.props.template]?.label||'Свободный блок')}</span><strong>${mobile?'Телефон · 390 px':'Компьютер · 1120 px'}</strong></div>`;
      html+=`<div class="vf-add-tools" role="group" aria-label="Добавить свободный элемент">${Object.keys(names).map(k=>`<button data-free-add="${k}" title="Добавить: ${names[k]}"><span>${icons[k]}</span>${names[k]}</button>`).join('')}</div>`;
      html+=`<p class="vb-control-note">Выделяйте рамкой или Shift+кликом. Перетащите элемент. Потяните за угол, чтобы изменить размер. Двойной клик по тексту — редактировать. Ctrl/⌘V вставит текст, картинку или ссылку.</p>`;
      if(members().length>1)return html+groupInspector();
      if(!e){
        html+=`<label class="vb-field"><span>Высота блока на ${mobile?'телефоне':'компьютере'}, px</span><input type="number" min="40" max="${mobile?6000:4000}" data-free-height value="${height()}"></label><button class="vb-btn" data-free-action="mobile-stack">Собрать версию для телефона</button><p class="vb-control-note">Тексты, картинки и кнопки встанут друг под другом. Оформление компьютера сохранится.</p>`;
        html+=`<div class="vb-separator"></div><div class="vf-order"><button class="vb-btn" data-action="block-up">Блок выше</button><button class="vb-btn" data-action="block-down">Блок ниже</button></div><button class="vb-btn" data-action="block-duplicate">Копировать блок</button><button class="vb-btn vb-danger" data-action="block-delete">Удалить блок</button>`;
        return html;
      }
      const box=e[device()];
      html+=`<div class="vf-element-heading"><h2>${names[e.type]}</h2><button data-free-action="deselect" class="vb-icon" aria-label="Настройки свободного блока">×</button></div>`;
      if(['text','button','faq','art'].includes(e.type))html+=`<label class="vb-field"><span>Текст</span><textarea data-free-content="text" maxlength="5000" rows="3">${esc(e.content.text)}</textarea></label>`;
      if(e.type==='faq')html+=`<label class="vb-field"><span>Ответ</span><textarea data-free-content="alt" maxlength="5000" rows="4">${esc(e.content.alt)}</textarea></label>`;
      if(e.type==='button')html+=`<label class="vb-field"><span>Ссылка</span><input data-free-content="link" value="${esc(e.content.link)}" placeholder="/about/ или https://…" maxlength="1000"></label>`;
      if(e.type==='image'){
        const asset=config.assets.find(a=>a.id===e.content.image);
        html+=`<div class="vf-image-control">${asset?`<img src="${esc(asset.url)}" alt="">`:''}<button class="vb-btn" data-free-action="image">${asset?'Заменить изображение':'Выбрать изображение'}</button></div><label class="vb-field"><span>Описание картинки</span><input data-free-content="alt" value="${esc(e.content.alt)}" maxlength="300"></label>`;
      }
      html+=`<p class="vf-device-note">Положение и размер · ${mobile?'только на телефоне':'только на компьютере'}</p><div class="vf-grid">${boxInput('x','Слева, px',box.x,'min="0"')}${boxInput('y','Сверху, px',box.y,'min="0"')}${boxInput('width','Ширина, px',box.width,'min="16"')}${boxInput('height','Высота, px',box.height,'min="4"')}</div>`;
      html+=`<div class="vf-align" role="group" aria-label="Выравнивание элемента"><button data-free-align="left" title="К левому краю" aria-label="К левому краю">╞</button><button data-free-align="center" title="По центру по горизонтали" aria-label="По центру по горизонтали">↔</button><button data-free-align="right" title="К правому краю" aria-label="К правому краю">╡</button><button data-free-align="top" title="К верхнему краю" aria-label="К верхнему краю">⊤</button><button data-free-align="middle" title="По центру по вертикали" aria-label="По центру по вертикали">↕</button><button data-free-align="bottom" title="К нижнему краю" aria-label="К нижнему краю">⊥</button></div>`;
      if(['text','button','faq'].includes(e.type))html+=boxInput('fontSize','Размер текста, px',box.fontSize,'min="8" max="240"')+color('color','Цвет текста',e.style.color)+choice('fontFamily','Шрифт',e.style.fontFamily,[['inherit','Шрифт сайта'],['modern','Современный'],['serif','С засечками'],['mono','Моноширинный']])+choice('fontWeight','Насыщенность',e.style.fontWeight,[['400','Обычный'],['500','Средний'],['600','Полужирный'],['700','Жирный'],['800','Очень жирный']])+choice('textAlign','Текст',e.style.textAlign,[['left','Слева'],['center','По центру'],['right','Справа']]);
      html+=`<details class="vf-appearance"><summary>Фон, обводка и поворот</summary><div>${color('background','Фон',e.style.background)}<button class="vf-plain" data-free-action="transparent">Убрать фон</button>${color('borderColor','Цвет обводки',e.style.borderColor)}<div class="vf-grid"><label class="vb-field"><span>Обводка, px</span><input type="number" data-free-style="borderWidth" value="${e.style.borderWidth}" min="0" max="20"></label><label class="vb-field"><span>Скругление, px</span><input type="number" data-free-style="radius" value="${e.style.radius}" min="0" max="999"></label><label class="vb-field"><span>Поворот, °</span><input type="number" data-free-style="rotation" value="${e.style.rotation}" min="-180" max="180"></label><label class="vb-field"><span>Непрозрачность, %</span><input type="number" data-free-style="opacity" value="${e.style.opacity}" min="0" max="100"></label></div>${e.type==='image'?choice('objectFit','Картинка',e.style.objectFit,[['cover','Заполнить'],['contain','Вместить целиком']]):''}</div></details>`;
      html+=`<div class="vf-toggles"><label><input type="checkbox" data-free-toggle="locked" ${e.locked?'checked':''}> Закрепить положение</label><label><input type="checkbox" data-free-toggle="hidden" ${box.hidden?'checked':''}> Скрыть на ${mobile?'телефоне':'компьютере'}</label></div><div class="vf-order"><button class="vb-btn" data-free-action="back">На слой ниже</button><button class="vb-btn" data-free-action="front">На слой выше</button></div><div class="vf-order"><button class="vb-btn" data-free-action="duplicate">Копировать</button><button class="vb-btn vb-danger" data-free-action="delete" ${e.locked?'disabled':''}>Удалить</button></div><p class="vb-control-note">Стрелки — сдвиг на 1 px, Shift — на 10. ⌘/Ctrl+D — копия. ⌘/Ctrl+Z — отмена.</p>`;
      return html;
    }
    function toolbar(){return `<div class="vf-floating-tools" role="group" aria-label="Элементы свободного блока">${Object.keys(names).map(k=>`<button data-free-add="${k}"><span>${icons[k]}</span>${names[k]}</button>`).join('')}<button data-free-action="deselect" title="Настроить свободный блок">⚙</button></div>`;}
    function updateSelection(){
      current();if(!doc?.body)return;
      doc.querySelectorAll('.vf-handles,.vf-selection-label,.vf-group-box').forEach(n=>n.remove());if(readonly)return;
      if(pendingReveal){const pasted=doc.querySelector(`[data-free-id="${pendingReveal}"]`);if(pasted){const section=pasted.closest('[data-block-id]'),scale=Number(frame.dataset.scale||1);api.stage.scrollTop=Math.max(0,(section.offsetTop+pasted.offsetTop)*scale-100);pendingReveal=null;}}
      const list=members();
      doc.querySelectorAll('[data-free-id]').forEach(n=>{n.classList.toggle('vf-selected',selectedIds.has(n.dataset.freeId));n.classList.toggle('vf-locked',list.find(r=>r.e.id===n.dataset.freeId)?.e.locked||!!currentPage()?.blocks.flatMap(b=>b.props.elements||[]).find(e=>e.id===n.dataset.freeId)?.locked);});
      if(!list.length)return;
      let el,labelText,locked=list.some(r=>r.e.locked);
      if(list.length>1){
        const bounds=groupBounds();el=doc.createElement('div');el.className='vf-group-box';el.style.cssText=`left:${bounds.x}px;top:${bounds.y}px;width:${bounds.width}px;height:${bounds.height}px`;doc.body.append(el);labelText=`Выбрано: ${list.length}`;
      }else{const e=list[0].e;el=doc.querySelector(`[data-free-id="${e.id}"]`);if(!el)return;labelText=`${names[e.type]} · ${e[device()].width} × ${e[device()].height}${e.locked?' · закреплён':''}`;}
      const label=doc.createElement('span');label.className='vf-selection-label';label.textContent=labelText;el.append(label);
      if(!locked){const handles=doc.createElement('div');handles.className='vf-handles';handles.innerHTML=['nw','n','ne','e','se','s','sw','w'].map(h=>`<span data-free-handle="${h}" class="vf-handle vf-handle-${h}"></span>`).join('');el.append(handles);}
    }
    const EDITOR_CSS=`body{position:relative}.vf-group-box{position:absolute;border:2px solid #147d73;pointer-events:none;z-index:10000;box-sizing:border-box}.vf-marquee{position:absolute;border:1px solid #147d73;background:#147d7318;pointer-events:none;z-index:10000;box-sizing:border-box}.bb-free-canvas{outline:1px dashed #147d7325;overflow:clip}.bb-kind-canvas .vb-block-tools{top:4px;opacity:.85}.bb-kind-canvas .vb-block-tools:hover{opacity:1}.vf-floating-bar{position:absolute;top:44px;left:12px;z-index:900}.vf-floating-tools{display:flex;gap:3px;align-items:center;padding:5px;border:1px solid #147d7330;border-radius:8px;background:#fff;box-shadow:0 2px 10px #12233812;font:11px/1.3 system-ui,sans-serif;color:#122338}.vf-floating-tools button{border:0;background:none;border-radius:5px;padding:6px 9px;cursor:pointer;color:inherit;font:inherit}.vf-floating-tools button:hover{background:#147d7310}.vf-floating-tools span{color:#147d73;margin-right:5px}.bb-free-element{cursor:move;touch-action:none;user-select:none;pointer-events:auto!important}.bb-free-element:not(.vf-selected):hover{outline:1px solid #147d7370;outline-offset:1px}.bb-free-element.vf-selected{outline:2px solid #147d73;outline-offset:0}.bb-free-element.vf-locked{cursor:default}.bb-free-copy[contenteditable]{cursor:text;user-select:text;outline:none!important}.vf-handles{position:absolute;inset:0;pointer-events:none}.vf-handle{position:absolute;width:9px;height:9px;border:1.5px solid #147d73;background:#fff;border-radius:2px;pointer-events:auto}.vf-handle-nw{top:-5px;left:-5px;cursor:nwse-resize}.vf-handle-n{top:-5px;left:calc(50% - 4px);cursor:ns-resize}.vf-handle-ne{top:-5px;right:-5px;cursor:nesw-resize}.vf-handle-e{top:calc(50% - 4px);right:-5px;cursor:ew-resize}.vf-handle-se{bottom:-5px;right:-5px;cursor:nwse-resize}.vf-handle-s{bottom:-5px;left:calc(50% - 4px);cursor:ns-resize}.vf-handle-sw{bottom:-5px;left:-5px;cursor:nesw-resize}.vf-handle-w{top:calc(50% - 4px);left:-5px;cursor:ew-resize}.vf-selection-label{position:absolute;bottom:calc(100% + 7px);left:0;background:#147d73;color:#fff;border-radius:4px;padding:4px 6px;white-space:nowrap;font:10px/1.3 system-ui,sans-serif;pointer-events:none;z-index:80}.vf-guide{position:absolute;background:#147d73;pointer-events:none;z-index:999}.vf-guide-x{top:0;bottom:0;width:1px}.vf-guide-y{left:0;right:0;height:1px}.bb-free-copy{pointer-events:none}.bb-free-copy[contenteditable]{pointer-events:auto}.bb-free-canvas[data-free-empty]:after{content:'Добавьте текст, картинку или кнопку в панели справа';position:absolute;top:45%;left:10%;right:10%;text-align:center;font:16px/1.5 system-ui,sans-serif;color:#147d7380;pointer-events:none}.bb-free-element[data-free-d-hidden=true]{display:flex;opacity:.22!important}@media(max-width:650px){.bb-free-element[data-free-d-hidden=true]{display:var(--m-display);opacity:inherit}.bb-free-element[data-free-m-hidden=true]{display:flex;opacity:.22!important}}`;
    function applyBox(el,box,b=block()){if(!el||!b)return;const prefix=device()==='mobile'?'m':'d',w=WIDTH[device()],h=b.props[device()==='mobile'?'mobileHeight':'height'];for(const[k,v]of [['x',box.x/w*100],['y',box.y/h*100],['w',box.width/w*100],['h',box.height/h*100]])el.style.setProperty(`--${prefix}-${k}`,v+'%');el.style.setProperty(`--${prefix}-fs`,box.fontSize/w*100+'cqw');}
    function guides(canvas,lines){canvas.querySelectorAll('.vf-guide').forEach(n=>n.remove());for(const axis of Object.keys(lines)){const n=doc.createElement('div');n.className='vf-guide vf-guide-'+axis;n.style[axis==='x'?'left':'top']=lines[axis]/(axis==='x'?WIDTH[device()]:height())*100+'%';canvas.append(n);}}
    let suppressUntil=0;
    function point(event,external){const r=frame.getBoundingClientRect(),scale=Number(frame.dataset.scale||1);return external?{x:(event.clientX-r.left)/scale,y:(event.clientY-r.top)/scale}:{x:event.clientX,y:event.clientY};}
    function endGesture(cancel=false){
      if(!gesture)return;const g=gesture;gesture=null;doc?.querySelectorAll('.vf-guide').forEach(n=>n.remove());g.overlay?.remove();
      if(g.marquee){if(cancel)choose(g.previous);else if(!g.moved)choose(g.clicked?[g.clicked]:[]);if(g.moved)suppressUntil=Date.now()+300;api.refresh();return;}
      if(cancel||!g.moved){g.records.forEach(r=>applyBox(r.el,r.box,r.b));updateSelection();return;}
      mutate(()=>{g.records.forEach((r,i)=>r.e[g.device]=g.result[i]);},{inspect:true,immediate:true});suppressUntil=Date.now()+300;
    }
    function pointerDown(event,external=false){
      if(readonly||event.button!==0||event.target.closest('[contenteditable],input,textarea,select,.vb-block-tools,.vf-floating-bar'))return;
      if(external&&event.target!==api.stage&&event.target.id!=='vb-scaled')return;
      const el=external?null:event.target.closest('[data-free-id]'),handle=external?'':event.target.closest('[data-free-handle]')?.dataset.freeHandle;
      const ref=el&&currentPage()?.blocks.filter(b=>b.type==='canvas').flatMap(b=>b.props.elements.map(e=>({b,e}))).find(r=>r.e.id===el.dataset.freeId);
      const multiKey=event.shiftKey||event.metaKey||event.ctrlKey;
      if(ref&&multiKey){event.preventDefault();select(ref.e.id,{toggle:true});suppressUntil=Date.now()+300;return;}
      if(!handle&&(!ref||ref.e.locked&&['shape','line'].includes(ref.e.type))){
        event.preventDefault();const p=point(event,external),overlay=api.stage.ownerDocument.createElement('div');overlay.className='vf-marquee';overlay.style.cssText='position:absolute;border:1px solid #147d73;background:#147d7318;pointer-events:none;z-index:10000';api.stage.append(overlay);
        gesture={marquee:true,start:p,end:p,previous:[...selectedIds],base:multiKey?[...selectedIds]:[],clicked:ref?.e.id,overlay,moved:false,external};(external?api.stage:doc.body).setPointerCapture(event.pointerId);return;
      }
      if(ref&&!selectedIds.has(ref.e.id))select(ref.e.id);
      const selectedRecords=records();if(!selectedRecords.length)return;
      if(selectedRecords.some(r=>r.e.locked)){if(selectedRecords.length>1)toast('В выделении есть закреплённые элементы. Снимите закрепление, чтобы двигать их вместе.');return;}
      event.preventDefault();const p=point(event,external);
      gesture={records:selectedRecords,device:device(),result:selectedRecords.map(r=>clone(r.box)),start:p,handle:handle||'',moved:false,external};doc.body.setPointerCapture(event.pointerId);
    }
    function pointerMove(event,external=false){
      if(!gesture||gesture.external!==external)return;const g=gesture,p=point(event,external),dx=p.x-g.start.x,dy=p.y-g.start.y;
      if(Math.abs(dx)+Math.abs(dy)<3&&!g.moved)return;g.moved=true;
      if(g.marquee){
        const rectangle={x:Math.min(p.x,g.start.x),y:Math.min(p.y,g.start.y),width:Math.abs(dx),height:Math.abs(dy)},fr=frame.getBoundingClientRect(),sr=api.stage.getBoundingClientRect(),scale=Number(frame.dataset.scale||1);
        Object.assign(g.overlay.style,{left:(fr.left-sr.left+rectangle.x*scale)+'px',top:(api.stage.scrollTop+fr.top-sr.top+rectangle.y*scale)+'px',width:rectangle.width*scale+'px',height:rectangle.height*scale+'px'});
        const hits=currentPage()?.blocks.filter(b=>b.type==='canvas').flatMap(b=>b.props.elements).filter(e=>!e.locked&&!e[device()].hidden).filter(e=>{const n=doc.querySelector(`[data-free-id="${e.id}"]`);if(!n)return false;const r=n.getBoundingClientRect();return Geometry.intersects(rectangle,{x:r.left,y:r.top,width:r.width,height:r.height});}).map(e=>e.id)||[];
        choose([...new Set([...g.base,...hits])]);updateSelection();return;
      }
      if(g.handle){g.result=g.records.length===1?[Geometry.resize(g.records[0].box,dx,dy,g.handle,g.records[0].width,g.records[0].height,g.records[0].e.style.rotation)]:(Geometry.resizeGroup(g.records,dx,dy,g.handle)||g.result);}
      else{
        let mx=dx,my=dy;
        if(g.records.length===1&&!event.altKey){const r=g.records[0],next=Geometry.move(r.box,dx,dy,r.width,r.height),others=r.b.props.elements.filter(e=>e!==r.e&&!e[g.device].hidden).map(e=>e[g.device]),snap=Geometry.snap(next,others,r.width,r.height,6);mx=snap.box.x-r.box.x;my=snap.box.y-r.box.y;guides(r.el.closest('.bb-free-canvas'),snap.guides);}
        g.result=Geometry.moveGroup(g.records,mx,my);
      }
      g.records.forEach((r,i)=>applyBox(r.el,g.result[i],r.b));
      const overlay=doc.querySelector('.vf-group-box');if(overlay){const bounds=Geometry.union(g.records.map((r,i)=>({...g.result[i],y:g.result[i].y+r.offsetY})));Object.assign(overlay.style,{left:bounds.x+'px',top:bounds.y+'px',width:bounds.width+'px',height:bounds.height+'px'});}
      if(g.records.length===1)for(const key of ['x','y','width','height']){const input=inspector.querySelector(`[data-free-box="${key}"]`);if(input)input.value=g.result[0][key];}
    }
    function suppressClick(event){if(Date.now()<suppressUntil){event.preventDefault();event.stopImmediatePropagation();suppressUntil=0;}}
    api.stage?.addEventListener('pointerdown',e=>pointerDown(e,true));api.stage?.addEventListener('pointermove',e=>pointerMove(e,true));api.stage?.addEventListener('pointerup',()=>endGesture());api.stage?.addEventListener('pointercancel',()=>endGesture(true));api.stage?.addEventListener('click',suppressClick,true);
    function bind(document,preview){
      doc=document;readonly=preview;if(preview)return;
      const style=doc.createElement('style');style.textContent=EDITOR_CSS;doc.head.append(style);
      doc.querySelectorAll('.bb-free-canvas').forEach(c=>{if(!c.querySelector('[data-free-id]'))c.dataset.freeEmpty='true';});
      doc.addEventListener('pointerdown',event=>{const canvas=event.target.closest('.bb-free-canvas');if(!canvas)return;const rect=canvas.getBoundingClientRect(),ratio=WIDTH[device()]/rect.width;lastPoint={block:canvas.closest('[data-block-id]').dataset.blockId,device:device(),x:Math.round((event.clientX-rect.left)*ratio),y:Math.round((event.clientY-rect.top)*ratio)};},true);
      doc.addEventListener('pointerdown',e=>pointerDown(e));doc.addEventListener('pointermove',e=>pointerMove(e));doc.addEventListener('pointerup',()=>endGesture());doc.addEventListener('pointercancel',()=>endGesture(true));doc.addEventListener('click',suppressClick,true);
      doc.addEventListener('dblclick',event=>{
        const el=event.target.closest('[data-free-id]'),ref=el&&currentPage()?.blocks.filter(b=>b.type==='canvas').flatMap(b=>b.props.elements.map(e=>({b,e}))).find(r=>r.e.id===el.dataset.freeId);if(!ref||!['text','button'].includes(ref.e.type))return;
        event.preventDefault();select(ref.e.id);const e=ref.e,text=el.querySelector('.bb-free-copy');text.setAttribute('contenteditable','plaintext-only');text.setAttribute('spellcheck','false');text.focus();
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
      mutate(()=>{block().props.elements.push(e);choose([e.id]);owner=block().id;},{inspect:true,immediate:true});
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
      mutate(()=>{b.props=props;commit();if(api.block()?.id===b.id){choose(elements.map(e=>e.id));owner=b.id;pendingReveal=selected;lastPoint=null;}},{inspect:true,immediate:true});
    }
    function stackMobile(b){
      const layout=Geometry.mobileLayout(b.props.elements);if(!layout)return false;
      b.props.mobileHeight=layout.height;b.props.elements.forEach(e=>e.mobile=layout.boxes[e.id]);return true;
    }
    function action(name){
      const e=item(),b=block(),refs=members();if(!active())return false;
      if(name==='deselect'){choose([]);api.refresh();return true;}
      if(name==='image'&&e){api.openMedia({block:b.id,element:e.id});return true;}
      if(name==='mobile-stack'){let built=false;mutate(()=>{built=stackMobile(b);},{inspect:true,immediate:true});toast(built?'Версия для телефона собрана. При необходимости поправьте элементы.':'Блок слишком длинный для телефона. Разделите его на несколько блоков.');return true;}
      if(!refs.length)return true;
      if(name==='delete'&&refs.some(r=>r.e.locked)){toast('Сначала снимите закрепление выбранных элементов.');return true;}
      const blocks=[...new Set(refs.map(r=>r.b))];
      if(name==='duplicate'&&(total()+refs.length>250||blocks.some(b=>b.props.elements.length+refs.filter(r=>r.b===b).length>60))){toast('Недостаточно места для копии. В блоке может быть до 60 элементов, на сайте — до 250.');return true;}
      mutate(()=>{
        const copies=[];
        for(const b of blocks){const list=b.props.elements,chosen=list.filter(e=>selectedIds.has(e.id)),rest=list.filter(e=>!selectedIds.has(e.id));
          if(name==='delete')b.props.elements=rest;
          if(name==='front'){if(refs.length===1){const i=list.indexOf(chosen[0]);if(i<list.length-1)[list[i],list[i+1]]=[list[i+1],list[i]];}else b.props.elements=[...rest,...chosen];}
          if(name==='back'){if(refs.length===1){const i=list.indexOf(chosen[0]);if(i>0)[list[i],list[i-1]]=[list[i-1],list[i]];}else b.props.elements=[...chosen,...rest];}
          if(name==='duplicate'){
            const cloned=chosen.map(e=>({...clone(e),id:uid(),locked:false}));
            for(const d of ['desktop','mobile']){const boxes=Geometry.moveGroup(chosen.map(e=>({box:e[d],width:WIDTH[d],height:b.props[d==='mobile'?'mobileHeight':'height']})),16,16);cloned.forEach((e,i)=>e[d]=boxes[i]);}
            list.push(...cloned);copies.push(...cloned.map(e=>e.id));
          }
          if(name==='transparent')chosen.forEach(e=>e.style.background='transparent');
        }
        if(name==='delete')choose([]);if(name==='duplicate')choose(copies);
      },{inspect:true,immediate:true});return true;
    }
    function align(value){
      const list=records();if(!list.length||list.some(r=>r.e.locked))return;
      const bounds=groupBounds(list),w=WIDTH[device()],top=Math.min(...list.map(r=>r.offsetY)),bottom=Math.max(...list.map(r=>r.offsetY+r.height));let dx=0,dy=0;
      if(value==='left')dx=-bounds.x;if(value==='center')dx=(w-bounds.width)/2-bounds.x;if(value==='right')dx=w-bounds.width-bounds.x;
      if(value==='top')dy=top-bounds.y;if(value==='middle')dy=(top+bottom-bounds.height)/2-bounds.y;if(value==='bottom')dy=bottom-bounds.height-bounds.y;
      const boxes=Geometry.moveGroup(list,dx,dy);mutate(()=>list.forEach((r,i)=>r.e[device()]=boxes[i]),{inspect:true});
    }
    function input(el){
      const d=el.dataset;if(!active())return;
      if(d.freeHeight!==undefined){const max=device()==='mobile'?6000:4000,n=Number(el.value);if(!el.value||!Number.isInteger(n)||n<40||n>max)return;mutate(()=>{block().props[device()==='mobile'?'mobileHeight':'height']=n;block().props.elements.forEach(i=>{i[device()].height=Math.min(i[device()].height,n);i[device()].y=Math.min(i[device()].y,n-i[device()].height);});},{group:'free-height'});return;}
      const refs=members(),e=d.freeMember?refs.find(r=>r.e.id===d.freeMember)?.e:item();if(!refs.length)return;
      const group=refs.map(r=>r.e.id).join(':');
      if(d.freeContent&&e)mutate(()=>e.content[d.freeContent]=el.value,{group:'free-content:'+e.id+':'+d.freeContent,inspect:false});
      if(d.freeToggle)mutate(()=>refs.forEach(({e})=>{if(d.freeToggle==='locked')e.locked=el.checked;else e[device()].hidden=el.checked;}),{inspect:d.freeToggle==='locked'});
      if(d.freeBox){const n=Number(el.value),key=d.freeBox;if(!el.value||!Number.isInteger(n)||!el.validity.valid)return;
        if(key==='fontSize'){mutate(()=>refs.filter(r=>['text','button','faq'].includes(r.e.type)).forEach(({e})=>e[device()].fontSize=clamp(n,8,240)),{group:'free-font:'+group,inspect:false});return;}
        if(refs.some(r=>r.e.locked))return;
        const list=records(),bounds=groupBounds(list);let boxes;
        if(key==='x'||key==='y')boxes=Geometry.moveGroup(list,key==='x'?n-bounds.x:0,key==='y'?n-bounds.y:0);
        else if(list.length>1)boxes=Geometry.resizeGroup(list,key==='width'?n-bounds.width:0,key==='height'?n-bounds.height:0,'se');
        else{const r=list[0],box=clone(r.box);box[key]=clamp(n,key==='width'?16:4,(key==='width'?r.width-box.x:r.height-box.y));boxes=[box];}
        if(!boxes){toast('Размер группы выходит за границы блока. Сначала увеличьте высоту блока.');return;}
        mutate(()=>list.forEach((r,i)=>r.e[device()]=boxes[i]),{group:'free-box:'+group+':'+key,inspect:false});
      }
      const key=d.freeStyle||d.freeColor;if(key){let value=el.type==='number'?Number(el.value):el.value;if(!el.value||el.type==='number'&&(!Number.isInteger(value)||!el.validity.valid))return;if(d.freeColor&&!/^(#[0-9a-fA-F]{6}|transparent)$/.test(value))return;
        const textOnly=['color','fontFamily','fontWeight','textAlign','lineHeight','letterSpacing'].includes(key);
        mutate(()=>refs.filter(r=>!textOnly||['text','button','faq','art'].includes(r.e.type)).forEach(({e})=>e.style[key]=value),{group:'free-style:'+group+':'+key,inspect:false});
        const twin=inspector.querySelector(d.freeColor?`[data-free-style="${key}"]`:`[data-free-color="${key}"]`);if(twin&&value!=='transparent')twin.value=value;
      }
    }
    function keydown(event){
      if(event.target.closest?.('input,textarea,select,[contenteditable]')||!active())return false;
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='a'){event.preventDefault();choose(block().props.elements.filter(e=>!e.locked&&!e[device()].hidden).map(e=>e.id));api.refresh();return true;}
      if(event.key==='Escape'){endGesture(true);choose([]);api.refresh();return true;}
      if(!members().length)return false;
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='d'){event.preventDefault();action('duplicate');return true;}
      if(['Delete','Backspace'].includes(event.key)){event.preventDefault();action('delete');return true;}
      if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();const list=records(),step=event.shiftKey?10:1;if(!list.some(r=>r.e.locked)){const boxes=Geometry.moveGroup(list,event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0,event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0);mutate(()=>list.forEach((r,i)=>r.e[device()]=boxes[i]),{group:'free-nudge:'+list.map(r=>r.e.id).join(':'),inspect:true});}return true;}return false;
    }
    function assignImage(target,id){if(!target?.element)return false;const b=api.pages().flatMap(p=>p.blocks).find(b=>b.id===target.block),e=b?.props.elements.find(e=>e.id===target.element);if(e){mutate(()=>{e.content.image=id;e.content.alt=e.content.alt||config.assets.find(a=>a.id===id)?.name||'';},{inspect:true,immediate:true});}return true;}
    async function convert(){
      const b=block();if(!b||b.type==='canvas')return;
      const source=JSON.stringify(b),html=frame.contentDocument?.documentElement.outerHTML;if(!html)return;
      const [result]=await window.BrunoBuilderTemplates.prepare(html,[clone(b)],{uid,assets:config.assets});
      if(JSON.stringify(b)!==source){toast('Блок изменился во время подготовки. Повторите попытку.');return;}
      mutate(()=>{Object.assign(b,result);choose([]);},{inspect:true,immediate:true});
    }
    return {active,current,item,layers,select,selection:()=>members().map(r=>r.e.id),inspectorHTML,toolbar,updateSelection,bind,add,action,align,input,keydown,assignImage,convert,pastePoint,insertClipboard,clear(){endGesture(true);selected=null;selectedIds.clear();owner=null;},cancel(){endGesture(true);}};
  };
})();

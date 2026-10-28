/* AI-site visual editor: literal source changes; the preview executes no site scripts. */
(() => {
  'use strict';
  const configNode=document.getElementById('manual-editor-config');if(!configNode)return;
  const cfg=JSON.parse(configNode.textContent),root=document.getElementById('visual-builder');
  const $=s=>root.querySelector(s), $$=s=>[...root.querySelectorAll(s)];
  const clone=v=>JSON.parse(JSON.stringify(v)), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const frame=$('#bm-frame'),stage=$('#bm-stage'),scaled=$('#bm-scaled'),inspector=$('#bm-inspector'),dialog=$('#bm-dialog');
  const csrf=$('input[name=csrfmiddlewaretoken]').value;
  let changes=clone(cfg.changes),revision=cfg.revision,saved=JSON.stringify(changes),saving=null,conflict=false;
  let selected=cfg.elements.find(e=>e.tag==='h1')?.id||cfg.elements[0]?.id,panel='content',filter='content';
  let device=innerWidth<=680?'mobile':'desktop',undo=[],redo=[],group='',lastChange=0;
  let saveTimer,renderTimer,controller,serial=0,toastTimer,imageTarget=null,frameLoaded=false;
  const element=()=>cfg.elements.find(e=>e.id===selected), dirty=()=>JSON.stringify(changes)!==saved;
  const fieldChanges=id=>changes.elements[id]||{};
  function status(text,pending=false){$('#bm-status').innerHTML=`<i></i>${esc(text)}`;$('#bm-status').classList.toggle('dirty',pending);}
  function toast(text){const el=$('#bm-toast');el.textContent=text;el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,3500);}
  function error(text){$('#bm-error span').textContent=text;$('#bm-error').hidden=false;}
  async function post(url,data,form=false,signal){
    let response;try{response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':csrf,'Accept':'application/json',...(form?{}:{'Content-Type':'application/json'})},body:form?data:JSON.stringify(data),signal});}
    catch(e){if(e.name==='AbortError')throw e;throw new Error('Нет связи с сервером. Правки остаются в этой вкладке.');}
    let result;try{result=await response.json();}catch(_){throw new Error(response.status===403?'Сессия закончилась. Скачайте копию правок и войдите снова.':'Сервер не смог обработать запрос. Повторите попытку.');}
    if(!response.ok){const e=new Error(result.error||'Не удалось применить изменения.');e.status=response.status;throw e;}return result;
  }
  function mutate(work,key='',reinspect=false){
    const before=clone(changes),now=Date.now();work();if(JSON.stringify(before)===JSON.stringify(changes))return;
    if(!key||key!==group||now-lastChange>950){undo.push(before);if(undo.length>50)undo.shift();}
    group=key;lastChange=now;redo=[];refresh(reinspect);status('Есть несохранённые изменения',true);
    clearTimeout(saveTimer);saveTimer=setTimeout(()=>save().catch(()=>{}),1800);scheduleRender();
  }
  async function save(){
    clearTimeout(saveTimer);if(saving){await saving;if(dirty())return save();return;}
    if(!dirty())return;if(conflict)throw new Error('Черновик изменён в другой вкладке. Скачайте копию и перезагрузите редактор.');
    const sent=JSON.stringify(changes);status('Сохраняем…',true);
    saving=post(cfg.urls.save,{changes:JSON.parse(sent),revision,digest:cfg.digest}).then(data=>{revision=data.revision;saved=sent;$('#bm-error').hidden=true;status(dirty()?'Есть несохранённые изменения':'Все изменения сохранены',dirty());})
      .catch(e=>{if(e.status===409)conflict=true;status('Не удалось сохранить',true);error(e.message);throw e;}).finally(()=>saving=null);
    await saving;if(dirty()){clearTimeout(saveTimer);saveTimer=setTimeout(()=>save().catch(()=>{}),1800);}
  }
  async function flush(){await save();if(dirty())await flush();}
  function history(back){const from=back?undo:redo,to=back?redo:undo;if(!from.length)return;to.push(clone(changes));changes=from.pop();group='';refresh(true);scheduleRender(0);status('Есть несохранённые изменения',true);clearTimeout(saveTimer);saveTimer=setTimeout(()=>save().catch(()=>{}),1800);}
  function refresh(full=true){
    $('#bm-count').textContent=cfg.elements.length;
    const search=$('#bm-search').value.trim().toLowerCase();
    const list=cfg.elements.filter(e=>(filter==='all'||filter==='images'&&e.image||filter==='content'&&(e.texts.length||e.image||['section','header','footer'].includes(e.tag)))&&(!search||`${e.label} ${e.type} ${e.tag}`.toLowerCase().includes(search)));
    $('#bm-layers').innerHTML=list.slice(0,220).map(e=>`<button class="vb-layer ${e.id===selected?'active':''}" data-element="${e.id}" aria-label="${esc(e.type+': '+(e.label||e.tag))}"><span class="vb-layer-symbol" aria-hidden="true">${e.image?'▧':/^h\d$/.test(e.tag)?e.tag.toUpperCase():e.tag==='button'?'↗':'T'}</span><span class="vb-layer-label">${esc(e.type)}<small>${esc((e.texts[0]&&changes.texts[e.texts[0].id])??e.label??'')}</small></span></button>`).join('')||'<p class="bm-empty">Ничего не найдено. Попробуйте другой запрос или выберите элемент на холсте.</p>';
    if(list.length>220)$('#bm-layers').insertAdjacentHTML('beforeend','<p class="bm-more">Уточните поиск, чтобы увидеть остальные элементы.</p>');
    $('[data-action=undo]').disabled=!undo.length;$('[data-action=redo]').disabled=!redo.length;
    if(full)renderInspector();highlight();
  }
  function select(id,scroll=false){selected=id;refresh(true);if(scroll){const el=frame.contentDocument?.querySelector(`[data-bm-element="${id}"]`);el?.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});}}
  function highlight(){if(!frameLoaded)return;frame.contentDocument?.querySelectorAll('[data-bm-element]').forEach(el=>el.classList.toggle('bm-selected',el.dataset.bmElement===selected));}
  function node(){return frame.contentDocument?.querySelector(`[data-bm-element="${selected}"]`);}
  function computed(){const el=node();return el?frame.contentWindow.getComputedStyle(el):null;}
  function hex(value,fallback){if(/^rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(String(value)))return fallback;const m=String(value||'').match(/^rgba?\((\d+)[, ]+(\d+)[, ]+(\d+)/);return m?'#'+m.slice(1,4).map(n=>Number(n).toString(16).padStart(2,'0')).join(''):fallback;}
  function input(key,label,value,type='text',extra=''){return `<label class="vb-field"><span>${label}</span><input data-value="${key}" type="${type}" value="${esc(value)}" ${extra}></label>`;}
  function styleField(key,label,type='number',options=null){
    const patch=fieldChanges(selected).style||{},c=computed();let value=patch[key];
    if(value===undefined){if(type==='color')value=hex(c?.[key],key==='color'?'#122338':'#ffffff');else if(type==='number')value=Math.round(parseFloat(c?.[key])||0);else value=c?.[key]||'';}
    let control;if(options)control=`<select data-style="${key}"><option value="">Как в исходнике</option>${options.map(([v,l])=>`<option value="${v}" ${patch[key]===v?'selected':''}>${l}</option>`).join('')}</select>`;
    else if(type==='color')control=`<div class="vb-color-row"><input type="color" data-style="${key}" value="${value}" aria-label="${label}"><input data-color="${key}" value="${value}" maxlength="7" aria-label="${label} HEX"></div>`;
    else control=`<input type="number" data-style="${key}" value="${value}" min="${key==='fontSize'?8:0}" max="${key==='fontSize'?120:key==='padding'?160:100}" step="1">`;
    return `<label class="vb-field bm-style-field"><span>${label}</span>${control}${patch[key]!==undefined?`<button type="button" class="bm-reset" data-reset-style="${key}" title="Вернуть исходное значение" aria-label="Сбросить ${label}">↶</button>`:''}</label>`;
  }
  function imageField(kind,label){const patch=fieldChanges(selected),asset=cfg.assets.find(a=>a.id===patch[kind]);return `<label class="vb-field"><span>${label}</span></label><div class="vb-image-field">${asset?`<img class="bm-image-preview" src="${esc(asset.url)}" alt="${esc(asset.name)}">`:'<div class="vb-image-placeholder" aria-hidden="true">▧</div>'}<div><button class="vb-btn" data-image="${kind}">${asset?'Заменить':'Выбрать изображение'}</button>${asset?`<button class="vb-icon" data-reset-image="${kind}" aria-label="Вернуть исходное изображение">↶</button>`:''}</div></div>`;}
  function renderInspector(){
    const e=element();if(!e){inspector.innerHTML='<div class="vb-empty-inspector"><span>✳</span><h2>Ваш сайт можно менять</h2><p>Выберите текст, иллюстрацию или секцию на холсте.</p></div>';return;}
    const patch=fieldChanges(selected);let html=`<span class="bm-node-kind">${esc(e.tag.toUpperCase())}</span><h2>${esc(e.type)}</h2><p class="vb-subtitle">${esc(e.label.slice(0,65)||'Элемент вашего сайта')}</p>`;
    if(e.parent&&cfg.elements.some(p=>p.id===e.parent))html+=`<div class="bm-breadcrumb"><button data-parent="${e.parent}">↑ Выбрать контейнер</button></div>`;
    if(panel==='content'){
      e.texts.forEach((t,i)=>{html+=`<label class="vb-field"><span>${e.texts.length===1?'Текст':`Фрагмент ${i+1}`}</span><textarea data-text="${t.id}" maxlength="6000" rows="4">${esc(changes.texts[t.id]??t.value)}</textarea></label>`;});
      if(e.image)html+=imageField('image',e.tag==='svg'?'Заменить иллюстрацию':'Изображение')+input('alt','Описание изображения',patch.alt??e.alt,'text','maxlength="300"');
      if(e.link!==null)html+=input('href','Ссылка',patch.href??e.link,'text','maxlength="1000" placeholder="https://, /страница/ или #якорь"');
      if(e.placeholder!==null)html+=input('placeholder','Подсказка в поле',patch.placeholder??e.placeholder,'text','maxlength="300"');
      if(!e.texts.length&&!e.image&&e.link===null&&e.placeholder===null)html+='<p class="bm-hint">Это контейнер. Для изменения текста выберите подпись внутри него, для фона и отступов — вкладку «Оформление».</p>';
    }else{
      html+=styleField('color','Цвет текста','color')+styleField('backgroundColor','Цвет фона','color');
      html+=`<div class="bm-two-fields">${styleField('fontSize','Размер текста, px')}${styleField('borderRadius','Скругление, px')}</div>`;
      html+=styleField('fontFamily','Шрифт','select',[['modern','Современный'],['serif','С засечками'],['mono','Моноширинный']]);
      html+=styleField('fontWeight','Насыщенность','select',[['400','Обычный'],['500','Средний'],['600','Полужирный'],['700','Жирный'],['800','Очень жирный']]);
      html+=styleField('textAlign','Выравнивание','select',[['left','Слева'],['center','По центру'],['right','Справа']]);
      html+=`<div class="bm-two-fields">${styleField('padding','Отступ внутри, px')}${styleField('gap','Между элементами, px')}</div>`;
      if(e.image)html+=styleField('objectFit','Вписывание картинки','select',[['cover','Заполнить'],['contain','Вместить целиком']]);
      html+='<div class="vb-separator"></div>'+imageField('backgroundImage','Фоновое изображение');
      if(e.tag!=='body')html+=`<label class="bm-check"><input type="checkbox" data-hidden ${patch.hidden?'checked':''}> Скрыть элемент</label>`;
    }
    html+='<div class="vb-separator"></div><button class="vb-btn" data-action="reset-element">↶ Вернуть исходный элемент</button><p class="vb-control-note">Правки не расходуют AI-токены. Предыдущая версия сайта сохраняется.</p>';
    inspector.innerHTML=html;
  }
  function setPanel(value){panel=value;$$('[data-tab]').forEach(el=>{el.classList.toggle('active',el.dataset.tab===value);el.setAttribute('aria-selected',el.dataset.tab===value);});renderInspector();}
  function scheduleRender(ms=500){clearTimeout(renderTimer);renderTimer=setTimeout(()=>renderCanvas().catch(e=>{if(e.name!=='AbortError')error(e.message);}),ms);}
  function fit(){const width=device==='mobile'?390:1120,available=Math.max(100,stage.clientWidth-(innerWidth<=680?28:innerWidth>=1600?80:innerWidth<=1180?36:56)),scale=Math.min(1,available/width);const height=device==='mobile'?844:Math.max(720,Math.min(1000,(stage.clientHeight-5)/scale));frame.style.width=width+'px';frame.style.height=height+'px';frame.style.transform=`scale(${scale})`;scaled.style.width=width*scale+'px';scaled.style.height=height*scale+'px';$('#bm-zoom').textContent=Math.round(scale*100)+'%';}
  async function renderCanvas(){
    if(frame.contentDocument?.activeElement?.isContentEditable)return;
    controller?.abort();controller=new AbortController();const run=++serial;
    const result=await post(cfg.urls.preview,{changes,digest:cfg.digest},false,controller.signal);if(run!==serial)return;
    const scroll=frame.contentDocument?.scrollingElement?.scrollTop||0;
    frame.onload=()=>{
      if(run!==serial)return;const doc=frame.contentDocument;if(!doc?.body)return;
      frameLoaded=true;fit();highlight();$('#bm-loading').hidden=true;
      doc.addEventListener('click',event=>{if(event.target.closest('a,button,input,select,textarea,label'))event.preventDefault();const el=event.target.closest('[data-bm-element]');if(el)select(el.dataset.bmElement);},true);
      doc.addEventListener('submit',event=>event.preventDefault(),true);
      doc.querySelectorAll('bl-text[data-bm-text]').forEach(text=>{
        text.setAttribute('contenteditable','plaintext-only');text.setAttribute('spellcheck','false');
        text.addEventListener('focus',()=>select(text.dataset.bmOwner));
        text.addEventListener('input',()=>{const id=text.dataset.bmText,value=text.innerText.slice(0,6000),original=cfg.elements.flatMap(e=>e.texts).find(t=>t.id===id)?.value;mutate(()=>{if(value===original)delete changes.texts[id];else changes.texts[id]=value;},'inline:'+id);const input=inspector.querySelector(`[data-text="${id}"]`);if(input)input.value=value;});
        text.addEventListener('blur',()=>scheduleRender(0));
      });
      doc.addEventListener('keydown',shortcuts);if(doc.scrollingElement)doc.scrollingElement.scrollTop=scroll;
    };
    frame.srcdoc=result.html;
  }
  function openDialog(title,html){$('#bm-dialog-title').textContent=title;$('#bm-dialog-body').innerHTML=html;if(!dialog.open)dialog.showModal();}
  function openImages(kind){imageTarget={id:selected,kind};openDialog('Ваши изображения',`<div class="vb-media-top"><p>JPG, PNG или WebP · до 5 МБ<br>Из библиотеки этого проекта</p><button class="vb-btn vb-primary" data-action="upload">↑ Загрузить</button></div>${cfg.assets.length?`<div class="vb-media-grid">${cfg.assets.map(a=>`<button class="vb-media-card" data-asset="${a.id}"><img src="${esc(a.url)}" alt="${esc(a.name)}"><span>${esc(a.name)}</span><small>${a.width} × ${a.height}</small></button>`).join('')}</div>`:'<div class="vb-media-empty">Загрузите свою фотографию или иллюстрацию.</div>'}`);}
  function useImage(id){if(!imageTarget)return;const{id:target,kind}=imageTarget;mutate(()=>{changes.elements[target]={...fieldChanges(target),[kind]:id};},'',true);dialog.close();toast('Изображение заменено');}
  async function upload(file){if(!file)return;if(file.size>5*1024*1024){error('Изображение должно быть не больше 5 МБ.');return;}const form=new FormData();form.append('image',file);const button=$('[data-action=upload]');if(button){button.disabled=true;button.textContent='Загружаем…';}try{const asset=await post(cfg.uploadUrl,form,true);if(!cfg.assets.some(a=>a.id===asset.id))cfg.assets.unshift(asset);useImage(asset.id);}catch(e){error(e.message);if(button){button.disabled=false;button.textContent='↑ Загрузить';}}finally{$('#bm-upload').value='';}}
  async function snapshot(){await flush();const data=await post(cfg.urls.snapshot,{revision});return data;}
  async function busy(button,work){const old=button?.innerHTML;if(button){button.disabled=true;button.textContent='Подождите…';}try{await work();}catch(e){error(e.message);}finally{if(button){button.disabled=false;button.innerHTML=old;}}}
  function backup(){const blob=new Blob([JSON.stringify({source:cfg.source,digest:cfg.digest,changes},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='brunolab-manual-changes.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
  async function action(name,button){
    if(name==='undo')return history(true);if(name==='redo')return history(false);
    if(name==='layers'){root.classList.toggle('is-left-open');root.classList.remove('is-right-open');return;}
    if(name==='settings'){root.classList.toggle('is-right-open');root.classList.remove('is-left-open');return;}
    if(name==='close')return dialog.close();if(name==='upload')return $('#bm-upload').click();if(name==='backup')return backup();
    if(name==='reset-element'){const e=element();if(!e)return;mutate(()=>{delete changes.elements[e.id];e.texts.forEach(t=>delete changes.texts[t.id]);},'',true);return;}
    if(name==='retry')return busy(button,async()=>{await flush();await renderCanvas();$('#bm-error').hidden=true;});
    if(name==='download')return busy(button,async()=>{await flush();const a=document.createElement('a');a.href=cfg.urls.download;a.download='';a.click();});
    if(name==='snapshot')return busy(button,async()=>{await snapshot();toast('Ручные правки сохранены в новой версии');});
    if(name==='work')return busy(button,async()=>{const data=await snapshot();location.href=data.url;});
    if(name==='publish')return busy(button,async()=>{const result=await snapshot(),form=new FormData();form.append('version',result.version);form.append('action','publish');const data=await post(cfg.hostingUrl,form,true);openDialog('Сайт опубликован',`<div class="vb-publish-success"><div class="vb-success-mark">✓</div><h3>Ручные правки уже на сайте.</h3><p>Следующие изменения останутся в черновике до новой публикации.</p><div class="vb-public-url"><a href="${esc(data.url)}" target="_blank" rel="noopener">${esc(data.url)}</a><button class="vb-btn" data-copy="${esc(data.url)}">Копировать</button></div><a class="vb-btn vb-primary" href="${esc(data.url)}" target="_blank" rel="noopener">Открыть сайт ↗</a></div>`);});
  }
  root.addEventListener('click',event=>{
    const el=event.target.closest('button');if(!el)return;const d=el.dataset;
    if(d.action){event.preventDefault();action(d.action,el);return;}
    if(d.element)return select(d.element,true);if(d.parent)return select(d.parent,true);
    if(d.tab)return setPanel(d.tab);
    if(d.filter){filter=d.filter;$$('[data-filter]').forEach(b=>b.classList.toggle('active',b===el));refresh(false);return;}
    if(d.device){device=d.device;$$('[data-device]').forEach(b=>{b.classList.toggle('active',b===el);b.setAttribute('aria-pressed',b===el);});fit();return;}
    if(d.image)return openImages(d.image);if(d.asset)return useImage(d.asset);
    if(d.resetImage){mutate(()=>{const p=fieldChanges(selected);delete p[d.resetImage];if(!Object.keys(p).length)delete changes.elements[selected];},'',true);return;}
    if(d.resetStyle){mutate(()=>{const p=fieldChanges(selected);if(p.style)delete p.style[d.resetStyle];if(p.style&&!Object.keys(p.style).length)delete p.style;if(!Object.keys(p).length)delete changes.elements[selected];},'',true);return;}
    if(d.copy)navigator.clipboard.writeText(d.copy).then(()=>toast('Ссылка скопирована')).catch(()=>error('Скопируйте ссылку из адресной строки сайта.'));
  });
  inspector.addEventListener('input',event=>{
    const el=event.target,d=el.dataset,e=element();if(!e)return;
    if(d.text){const original=e.texts.find(t=>t.id===d.text)?.value;mutate(()=>{if(el.value===original)delete changes.texts[d.text];else changes.texts[d.text]=el.value;},'text:'+d.text);}
    if(d.value)mutate(()=>{changes.elements[selected]={...fieldChanges(selected),[d.value]:el.value};},selected+':'+d.value);
    let key=d.style||d.color,value=el.value;
    if(d.hidden!==undefined)mutate(()=>{const p={...fieldChanges(selected)};if(el.checked)p.hidden=true;else delete p.hidden;changes.elements[selected]=p;},'',false);
    if(key){if(d.color&&!/^#[0-9a-fA-F]{6}$/.test(value))return;if(el.type==='number'){if(value===''||!el.validity.valid)return;value=Number(value);}mutate(()=>{const p={...fieldChanges(selected),style:{...fieldChanges(selected).style}};if(value==='')delete p.style[key];else p.style[key]=value;changes.elements[selected]=p;},'style:'+selected+':'+key);const twin=inspector.querySelector(d.color?`[data-style="${key}"]`:`[data-color="${key}"]`);if(twin)twin.value=value;}
  });
  $('#bm-search').addEventListener('input',()=>refresh(false));$('#bm-upload').addEventListener('change',event=>upload(event.target.files[0]));
  function shortcuts(event){const typing=event.target.matches?.('input,textarea,select,[contenteditable]');if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();busy(null,async()=>{await flush();toast('Изменения сохранены');});}else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'&&!typing){event.preventDefault();history(!event.shiftKey);}else if(event.key==='Escape'){root.classList.remove('is-left-open','is-right-open');}}
  document.addEventListener('keydown',shortcuts);window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&dirty()&&!conflict)save().catch(()=>{});});new ResizeObserver(fit).observe(stage);
  $$('[data-device]').forEach(b=>{b.classList.toggle('active',b.dataset.device===device);b.setAttribute('aria-pressed',b.dataset.device===device);});
  refresh(true);renderCanvas().catch(e=>error(e.message));
})();

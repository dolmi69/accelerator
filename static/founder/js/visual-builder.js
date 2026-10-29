/* Native block/page editor. Trusted, script-free iframe; no third-party editor CDN. */
(() => {
  'use strict';
  const root = document.getElementById('visual-builder');
  if (!root) return;
  const config = JSON.parse(document.getElementById('visual-builder-config').textContent);
  const $ = (selector, within = root) => within.querySelector(selector);
  const $$ = (selector, within = root) => [...within.querySelectorAll(selector)];
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uid = () => crypto.randomUUID().replaceAll('-', '');
  const frame = $('#vb-frame'), stage = $('#vb-stage'), scaled = $('#vb-scaled');
  const dialog = $('#vb-dialog'), inspector = $('#vb-inspector');
  const csrf = $('input[name=csrfmiddlewaretoken]').value;
  const symbols = {nav:'☰',hero:'◩',text:'T',features:'▦',image:'▧',gallery:'▥',quotes:'❝',pricing:'₽',faq:'?',cta:'↗',footer:'⊟',canvas:'◇'};
  let documentState = clone(config.document), revision = config.revision;
  let currentPage = documentState.pages[0].id, selected = documentState.pages[0].blocks[1]?.id || documentState.pages[0].blocks[0]?.id || null;
  let panel = 'content', device = innerWidth <= 680 ? 'mobile' : 'desktop', previewMode = false, undoStack = [], redoStack = [];
  let savedJSON = JSON.stringify(documentState), savePromise = null, saveTimer, renderTimer, renderController;
  let changeGroup = '', lastChange = 0, previewSerial = 0, resizeObserver, naturalHeight = 1000;
  let imageTarget = null, dragID = null, toastTimer, conflict = false, dialogKind = '', pendingInsertion = null;
  let hostedURL = config.hostedUrl, pasteQueue=Promise.resolve(), pendingPageAnchor='';
  const clipboard=window.BrunoBuilderClipboard;
  const page = () => documentState.pages.find(p => p.id === currentPage) || documentState.pages[0];
  const block = () => page().blocks.find(b => b.id === selected);
  const isDirty = () => savedJSON !== JSON.stringify(documentState);
  const path = p => p.slug ? `/${p.slug}/` : '/';
  const status = (message, dirty = false) => { const el = $('#vb-save-status'); el.innerHTML = `<i></i>${esc(message)}`; el.classList.toggle('dirty', dirty); };
  const toast = message => { const el = $('#vb-toast'); el.textContent = message; el.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => el.hidden = true, 3500); };
  const showError = message => { $('#vb-error span').textContent = message; $('#vb-error').hidden = false; };
  const hideError = () => { $('#vb-error').hidden = true; };
  async function request(url, body, {form = false, signal} = {}) {
    let response;
    try { response = await fetch(url, {method: 'POST', credentials: 'same-origin', signal,
      headers: {'X-CSRFToken': csrf, 'Accept': 'application/json', ...(form ? {} : {'Content-Type':'application/json'})}, body: form ? body : JSON.stringify(body)}); }
    catch (error) { if (error.name === 'AbortError') throw error; throw new Error('Нет связи с сервером. Изменения остаются в этой вкладке.'); }
    let data;
    try { data = await response.json(); } catch (_) { throw new Error(response.status === 403 ? 'Сессия закончилась. Скачайте копию изменений и войдите снова.' : 'Сервер не смог обработать запрос. Повторите попытку.'); }
    if (!response.ok) { const error = new Error(data.error || 'Не удалось сохранить изменения.'); error.status = response.status; throw error; }
    return data;
  }
  const free=window.BrunoFreeCanvas({esc,uid,clone,mutate,toast,frame,stage,inspector,config,block,page,pages:()=>documentState.pages,theme:()=>documentState.theme,device:()=>device,refresh,scheduleRender,openMedia,selectBlock:(id)=>selectBlock(id,{preserveFree:true})});
  function mutate(action, {group = '', inspect = false, immediate = false} = {}) {
    const before = clone(documentState), now = Date.now();
    action();
    if (JSON.stringify(before) === JSON.stringify(documentState)) return;
    if (!group || changeGroup !== group || now - lastChange > 950) {
      undoStack.push(before); if (undoStack.length > 50) undoStack.shift();
    }
    changeGroup = group; lastChange = now; redoStack = [];
    refresh({inspect}); status('Есть несохранённые изменения', true);
    clearTimeout(saveTimer); saveTimer = setTimeout(() => save().catch(() => {}), 1800);
    scheduleRender(immediate ? 0 : 450);
  }
  async function save() {
    clearTimeout(saveTimer);
    if (savePromise) { await savePromise; if (isDirty()) return save(); return; }
    if (!isDirty()) return;
    if (conflict) throw new Error('Черновик изменён в другой вкладке. Скачайте свою копию и перезагрузите страницу.');
    const sent = JSON.stringify(documentState);
    status('Сохраняем…', true);
    savePromise = request(config.urls.draft, {document: JSON.parse(sent), revision}).then(data => {
      revision = data.revision; savedJSON = sent; hideError(); status(isDirty() ? 'Есть несохранённые изменения' : 'Все изменения сохранены', isDirty());
    }).catch(error => {
      if (error.status === 409) conflict = true;
      status('Изменения не сохранены', true); showError(error.message); throw error;
    }).finally(() => savePromise = null);
    await savePromise;
    if (isDirty()) { clearTimeout(saveTimer); saveTimer = setTimeout(() => save().catch(() => {}), 1800); }
  }
  async function flushSave() { await save(); if (isDirty()) await flushSave(); }
  function history(direction) {
    const from = direction === 'undo' ? undoStack : redoStack, to = direction === 'undo' ? redoStack : undoStack;
    if (!from.length) return;
    to.push(clone(documentState)); documentState = from.pop(); changeGroup = '';
    if (!documentState.pages.some(p => p.id === currentPage)) currentPage = documentState.pages[0].id;
    if (!block()) selected = null;
    refresh(); scheduleRender(0); status('Есть несохранённые изменения', true);
    clearTimeout(saveTimer); saveTimer = setTimeout(() => save().catch(() => {}), 1800);
  }
  function switchPage(id, anchor='') {
    currentPage = id; selected = null; pendingPageAnchor=anchor; root.classList.remove('is-left-open');
    refresh(); scheduleRender(0); stage.scrollTop = 0;
  }
  function refresh({inspect = true} = {}) {
    const p = page(); currentPage = p.id; free.current();
    $('#vb-page-title').textContent = p.title;
    $('#vb-page-count').textContent = documentState.pages.length;
    $('#vb-block-count').textContent = String(p.blocks.length).padStart(2, '0');
    $('#vb-canvas-address').textContent = `${p.title} ${path(p)}`;
    $('#vb-pages').innerHTML = documentState.pages.map((p,i) => `<div class="vb-page ${p.id === currentPage ? 'active' : ''}"><button data-page="${p.id}"><span aria-hidden="true">${i === 0 ? '⌂' : '▤'}</span><span>${esc(p.title)}</span></button><button class="vb-icon" data-page-options="${p.id}" title="Настройки страницы ${esc(p.title)}" aria-label="Настройки страницы ${esc(p.title)}">⋯</button></div>`).join('');
    $('#vb-layers').innerHTML = p.blocks.map((b,i) => `<div class="vb-layer ${b.id === selected ? 'active' : ''}" draggable="true" data-layer="${b.id}" role="button" tabindex="0" aria-label="${i+1}. ${esc(config.catalog[b.type].label)}"><span class="vb-layer-symbol" aria-hidden="true">${symbols[b.type]}</span><span class="vb-layer-label">${esc(config.catalog[b.type].label)}<small>${esc(b.props.title || b.props.brand || 'Блок страницы')}</small></span><span class="vb-grip" aria-hidden="true">⠿</span></div>${b.type==='canvas'&&b.id===selected?free.layers(b):''}`).join('') || '<p class="vb-control-note" style="padding:10px">Здесь появится структура вашей страницы.</p>';
    $('[data-action=undo]').disabled = !undoStack.length; $('[data-action=redo]').disabled = !redoStack.length;
    const quick=$('#vf-quick-add');quick.hidden=!free.active()||previewMode;quick.innerHTML=free.active()?free.toolbar():'';
    if (inspect) renderInspector();
    updateSelection();
  }
  function selectBlock(id, {scroll = false, preserveFree = false} = {}) {
    if(!preserveFree)free.clear(); selected = id;
    if (panel === 'theme') setPanel('content'); else refresh();
    if (scroll) {
      const el = frame.contentDocument?.querySelector(`[data-block-id="${id}"]`);
      if (el) { const scale = parseFloat(frame.dataset.scale || 1); stage.scrollTo({top: Math.max(0, el.offsetTop * scale - 35), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'}); }
    }
  }
  function updateSelection() {
    const doc = frame.contentDocument;
    if (!doc?.body) return;
    doc.querySelectorAll('[data-block-id]').forEach(el => {
      el.classList.toggle('vb-selected', !previewMode && el.dataset.blockId === selected);
      el.querySelector('.vb-block-tools')?.remove();el.querySelector('.vf-floating-bar')?.remove();
      if (el.dataset.blockId === selected && !previewMode) {
        const tools = doc.createElement('div'); tools.className = 'vb-block-tools';
        tools.innerHTML = `<span>${esc(config.catalog[el.dataset.blockType].label)}</span><button data-tool="up" title="Блок выше" aria-label="Блок выше">↑</button><button data-tool="down" title="Блок ниже" aria-label="Блок ниже">↓</button><button data-tool="duplicate" title="Копировать блок" aria-label="Копировать блок">⧉</button><button data-tool="add-after" title="Добавить блок ниже" aria-label="Добавить блок ниже">＋</button><button data-tool="delete" title="Удалить блок" aria-label="Удалить блок">×</button>`;
        tools.addEventListener('click', event => { const action = event.target.closest('button')?.dataset.tool; if (action) {event.stopPropagation(); blockAction(action);} });
        el.append(tools);
      }
    });
    free.updateSelection();
  }
  function scheduleRender(ms = 450) {
    clearTimeout(renderTimer);
    renderTimer = setTimeout(() => renderCanvas().catch(error => { if (error.name !== 'AbortError') showError(error.message); }), ms);
  }
  async function renderCanvas() {
    // Do not replace the focused text node while someone is typing on the canvas.
    if (frame.contentDocument?.activeElement?.isContentEditable && !previewMode) return;
    renderController?.abort(); renderController = new AbortController();
    const serial = ++previewSerial;
    const data = await request(config.urls.preview, {document: documentState, page: currentPage}, {signal: renderController.signal});
    if (serial !== previewSerial) return;
    const scroll = stage.scrollTop;
    frame.onload = () => {
      if (serial !== previewSerial) return;
      const doc = frame.contentDocument;
      if (!doc?.body) return;
      const style = doc.createElement('style');
      style.textContent = `html{scroll-behavior:auto!important}html,body{overflow:clip!important}body{min-height:400px}[data-block-id]{position:relative;outline-offset:-2px}${!previewMode ? '[data-block-id]{cursor:default}[data-block-id]:hover{outline:1px solid #147d7360}[data-bb-text]{cursor:text}[data-bb-text]:hover{box-shadow:inset 0 -1px #147d7350}[contenteditable]:focus{outline:2px solid #147d73!important;outline-offset:6px;min-width:15px}' : ''}.vb-selected{outline:2px solid #147d73!important;z-index:1}.vb-block-tools{position:absolute;top:8px;right:8px;display:flex;gap:2px;background:#147d73;color:#fff;z-index:20;padding:3px;border-radius:6px;box-shadow:0 2px 8px #0001;font:11px/1.4 system-ui;letter-spacing:0;align-items:center}.vb-block-tools>span{padding:0 9px}.vb-block-tools button{border:0;background:transparent;color:#fff;border-radius:3px;padding:4px 7px;cursor:pointer;font:16px/1.4 system-ui}.vb-block-tools button:hover{background:#ffffff25}`;
      doc.head.append(style);
      doc.addEventListener('click', event => {
        const link = event.target.closest('a');
        if (link) {
          event.preventDefault();
          if (previewMode) {
            const raw = link.getAttribute('href');
            const targetPage = documentState.pages.find(p => path(p) === raw?.split('#')[0]);
            if (targetPage) switchPage(targetPage.id,raw.split('#')[1]||'');
            else if (raw?.startsWith('#')) doc.getElementById(raw.slice(1))?.scrollIntoView();
            else if (/^(https:\/\/|mailto:|tel:)/.test(raw || '')) window.open(raw, '_blank', 'noopener,noreferrer');
            return;
          }
        }
        if (!previewMode && !event.target.closest('.vb-block-tools,.vf-floating-bar,[data-free-id]') && !event.target.isContentEditable) {
          const el = event.target.closest('[data-block-id]'); if (el) selectBlock(el.dataset.blockId);
        }
      });
      if (!previewMode) doc.querySelectorAll('[data-bb-text]').forEach(el => {
        const id = el.closest('[data-block-id]').dataset.blockId, key = el.dataset.bbText;
        el.setAttribute('contenteditable', 'plaintext-only');
        el.setAttribute('spellcheck', 'false');
        el.addEventListener('focus', () => selectBlock(id));
        el.addEventListener('input', () => {
          const editedBlock = page().blocks.find(b => b.id === id);
          if (!editedBlock) return;
          const value = el.innerText.slice(0, key === 'text' ? 5000 : 300);
          mutate(() => { editedBlock.props[key] = value; }, {group: `inline:${id}:${key}`});
          const input = inspector.querySelector(`[data-prop="${key}"]:not([data-item])`); if (input) input.value = value;
        });
        el.addEventListener('blur', () => { scheduleRender(0); renderInspector(); });
      });
      doc.addEventListener('paste',handlePaste);
      free.bind(doc,previewMode);
      doc.addEventListener('keydown', shortcuts);
      resizeObserver?.disconnect(); resizeObserver = new ResizeObserver(() => fitCanvas()); resizeObserver.observe(doc.body);
      fitCanvas(); stage.scrollTop = scroll; updateSelection(); $('#vb-loading').hidden = true;
      if(pendingPageAnchor){doc.getElementById(pendingPageAnchor)?.scrollIntoView();pendingPageAnchor='';}
    };
    frame.srcdoc = data.html;
  }
  function fitCanvas() {
    const width = device === 'mobile' ? 390 : 1120;
    const available = stage.clientWidth - (innerWidth <= 680 ? 28 : innerWidth <= 1180 ? 36 : innerWidth >= 1600 ? 80 : 56);
    const scale = Math.min(1, Math.max(.15, available / width));
    frame.style.width = width + 'px';
    const body = frame.contentDocument?.body;
    // scrollHeight rounds to an integer; at fractional zoom the body can be
    // slightly taller. Only the outer stage owns scrolling, including on shrink.
    if (body) naturalHeight = Math.max(500, Math.ceil(Math.max(body.scrollHeight,body.getBoundingClientRect().height)));
    frame.style.height = naturalHeight + 'px'; frame.style.transform = `scale(${scale})`; frame.dataset.scale = scale;
    scaled.style.width = width * scale + 'px'; scaled.style.height = naturalHeight * scale + 'px';
    $('#vb-zoom').textContent = Math.round(scale * 100) + '%';
  }
  function setPanel(value) {
    panel = value; root.dataset.panel = value;
    $$('[data-inspector]').forEach(el => {const active = el.dataset.inspector === value; el.classList.toggle('active', active); el.setAttribute('aria-selected', active);});
    renderInspector();
  }
  function field(key, value, item = null) {
    const attrs = `data-prop="${key}"${item === null ? '' : ` data-item="${item}"`}`;
    const label = config.labels[key];
    if (key === 'image') {
      const asset = config.assets.find(a => a.id === value);
      return `<label class="vb-field"><span>${esc(label)}</span></label><div class="vb-image-field">${asset ? `<img src="${esc(asset.url)}" alt="${esc(asset.name)}">` : '<div class="vb-image-placeholder" aria-hidden="true">▧</div>'}<div><button class="vb-btn" data-choose-image="${item === null ? 'block' : item}">${asset ? 'Заменить' : 'Выбрать изображение'}</button>${asset ? `<button class="vb-icon" data-remove-image="${item === null ? 'block' : item}" aria-label="Убрать изображение">×</button>` : ''}</div></div>`;
    }
    return `<label class="vb-field"><span>${esc(label)}</span>${['text','title'].includes(key) ? `<textarea ${attrs} maxlength="${key === 'text' ? 5000 : 300}" rows="${key === 'text' ? 4 : 2}">${esc(value)}</textarea>` : `<input ${attrs} value="${esc(value)}" maxlength="${key === 'link' ? 1000 : 300}" ${key === 'link' ? 'placeholder="https://, /страница/ или #якорь"' : ''}>`}</label>`;
  }
  const select = (key, label, value, choices, theme = false) => `<label class="vb-field"><span>${esc(label)}</span><select ${theme ? 'data-theme' : 'data-style'}="${key}">${choices.map(([v,l]) => `<option value="${v}" ${v === value ? 'selected' : ''}>${l}</option>`).join('')}</select></label>`;
  function renderInspector() {
    const b = block();
    if (panel === 'theme') {
      const theme = documentState.theme;
      inspector.innerHTML = `<h2>Стиль всего сайта</h2><p class="vb-subtitle">Одна система для всех страниц</p>` + [['accent','Акцент'],['background','Фон страницы'],['ink','Текст']].map(([key,label]) => `<label class="vb-field"><span>${label}</span><div class="vb-color-row"><input type="color" data-theme="${key}" value="${theme[key]}" aria-label="${label}"><input data-color-text="${key}" value="${theme[key]}" maxlength="7" aria-label="${label} в формате HEX"></div></label>`).join('') + select('font','Типографика',theme.font,[['modern','Современная'],['editorial','Редакционная'],['classic','Классическая']],true) + select('radius','Скругления',theme.radius,[['sharp','Строгие'],['soft','Мягкие'],['round','Выразительные']],true) + `<div class="vb-separator"></div><h2>Ваш сайт</h2><p class="vb-control-note">${documentState.pages.length} страниц · ${documentState.pages.reduce((n,p) => n+p.blocks.length,0)} блоков</p><button class="vb-btn" data-action="media-library">▧ Библиотека изображений</button><div class="vb-separator"></div><button class="vb-btn" data-action="download">↓ Скачать сайт</button><p class="vb-control-note">ZIP с HTML-страницами и изображениями. Можно разместить на другом хостинге.</p><button class="vb-btn" data-action="go-bruno">Продолжить с Бруно ↗</button><p class="vb-control-note">Сохранённые блоки можно доработать через диалог лаборатории.</p><button class="vb-btn" data-action="snapshot">Сохранить версию</button><p class="vb-control-note">Версия появится в истории лаборатории. Публикация обновляется отдельно.</p>`;
      return;
    }
    if (!b) {
      inspector.innerHTML = `<div class="vb-empty-inspector"><span aria-hidden="true">✳</span><h2>Место для вашей идеи</h2><p>Выберите блок на странице,<br>чтобы изменить его содержимое<br>и оформление.</p><button class="vb-btn vb-primary" data-action="catalog">＋ Добавить блок</button></div>`; return;
    }
    const def = config.catalog[b.type];
    let html = `<h2>${esc(def.label)}</h2><p class="vb-subtitle">${esc(def.caption)}</p>`;
    if (b.type==='canvas'){inspector.innerHTML=free.inspectorHTML();return;}
    if (panel === 'style') {
      html += select('surface','Цвет секции',b.style.surface,[['base','Фон сайта'],['white','Белый'],['accent','Акцентный'],['dark','Тёмный']]);
      html += select('align','Выравнивание заголовка',b.style.align,[['left','По левому краю'],['center','По центру']]);
      html += select('spacing','Вертикальные отступы',b.style.spacing,[['compact','Компактно'],['normal','Обычно'],['airy','Больше воздуха']]);
      if (b.type === 'hero') html += select('variant','Композиция',b.style.variant,[['split','Текст и изображение'],['center','По центру']]);
      html += '<p class="vb-control-note">Цвета, шрифт и скругления всех блоков можно изменить во вкладке «Сайт».</p>';
    } else {
      html += def.fields.map(key => field(key, b.props[key] || '')).join('');
      if (def.items) {
        html += '<div class="vb-separator"></div><label class="vb-field"><span>Элементы блока</span></label>';
        html += b.props.items.map((item,i) => `<details class="vb-item" ${b.props.items.length === 1 ? 'open' : ''}><summary>${esc(item.title || `Элемент ${i+1}`)}</summary><div class="vb-item-body">${Object.keys(def.props.items[0]).map(key => field(key,item[key] || '',i)).join('')}<div class="vb-item-actions"><button data-item-move="${i}" data-dir="-1" aria-label="Элемент выше">↑ Выше</button><button data-item-move="${i}" data-dir="1" aria-label="Элемент ниже">↓ Ниже</button><button data-item-delete="${i}">Удалить</button></div></div></details>`).join('');
        html += `<button class="vb-btn" data-action="add-item" ${b.props.items.length >= 12 ? 'disabled' : ''}>＋ Добавить элемент</button>`;
      }
      if (def.fields.includes('link') || def.items) html += '<p class="vb-control-note">Для перехода на страницу используйте её адрес: /about/. Для секции — #идентификатор.</p>';
      if (b.type === 'pricing') html += '<p class="vb-control-note">Кнопки тарифов ведут по указанным ссылкам. Приём платежей подключается отдельно.</p>';
    }
    html += `<div class="vb-separator"></div><button class="vb-btn vf-convert" data-action="free-convert">◇ Свободное размещение</button><p class="vb-control-note">Перенести содержимое в свободный блок, чтобы двигать каждый элемент. Отмена доступна через ⌘/Ctrl+Z.</p><div class="vb-block-actions"><button class="vb-btn" data-action="block-up" aria-label="Переместить блок выше">↑</button><button class="vb-btn" data-action="block-down" aria-label="Переместить блок ниже">↓</button><button class="vb-btn" data-action="block-duplicate">⧉ Копия</button><button class="vb-btn vb-danger" data-action="block-delete">Удалить</button></div><p class="vb-control-note">Якорь секции: <code>#${b.id}</code></p>`;
    inspector.innerHTML = html;
  }
  function blockAction(action) {
    const b = block(); if (!b) return;
    const blocks = page().blocks, index = blocks.indexOf(b);
    if (action === 'add-after') { pendingInsertion = index + 1; openCatalog(); return; }
    if (action === 'duplicate' && documentState.pages.reduce((n,p) => n+p.blocks.length,0) >= 100) {toast('На сайте может быть до 100 блоков.');return;}
    if(action==='duplicate'&&documentState.pages.reduce((n,p)=>n+p.blocks.reduce((n,b)=>n+(b.props.elements?.length||0),0),0)+(b.props.elements?.length||0)>250){toast('На сайте может быть до 250 свободных элементов.');return;}
    mutate(() => {
      if (action === 'up' && index > 0) [blocks[index-1],blocks[index]] = [blocks[index],blocks[index-1]];
      if (action === 'down' && index < blocks.length-1) [blocks[index+1],blocks[index]] = [blocks[index],blocks[index+1]];
      if (action === 'duplicate') {const copy = clone(b); copy.id = uid();if(copy.type==='canvas')copy.props.elements.forEach(e=>e.id=uid()); blocks.splice(index+1,0,copy);selected=copy.id;}
      if (action === 'delete') {blocks.splice(index,1);selected=blocks[Math.min(index,blocks.length-1)]?.id || null;}
    }, {inspect:true,immediate:true});
    if (action === 'delete') toast('Блок удалён. ⌘Z — отменить.');
  }
  function openDialog(title, eyebrow, html, kind) {
    $('#vb-dialog-title').textContent = title; $('#vb-dialog-eyebrow').textContent = eyebrow;
    $('#vb-dialog-body').innerHTML = html; dialogKind = kind;
    if (!dialog.open) dialog.showModal();
  }
  function closeDialog() {dialog.close();dialogKind='';}
  function openCatalog(filter='Все') {
    const groups = ['Все',...new Set(Object.values(config.catalog).map(def=>def.group))];
    const cards = Object.entries(config.catalog).filter(([,def]) => filter === 'Все' || def.group === filter).map(([type,def]) => `<button class="vb-catalog-card" data-add-block="${type}"><div class="vb-thumb vb-thumb-${type}" aria-hidden="true">${['features','pricing','gallery'].includes(type) ? '<b></b><b></b><b></b>' : '<div class="vb-thumb-lines"><i></i><i></i><i></i></div><div class="vb-thumb-art"></div>'}</div><strong>${esc(def.label)}</strong><small>${esc(def.caption)}</small></button>`).join('');
    openDialog('Из чего начнём?', 'БИБЛИОТЕКА БЛОКОВ', `<div class="vb-catalog-nav">${groups.map(g=>`<button data-catalog-filter="${g}" class="${g === filter ? 'active' : ''}">${g}</button>`).join('')}</div><div class="vb-catalog-grid">${cards}</div>`, 'catalog');
  }
  function addBlock(type) {
    if (documentState.pages.reduce((n,p) => n+p.blocks.length,0) >= 100) {toast('На сайте может быть до 100 блоков.');return;}
    const b = {id:uid(),type,props:clone(config.catalog[type].props),style:{surface:'base',align:'left',spacing:'normal',variant:'split'}};
    if (type === 'cta') b.style.surface = 'accent';
    mutate(() => { const at = pendingInsertion ?? (selected ? page().blocks.findIndex(b => b.id === selected)+1 : page().blocks.length); page().blocks.splice(at,0,b);selected=b.id; }, {inspect:true,immediate:true});
    pendingInsertion=null;closeDialog();root.classList.remove('is-left-open');toast('Блок добавлен');
    setTimeout(() => selectBlock(b.id,{scroll:true}),600);
  }
  function openPageSettings(id = currentPage, create = false) {
    if (create && documentState.pages.length >= 20) {toast('На сайте может быть до 20 страниц.');return;}
    const p = documentState.pages.find(p => p.id === id);
    const home = !create && p === documentState.pages[0];
    openDialog(create ? 'Новая страница' : 'Настройки страницы', 'СТРУКТУРА САЙТА', `<form id="vb-page-form" class="vb-page-form" data-edit-page="${create ? '' : p.id}"><label class="vb-field"><span>Название страницы</span><input name="title" class="vb-input" value="${create ? '' : esc(p.title)}" maxlength="100" required placeholder="Например, о проекте"></label><label class="vb-field"><span>Адрес страницы</span><input name="slug" class="vb-input" value="${create ? '' : esc(p.slug)}" maxlength="60" ${home ? 'disabled placeholder="/ — главная страница"' : 'required pattern="[a-z0-9][a-z0-9-]*" placeholder="about"'}></label><label class="vb-field"><span>Описание для поисковиков</span><textarea name="description" rows="3" maxlength="300">${create ? '' : esc(p.description)}</textarea></label><p class="vb-control-note">Адрес — латинскими буквами, цифрами и дефисами. Ссылка будет выглядеть так: /about/.</p><div class="vb-page-form-actions"><button type="submit" class="vb-btn vb-primary">${create ? 'Создать страницу' : 'Сохранить'}</button>${!create ? `<button type="button" class="vb-btn" data-duplicate-page="${p.id}">Копия</button>${!home ? `<button type="button" class="vb-btn" data-delete-page="${p.id}">Удалить страницу</button>` : ''}` : ''}</div></form>`, 'page');
  }
  function pageForm(form) {
    const title = form.elements.title.value.trim(), slug = form.elements.slug.disabled ? '' : form.elements.slug.value.trim(), id = form.dataset.editPage;
    if (!title || (slug && !/^[a-z0-9][a-z0-9-]{0,59}$/.test(slug)) || ['assets','admin','api','index','favicon','robots'].includes(slug)) {toast('Проверьте название и адрес страницы.');return;}
    if (documentState.pages.some(p => p.id !== id && p.slug === slug)) {toast('Этот адрес уже используется.');return;}
    const description = form.elements.description.value;
    mutate(() => {
      if (id) {
        const target=documentState.pages.find(p=>p.id===id), oldPath=path(target);
        Object.assign(target,{title,slug,description});
        const newPath=path(target);
        if(oldPath!==newPath) documentState.pages.forEach(p=>p.blocks.forEach(b=>[b.props,...(b.props.items||[]),...(b.props.elements||[]).map(e=>e.content)].forEach(props=>{
          if(typeof props.link==='string' && props.link.split('#')[0]===oldPath) props.link=newPath+(props.link.includes('#')?'#'+props.link.split('#').slice(1).join('#'):'');
        })));
      }
      else {const p={id:uid(),title,slug,description,blocks:[]};documentState.pages.push(p);currentPage=p.id;selected=null;}
    },{inspect:true,immediate:true});
    closeDialog();if (!id) {pendingInsertion=null;openCatalog();}
  }
  function uniqueSlug(base) {let slug=base.slice(0,50)||'page',n=2;while(documentState.pages.some(p=>p.slug===slug))slug=`${base.slice(0,45)||'page'}-${n++}`;return slug;}
  function duplicatePage(id) {
    if (documentState.pages.length>=20){toast('На сайте может быть до 20 страниц.');return;}
    const original = documentState.pages.find(p=>p.id===id);
    if(documentState.pages.reduce((n,p)=>n+p.blocks.length,0)+original.blocks.length>100){toast('На сайте может быть до 100 блоков.');return;}
    if(documentState.pages.reduce((n,p)=>n+p.blocks.reduce((n,b)=>n+(b.props.elements?.length||0),0),0)+original.blocks.reduce((n,b)=>n+(b.props.elements?.length||0),0)>250){toast('На сайте может быть до 250 свободных элементов.');return;}
    mutate(()=>{const copy=clone(original);copy.id=uid();copy.slug=uniqueSlug(original.slug||'home');copy.title=(original.title+' · копия').slice(0,100);const ids=new Map();copy.blocks.forEach(b=>{const old=b.id;b.id=uid();ids.set(old,b.id);if(b.type==='canvas')b.props.elements.forEach(e=>{const old=e.id;e.id=uid();ids.set(old,e.id);});});copy.blocks.forEach(b=>[b.props,...(b.props.items||[]),...(b.props.elements||[]).map(e=>e.content)].forEach(props=>{if(typeof props.link==='string'&&props.link.startsWith('#')&&ids.has(props.link.slice(1)))props.link='#'+ids.get(props.link.slice(1));}));documentState.pages.push(copy);currentPage=copy.id;selected=null;},{inspect:true,immediate:true});closeDialog();
  }
  function openMedia(target = null) {
    imageTarget = target;
    openDialog('Ваши изображения', 'БИБЛИОТЕКА ПРОЕКТА', `<div class="vb-media-top"><p>JPG, PNG или WebP · до 5 МБ<br>Используйте свои фотографии и иллюстрации.</p><button class="vb-btn vb-primary" data-action="upload">↑ Загрузить</button></div>${config.assets.length ? `<div class="vb-media-grid">${config.assets.map(a=>`<button class="vb-media-card" data-asset="${a.id}"><img src="${esc(a.url)}" alt="${esc(a.name)}" loading="lazy"><span>${esc(a.name)}</span><small>${a.width} × ${a.height} · ${Math.round(a.size/1024)} КБ</small></button>`).join('')}</div>` : '<div class="vb-media-empty">Здесь пока пусто.<br>Загрузите первое изображение — оно останется в библиотеке проекта.</div>'}`, 'media');
  }
  function useAsset(id) {
    if (free.assignImage(imageTarget,id)){closeDialog();toast('Изображение добавлено');return;}
    if (!imageTarget) {toast('Выберите блок с изображением, чтобы вставить файл.');return;}
    const target=imageTarget, b=documentState.pages.flatMap(p=>p.blocks).find(b=>b.id===target.block);
    if (!b) return;
    mutate(()=>{const props=target.item===null?b.props:b.props.items[target.item];props.image=id;if(!props.alt)props.alt=config.assets.find(a=>a.id===id)?.name.replace(/\.[^.]+$/,'')||'';},{inspect:true,immediate:true});closeDialog();toast('Изображение добавлено');
  }
  async function storeImage(file){
    if(file.size>5*1024*1024)throw new Error('Выберите изображение размером до 5 МБ.');
    const form=new FormData();form.append('image',file);
    const asset=await request(config.urls.upload,form,{form:true});
    if(!config.assets.some(a=>a.id===asset.id))config.assets.unshift(asset);
    return asset;
  }
  async function uploadImage(file) {
    if (!file) return;
    const uploadButton=$('[data-action=upload]');if(uploadButton){uploadButton.disabled=true;uploadButton.textContent='Загружаем…';}
    try {const data=await storeImage(file);if(imageTarget)useAsset(data.id);else{openMedia();toast('Изображение загружено');}}
    catch(error){showError(error.message);if(uploadButton){uploadButton.disabled=false;uploadButton.textContent='↑ Загрузить';}}
    finally{$('#vb-upload').value='';}
  }
  function handlePaste(event){
    if(previewMode||dialog.open||event.defaultPrevented)return;
    if(event.target.ownerDocument===document&&event.target!==document.body&&!root.contains(event.target))return;
    if(clipboard.editing(event.target))return;
    const plan=clipboard.inspect(event.clipboardData,event.target.ownerDocument||document);if(!plan)return;
    event.preventDefault();event.stopPropagation();
    if(plan.error){showError(plan.error);return;}
    const target={page:currentPage,block:selected,device,point:free.pastePoint()};
    pasteQueue=pasteQueue.then(async()=>{
      const items=[];
      if(plan.type==='image'){
        toast(plan.files.length>1?'Добавляем изображения…':'Добавляем изображение…');
        // Preserve the initiating page/position even if the user switches pages
        // while the upload runs. An upload never reuses the media dialog target.
        for(const file of plan.files){const asset=await storeImage(file);items.push({type:'image',asset});}
      }else items.push(plan);
      const p=documentState.pages.find(p=>p.id===target.page);
      if(!p)throw new Error('Страница для вставки удалена. Картинки остались в библиотеке проекта.');
      let b=p.blocks.find(b=>b.id===target.block&&b.type==='canvas');const create=!b;
      if(!b)b={id:uid(),type:'canvas',props:clone(config.catalog.canvas.props),style:{surface:'base',align:'left',spacing:'normal',variant:'split'}};
      if(create&&documentState.pages.reduce((n,p)=>n+p.blocks.length,0)>=100)throw new Error('На сайте уже 100 блоков. Выберите существующий свободный блок.');
      const count=documentState.pages.reduce((n,p)=>n+p.blocks.reduce((n,b)=>n+(b.props.elements?.length||0),0),0);
      if(b.props.elements.length+items.length>60||count+items.length>250)throw new Error('Недостаточно места для вставки: до 60 элементов в блоке и 250 на сайте.');
      free.insertClipboard(b,items,target,()=>{
        if(create){const index=p.blocks.findIndex(b=>b.id===target.block);p.blocks.splice(index<0?p.blocks.length:index+1,0,b);}
        if(currentPage===p.id)selected=b.id;
      });
      if(currentPage===p.id){root.classList.remove('is-left-open');if(panel==='theme')setPanel('content');}
      hideError();toast(currentPage!==p.id?'Добавлено на страницу «'+p.title+'»':create?'Добавлено в новый свободный блок':items.length>1?'Изображения вставлены':plan.type==='image'?'Изображение вставлено':plan.type==='button'?'Ссылка вставлена как кнопка':'Текст вставлен');
    }).catch(error=>showError(error.message));
  }
  async function busy(button, work) {
    const old=button?.innerHTML;
    if(button){button.disabled=true;button.textContent='Подождите…';}
    try {await work();}catch(error){showError(error.message);}finally{if(button){button.disabled=false;button.innerHTML=old;}}
  }
  async function snapshot() {
    await flushSave();const data=await request(config.urls.snapshot,{revision});config.sourceVersion=data.version;return data;
  }
  async function publish() {
    const result=await snapshot();const form=new FormData();form.append('version',result.version);form.append('action','publish');
    const data=await request(config.hostingUrl,form,{form:true});hostedURL=data.url;
    openDialog('Сайт опубликован', 'BRUNOHOST', `<div class="vb-publish-success"><div class="vb-success-mark">✓</div><h3>Можно делиться с миром.</h3><p>Все страницы и изображения доступны по ссылке.<br>Следующие правки останутся в черновике до новой публикации.</p><div class="vb-public-url"><a href="${esc(data.url)}" target="_blank" rel="noopener">${esc(data.url)}</a><button class="vb-btn" data-copy-url="${esc(data.url)}">Копировать</button></div><a class="vb-btn vb-primary" href="${esc(data.url)}" target="_blank" rel="noopener">Открыть сайт ↗</a><button class="vb-btn" data-action="close-dialog">Продолжить редактирование</button></div>`,'publish');toast('Сайт опубликован');
  }
  function downloadBlob(content,name,type) {const blob=new Blob([content],{type}), url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
  async function action(name,button) {
    if(name==='free-convert')return busy(button,()=>free.convert());
    if(name.startsWith('block-'))return blockAction(name.slice(6));
    if(name==='undo'||name==='redo')return history(name);
    if(name==='catalog'){pendingInsertion=null;return openCatalog();}
    if(name==='close-dialog')return closeDialog();
    if(name==='add-page')return openPageSettings(currentPage,true);
    if(name==='page-settings')return openPageSettings();
    if(name==='pages'){root.classList.toggle('is-left-open');root.classList.remove('is-right-open');return;}
    if(name==='settings'){root.classList.toggle('is-right-open');root.classList.remove('is-left-open');return;}
    if(name==='media-library')return openMedia();
    if(name==='upload')return $('#vb-upload').click();
    if(name==='backup')return downloadBlob(JSON.stringify(documentState,null,2),'brunolab-draft.json','application/json');
    if(name==='retry')return busy(button,async()=>{await flushSave();await renderCanvas();hideError();});
    if(name==='download')return busy(button,async()=>{await flushSave();const a=document.createElement('a');a.href=config.urls.download;a.download='brunolab-site.zip';a.click();});
    if(name==='go-bruno')return busy(button,async()=>{const data=await snapshot();location.href=data.url;});
    if(name==='snapshot')return busy(button,async()=>{await snapshot();toast('Версия сохранена в истории лаборатории');});
    if(name==='publish')return busy(button,publish);
    if(name==='preview'){previewMode=!previewMode;$('#vf-quick-add').hidden=previewMode||!free.active();root.classList.toggle('is-preview',previewMode);root.classList.remove('is-left-open','is-right-open');scheduleRender(0);requestAnimationFrame(fitCanvas);toast(previewMode?'Режим просмотра · Escape — вернуться':'Вернулись в редактор');return;}
    if(name==='add-item'){const b=block();if(!b||b.props.items.length>=12)return;mutate(()=>b.props.items.push(clone(config.catalog[b.type].props.items[0])),{inspect:true});}
  }
  root.addEventListener('click', event => {
    const el=event.target.closest('button,a[data-action]');if(!el)return;
    const d=el.dataset;
    if(d.action){event.preventDefault();action(d.action,el);return;}
    if(d.page)return switchPage(d.page);
    if(d.pageOptions)return openPageSettings(d.pageOptions);
    if(d.inspector)return setPanel(d.inspector);
    if(d.device){free.cancel();device=d.device;$$('[data-device]').forEach(b=>{b.classList.toggle('active',b===el);b.setAttribute('aria-pressed',b===el);});if(free.active()){scheduleRender(0);renderInspector();}fitCanvas();return;}
    if(d.freeSelect){selectBlock(d.freeBlock,{preserveFree:true});free.select(d.freeSelect);return;}
    if(d.freeAdd)return free.add(d.freeAdd);
    if(d.freeAction)return free.action(d.freeAction);
    if(d.freeAlign)return free.align(d.freeAlign);
    if(d.catalogFilter)return openCatalog(d.catalogFilter);
    if(d.addBlock)return addBlock(d.addBlock);
    if(d.duplicatePage)return duplicatePage(d.duplicatePage);
    if(d.deletePage){mutate(()=>{documentState.pages=documentState.pages.filter(p=>p.id!==d.deletePage);currentPage=documentState.pages[0].id;selected=null;},{inspect:true,immediate:true});closeDialog();toast('Страница удалена. ⌘Z — отменить.');return;}
    if(d.chooseImage!==undefined)return openMedia({block:selected,item:d.chooseImage==='block'?null:Number(d.chooseImage)});
    if(d.removeImage!==undefined){const b=block();mutate(()=>{const props=d.removeImage==='block'?b.props:b.props.items[Number(d.removeImage)];props.image='';},{inspect:true});return;}
    if(d.asset)return useAsset(d.asset);
    if(d.itemDelete!==undefined){mutate(()=>block().props.items.splice(Number(d.itemDelete),1),{inspect:true});return;}
    if(d.itemMove!==undefined){const i=Number(d.itemMove),j=i+Number(d.dir),items=block().props.items;if(j>=0&&j<items.length)mutate(()=>[items[i],items[j]]=[items[j],items[i]],{inspect:true});return;}
    if(d.copyUrl){navigator.clipboard.writeText(d.copyUrl).then(()=>toast('Ссылка скопирована')).catch(()=>showError('Скопируйте ссылку из адресной строки открытого сайта.'));}
  });
  $('.vb-canvas').addEventListener('click',event=>{
    if(previewMode||dialog.open||event.defaultPrevented||!selected)return;
    // Iframe clicks stay inside its document. Here only the surrounding desk
    // clears selection; toolbar buttons and other controls keep their actions.
    if(event.target.closest('button,a,input,textarea,select,[contenteditable],[role="button"],#vf-quick-add'))return;
    free.clear();selected=null;refresh();
  });
  $('#vb-layers').addEventListener('click',event=>{const layer=event.target.closest('[data-layer]');if(layer)selectBlock(layer.dataset.layer,{scroll:true});});
  $('#vb-layers').addEventListener('keydown',event=>{const layer=event.target.closest('[data-layer]');if(layer&&['Enter',' '].includes(event.key)){event.preventDefault();selectBlock(layer.dataset.layer,{scroll:true});}});
  $('#vb-layers').addEventListener('dragstart',event=>{dragID=event.target.closest('[data-layer]')?.dataset.layer;if(dragID){event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',dragID);}});
  $('#vb-layers').addEventListener('dragover',event=>{if(!dragID)return;event.preventDefault();$$('.vb-layer').forEach(el=>el.classList.remove('drag-over'));event.target.closest('[data-layer]')?.classList.add('drag-over');});
  $('#vb-layers').addEventListener('drop',event=>{event.preventDefault();const target=event.target.closest('[data-layer]')?.dataset.layer;if(target&&dragID&&target!==dragID){const blocks=page().blocks,from=blocks.findIndex(b=>b.id===dragID),to=blocks.findIndex(b=>b.id===target);if(from>=0&&to>=0)mutate(()=>{const [moved]=blocks.splice(from,1);blocks.splice(to,0,moved);},{inspect:true,immediate:true});}dragID=null;$$('.vb-layer').forEach(el=>el.classList.remove('drag-over'));});
  $('#vb-layers').addEventListener('dragend',()=>{dragID=null;$$('.vb-layer').forEach(el=>el.classList.remove('drag-over'));});
  $('.vb-inspector-tabs').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const tabs=$$('[data-inspector]'),at=tabs.indexOf(event.target);if(at<0)return;const next=tabs[(at+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length];setPanel(next.dataset.inspector);next.focus();});
  inspector.addEventListener('input',event=>{
    const el=event.target,d=el.dataset,b=block();
    if(Object.keys(d).some(k=>k.startsWith('free'))){free.input(el);return;}
    if(d.prop&&b){mutate(()=>{const props=d.item===undefined?b.props:b.props.items[Number(d.item)];props[d.prop]=el.value;},{group:`prop:${b.id}:${d.item||''}:${d.prop}`});}
    if(d.theme){mutate(()=>documentState.theme[d.theme]=el.value,{group:`theme:${d.theme}`});const other=inspector.querySelector(`[data-color-text="${d.theme}"]`);if(other)other.value=el.value;}
    if(d.colorText&&/^#[0-9a-fA-F]{6}$/.test(el.value)){mutate(()=>documentState.theme[d.colorText]=el.value,{group:`theme:${d.colorText}`});inspector.querySelector(`[data-theme="${d.colorText}"]`).value=el.value;}
    if(d.style&&b)mutate(()=>b.style[d.style]=el.value,{group:`style:${b.id}:${d.style}`});
  });
  dialog.addEventListener('submit',event=>{if(event.target.id==='vb-page-form'){event.preventDefault();pageForm(event.target);}});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
  $('#vb-upload').addEventListener('change',event=>uploadImage(event.target.files[0]));
  function shortcuts(event) {
    const typing=event.target.matches?.('input,textarea,select,[contenteditable]');
    if(!previewMode&&!dialog.open&&free.keydown(event))return;
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='s'){event.preventDefault();event.stopPropagation();busy(null,async()=>{await flushSave();toast('Черновик сохранён');});return;}
    if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='z'&&!typing){event.preventDefault();event.stopPropagation();history(event.shiftKey?'redo':'undo');return;}
    if(event.key==='Escape'){if(dialog.open)return;if(previewMode)action('preview');else root.classList.remove('is-left-open','is-right-open');}
    if(event.key==='Delete'&&!typing&&!dialog.open&&selected&&!previewMode){event.preventDefault();blockAction('delete');}
  }
  document.addEventListener('paste',handlePaste);
  window.addEventListener('keydown',shortcuts);
  window.addEventListener('beforeunload',event=>{if(isDirty()){event.preventDefault();event.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'&&isDirty()&&!conflict)save().catch(()=>{});});
  new ResizeObserver(fitCanvas).observe(stage);
  $$('[data-device]').forEach(el=>{const active=el.dataset.device===device;el.classList.toggle('active',active);el.setAttribute('aria-pressed',active);});
  refresh();renderCanvas().catch(error=>showError(error.message));
  if(config.newBlockSite) toast('Открыт черновик блочного сайта. Предыдущие AI-версии сохранены в лаборатории.');
})();

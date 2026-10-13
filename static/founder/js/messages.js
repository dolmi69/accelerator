(() => {
  const box = document.querySelector('[data-messenger]');
  const threadId = box?.dataset.conversation || '';
  const myId = Number(box?.dataset.userId || 0);
  const list = document.querySelector('[data-direct-messages]');
  const scroller = document.querySelector('[data-direct-scroll]');
  const form = document.querySelector('[data-direct-form]');
  const input = form?.elements.content;
  const sendButton = form?.querySelector('button[type="submit"]');
  const draft = window.composerDrafts?.attach(input, threadId && myId ? `dm:${myId}:${threadId}` : '') || { clear() {}, active: false };
  const older = document.querySelector('[data-load-older]');
  const status = document.querySelector('[data-connection-status]');
  const errorBox = document.querySelector('[data-direct-error]');
  let socket, retry = 0, timer, ready = false, stopped = false;
  let blocked = box?.dataset.blocked === '1', lastId = 0, firstId = 0, lastRead = 0, peerRead = 0, loaded = false;
  const seen = new Set(), pending = new Map();
  let jump = null; // Search result that is older than the loaded history.
  const announce = () => list?.dispatchEvent(new CustomEvent('chat:updated'));
  function finishJump(element) {
    if (!jump) return;
    const done = jump; jump = null; clearTimeout(done.timer); done.resolve(element);
  }
  function requestJump() {
    // One request loads the whole gap down to the result (the server caps it at 500 rows).
    if (!send({type: 'sync', conversation: threadId, since: jump.id, ...(firstId ? {before: firstId} : {})})) finishJump(null);
  }
  function loadUntil(id) {
    const existing = list?.querySelector(`[data-id="${id}"]`);
    if (existing || !threadId || !Number.isInteger(id)) return Promise.resolve(existing || null);
    finishJump(null);
    return new Promise(resolve => {
      jump = {id, resolve, tries: 0, timer: setTimeout(() => finishJump(null), 15000)};
      requestJump();
    });
  }
  window.directChat = {loadUntil};
  const setError = text => { if (errorBox) { errorBox.textContent = text; errorBox.hidden = !text; } };
  const nearBottom = () => scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  const send = payload => { if (!ready || socket.readyState !== WebSocket.OPEN) return false; socket.send(JSON.stringify(payload)); return true; };
  // Files and photos: upload over POST, the message with their ids over the socket.
  const files = form && window.DirectAttachments?.composer(form, {onChange: () => composerState()});
  const composerState = () => {
    if (sendButton) sendButton.disabled = !ready || blocked || Boolean(files?.busy());
    if (input) input.disabled = blocked;
    files?.setDisabled(blocked);
  };
  const attachmentsLabel = message => {
    const items = message.attachments || [];
    if (!items.length) return '';
    return items.every(item => item.kind === 'image') ? (items.length > 1 ? `🖼 Фото (${items.length})` : '🖼 Фото')
      : (items.length > 1 ? `📎 Файлы (${items.length})` : `📎 ${items[0].name}`);
  };
  function countUnread(value) {
    if (!Number.isInteger(value)) return;
    document.querySelectorAll('[data-unread-count]').forEach(node => { node.textContent = value > 99 ? '99+' : value; node.hidden = value === 0; });
  }
  function readVisible() {
    if (threadId && !box.classList.contains('is-showing-list') && lastId > lastRead && document.visibilityState === 'visible' && nearBottom()) {
      if (send({type: 'read', conversation: threadId, id: lastId})) lastRead = lastId;
    }
  }
  function receipts() {
    list?.querySelectorAll('[data-own-message]').forEach(row => {
      row.querySelector('[data-delivery]').textContent = Number(row.dataset.id) <= peerRead ? 'Прочитано' : 'Отправлено';
    });
  }
  function appendMessage(message) {
    if (!list || message.conversation !== threadId || seen.has(message.id)) return;
    seen.add(message.id);
    lastId = Math.max(lastId, message.id); firstId = firstId ? Math.min(firstId, message.id) : message.id;
    const pendingItem = pending.get(message.client_id);
    pendingItem?.element.remove(); pending.delete(message.client_id);
    const own = message.sender_id === myId;
    const article = document.createElement('article');
    article.className = `direct-bubble ${own ? 'own' : ''}`; article.dataset.id = message.id;
    if (own) article.dataset.ownMessage = '1';
    if (message.attachments?.length && window.DirectAttachments) {
      article.classList.add('has-attachments');
      article.append(window.DirectAttachments.render(message.attachments));
    }
    if (message.content) { const content = document.createElement('p'); content.textContent = message.content; article.append(content); }
    const meta = document.createElement('div'); meta.className = 'direct-meta';
    const time = document.createElement('time'); time.dateTime = message.created_at;
    time.textContent = new Date(message.created_at).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
    meta.append(time);
    if (own) { const state = document.createElement('span'); state.dataset.delivery = '1'; meta.append(state); }
    article.append(meta);
    const following = [...list.children].find(node => node.dataset.id && Number(node.dataset.id) > message.id);
    list.insertBefore(article, following || list.querySelector('[data-pending]'));
    document.querySelector('[data-direct-empty]').hidden = true;
    receipts();
  }
  function updateThread(data) {
    const id = data.message?.conversation || data.conversation;
    if (!id) return;
    const row = document.querySelector(`[data-thread="${id}"]`);
    if (!row) { if (data.type === 'message') document.querySelector('[data-new-thread]')?.removeAttribute('hidden'); return; }
    if (data.message) row.querySelector('[data-thread-preview]').textContent = (data.message.content || attachmentsLabel(data.message)).slice(0, 65);
    if (Number.isInteger(data.thread_unread)) {
      const badge = row.querySelector('[data-thread-unread]'); badge.textContent = data.thread_unread; badge.hidden = !data.thread_unread;
    }
    if (data.type === 'message') row.parentElement.prepend(row);
  }
  function transmit(item) {
    if (send({type: 'send', conversation: threadId, client_id: item.clientId, content: item.content,
              ...(item.attachments.length ? {attachments: item.attachments.map(file => file.id)} : {})})) {
      item.failed = false; item.state.textContent = 'Отправляется…';
      item.element.querySelector('button')?.remove();
    }
  }
  function handle(data) {
    countUnread(data.unread); updateThread(data);
    if (data.type === 'ready') {
      ready = true; retry = 0; lastRead = 0; composerState();
      if (status) status.textContent = 'На связи · сообщения приходят сразу';
      if (threadId) send({type: 'sync', conversation: threadId, ...(loaded ? {after: lastId} : {})});
      pending.forEach(item => { if (!item.failed) transmit(item); });
    } else if (data.type === 'history' && data.conversation === threadId) {
      const oldHeight = scroller.scrollHeight, wasBottom = nearBottom(), initial = !loaded;
      const pagingBack = loaded && data.direction === 'before';
      data.messages.forEach(appendMessage); loaded = true;
      peerRead = Math.max(peerRead, data.peer_read_id); blocked = data.blocked; composerState(); receipts();
      if (data.direction === 'before') older.hidden = !data.has_more;
      if (data.direction === 'after' && data.has_more) send({type: 'sync', conversation: threadId, after: lastId});
      if (initial || (wasBottom && !pagingBack)) scroller.scrollTop = scroller.scrollHeight;
      else if (pagingBack) scroller.scrollTop += scroller.scrollHeight - oldHeight;
      older.disabled = false; readVisible(); announce();
      if (jump && data.direction === 'before') {
        const found = list.querySelector(`[data-id="${jump.id}"]`);
        if (found) finishJump(found);
        else if (data.has_more && ++jump.tries < 10) requestJump();
        else finishJump(null);
      }
    } else if (data.type === 'message' && data.message.conversation === threadId) {
      const bottom = nearBottom(), own = data.message.sender_id === myId;
      appendMessage(data.message);
      if (bottom || own) scroller.scrollTop = scroller.scrollHeight;
      readVisible(); announce();
    } else if (data.type === 'read' && data.conversation === threadId && data.user_id !== myId) {
      peerRead = Math.max(peerRead, data.id); receipts();
    } else if (data.type === 'ack') {
      // Direct acknowledgement also contains the saved row if a group event was lost.
      if (data.message) appendMessage(data.message);
    } else if (data.type === 'blocked' && data.conversation === threadId) {
      blocked = data.blocked; composerState();
      document.querySelector('[data-compose-help]').textContent = blocked ? 'Отправка сообщений недоступна.' : 'Enter — отправить · Shift + Enter — новая строка';
    } else if (data.type === 'error') {
      setError(data.message);
      const item = pending.get(data.client_id);
      if (item) {
        item.failed = true; item.state.textContent = 'Не отправлено';
        if (!item.element.querySelector('button')) {
          const button = document.createElement('button'); button.type = 'button'; button.className = 'link-button'; button.textContent = 'Повторить';
          button.addEventListener('click', () => transmit(item)); item.element.append(button);
        }
      }
    }
  }
  function connect() {
    if (stopped) return;
    clearTimeout(timer);
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/messages/`);
    socket.addEventListener('message', event => { try { handle(JSON.parse(event.data)); } catch (error) { setError('Не удалось обновить чат. Обновите страницу.'); } });
    socket.addEventListener('close', event => {
      ready = false; composerState();
      if (event.code === 4401 || event.code === 4403) {
        stopped = true;
        if (status) status.textContent = 'Войдите в аккаунт заново';
        setError('Сеанс завершён. Обновите страницу и войдите снова.'); return;
      }
      if (status) status.textContent = 'Восстанавливаем соединение…';
      if (!stopped) timer = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(retry++, 5)));
    });
  }
  form?.addEventListener('submit', event => {
    event.preventDefault(); setError('');
    const content = input.value.trim();
    if (files?.busy()) { setError('Дождитесь, пока файлы загрузятся.'); return; }
    const attachments = files?.ready() || [];
    if ((!content && !attachments.length) || blocked) return;
    if (!ready) { setError('Соединение восстанавливается. Ваш текст и файлы остаются в поле.'); return; }
    const clientId = crypto.randomUUID();
    const element = document.createElement('article'); element.className = 'direct-bubble own pending'; element.dataset.pending = '1';
    if (attachments.length) { element.classList.add('has-attachments'); element.append(window.DirectAttachments.render(attachments)); }
    if (content) { const text = document.createElement('p'); text.textContent = content; element.append(text); }
    const state = document.createElement('small'); state.textContent = 'Отправляется…'; element.append(state); list.append(element);
    const item = {clientId, content, attachments, element, state, failed: false}; pending.set(clientId, item); transmit(item);
    input.value = ''; draft.clear(); files?.takeAll(); scroller.scrollTop = scroller.scrollHeight;
  });
  input?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
  });
  older?.addEventListener('click', () => { if (send({type: 'sync', conversation: threadId, before: firstId})) older.disabled = true; });
  scroller?.addEventListener('scroll', readVisible, {passive: true});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { readVisible(); if (ready && threadId && loaded) send({type: 'sync', conversation: threadId, after: lastId}); } });
  window.addEventListener('beforeunload', event => { if (pending.size || files?.count() || (input?.value.trim() && !draft.active)) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); socket?.close(); });
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; connect(); } });
  // Session expiry and unread counts stay fresh even on a quiet page.
  setInterval(() => send({type: 'ping'}), 30000);
  connect();
})();

(() => {
  const messenger = document.querySelector('[data-messenger].has-active');
  const chat = messenger?.querySelector('.direct-chat');
  const back = messenger?.querySelector('[data-mobile-chat-back]');
  const sidebar = messenger?.querySelector('.inbox-sidebar');
  if (!chat || !back) return;

  const mobile = window.matchMedia('(max-width: 820px)');
  const setLayerAccessibility = () => {
    const chatOpen = mobile.matches && !messenger.classList.contains('is-showing-list');
    sidebar.inert = chatOpen;
    sidebar.setAttribute('aria-hidden', String(chatOpen));
    chat.inert = mobile.matches && !chatOpen;
    chat.setAttribute('aria-hidden', String(mobile.matches && !chatOpen));
  };
  setLayerAccessibility();
  mobile.addEventListener('change', setLayerAccessibility);
  const showList = () => {
    if (!mobile.matches || messenger.classList.contains('is-showing-list')) return;
    chat.style.transition = '';
    // Let the browser restore the transition before moving the panel away.
    void chat.offsetWidth;
    chat.style.transform = '';
    messenger.classList.add('is-showing-list');
    setLayerAccessibility();
    window.history.replaceState(window.history.state, '', messenger.dataset.inboxUrl);
  };
  back.addEventListener('click', showList);

  let gesture = null;
  chat.addEventListener('pointerdown', event => {
    if (!mobile.matches || messenger.classList.contains('is-showing-list') || event.button !== 0 ||
        event.clientX > messenger.getBoundingClientRect().left + 32) return;
    gesture = {id:event.pointerId, x:event.clientX, y:event.clientY, distance:0, dragging:false};
    chat.setPointerCapture(event.pointerId);
  });
  chat.addEventListener('pointermove', event => {
    if (!gesture || event.pointerId !== gesture.id) return;
    const dx = event.clientX - gesture.x;
    const dy = event.clientY - gesture.y;
    if (!gesture.dragging && (dx < 10 || Math.abs(dy) > dx)) return;
    gesture.dragging = true;
    gesture.distance = Math.max(0, dx);
    chat.style.transition = 'none';
    chat.style.transform = `translate3d(${gesture.distance}px,0,0)`;
    if (event.cancelable) event.preventDefault();
  });
  const finishGesture = event => {
    if (!gesture || event.pointerId !== gesture.id) return;
    const shouldClose = event.type === 'pointerup' && gesture.dragging &&
      gesture.distance > Math.min(120, messenger.clientWidth * .28);
    gesture = null;
    if (shouldClose) { showList(); return; }
    chat.style.transition = '';
    void chat.offsetWidth;
    chat.style.transform = '';
  };
  chat.addEventListener('pointerup', finishGesture);
  chat.addEventListener('pointercancel', finishGesture);
  chat.addEventListener('lostpointercapture', finishGesture);
})();

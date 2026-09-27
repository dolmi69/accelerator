(() => {
  const box = document.querySelector('[data-messenger]');
  const threadId = box?.dataset.conversation || '';
  const myId = Number(box?.dataset.userId || 0);
  const list = document.querySelector('[data-direct-messages]');
  const scroller = document.querySelector('[data-direct-scroll]');
  const form = document.querySelector('[data-direct-form]');
  const input = form?.elements.content;
  const sendButton = form?.querySelector('button[type="submit"]');
  const older = document.querySelector('[data-load-older]');
  const status = document.querySelector('[data-connection-status]');
  const errorBox = document.querySelector('[data-direct-error]');
  let socket, retry = 0, timer, ready = false, stopped = false;
  let blocked = box?.dataset.blocked === '1', lastId = 0, firstId = 0, lastRead = 0, peerRead = 0, loaded = false;
  const seen = new Set(), pending = new Map();
  const setError = text => { if (errorBox) { errorBox.textContent = text; errorBox.hidden = !text; } };
  const nearBottom = () => scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  const send = payload => { if (!ready || socket.readyState !== WebSocket.OPEN) return false; socket.send(JSON.stringify(payload)); return true; };
  const composerState = () => { if (sendButton) sendButton.disabled = !ready || blocked; if (input) input.disabled = blocked; };
  function countUnread(value) {
    if (!Number.isInteger(value)) return;
    document.querySelectorAll('[data-unread-count]').forEach(node => { node.textContent = value > 99 ? '99+' : value; node.hidden = value === 0; });
  }
  function readVisible() {
    if (threadId && lastId > lastRead && document.visibilityState === 'visible' && nearBottom()) {
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
    const content = document.createElement('p'); content.textContent = message.content;
    const meta = document.createElement('div'); meta.className = 'direct-meta';
    const time = document.createElement('time'); time.dateTime = message.created_at;
    time.textContent = new Date(message.created_at).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
    meta.append(time);
    if (own) { const state = document.createElement('span'); state.dataset.delivery = '1'; meta.append(state); }
    article.append(content, meta);
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
    if (data.message) row.querySelector('[data-thread-preview]').textContent = data.message.content.slice(0, 65);
    if (Number.isInteger(data.thread_unread)) {
      const badge = row.querySelector('[data-thread-unread]'); badge.textContent = data.thread_unread; badge.hidden = !data.thread_unread;
    }
    if (data.type === 'message') row.parentElement.prepend(row);
  }
  function transmit(item) {
    if (send({type: 'send', conversation: threadId, client_id: item.clientId, content: item.content})) {
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
      older.disabled = false; readVisible();
    } else if (data.type === 'message' && data.message.conversation === threadId) {
      const bottom = nearBottom(), own = data.message.sender_id === myId;
      appendMessage(data.message);
      if (bottom || own) scroller.scrollTop = scroller.scrollHeight;
      readVisible();
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
    if (!content || blocked) return;
    if (!ready) { setError('Соединение восстанавливается. Ваш текст остаётся в поле.'); return; }
    const clientId = crypto.randomUUID();
    const element = document.createElement('article'); element.className = 'direct-bubble own pending'; element.dataset.pending = '1';
    const text = document.createElement('p'); text.textContent = content;
    const state = document.createElement('small'); state.textContent = 'Отправляется…'; element.append(text, state); list.append(element);
    const item = {clientId, content, element, state, failed: false}; pending.set(clientId, item); transmit(item);
    input.value = ''; scroller.scrollTop = scroller.scrollHeight;
  });
  input?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
  });
  older?.addEventListener('click', () => { if (send({type: 'sync', conversation: threadId, before: firstId})) older.disabled = true; });
  scroller?.addEventListener('scroll', readVisible, {passive: true});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { readVisible(); if (ready && threadId && loaded) send({type: 'sync', conversation: threadId, after: lastId}); } });
  window.addEventListener('beforeunload', event => { if (pending.size || input?.value.trim()) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); socket?.close(); });
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; connect(); } });
  // Session expiry and unread counts stay fresh even on a quiet page.
  setInterval(() => send({type: 'ping'}), 30000);
  connect();
})();

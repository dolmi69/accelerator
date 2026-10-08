(() => {
  const chat = document.getElementById('chat');
  if (!chat) return;
  const list = document.getElementById('messages');
  const form = document.getElementById('send-form');
  const input = document.getElementById('content');
  const button = document.getElementById('send');
  const status = document.getElementById('connection');
  const error = document.getElementById('chat-error');
  const seen = new Set(Array.from(list.querySelectorAll('[data-id]'), node => Number(node.dataset.id)));
  let last = Math.max(0, ...seen), socket, timer, attempts = 0, pending = null, stopped = false;

  function append(message) {
    if (!seen.has(message.id)) {
      const row = document.createElement('article');
      row.className = 'message' + (String(message.sender_id) === chat.dataset.user ? ' own' : '');
      row.dataset.id = message.id;
      const name = document.createElement('strong'), text = document.createElement('p'), time = document.createElement('time');
      name.textContent = '@' + message.sender; text.textContent = message.content;
      time.textContent = new Date(message.created_at).toLocaleTimeString('ru', {hour:'2-digit', minute:'2-digit'});
      row.append(name, text, time);
      const next = Array.from(list.children).find(node => Number(node.dataset.id) > message.id);
      list.insertBefore(row, next || null); seen.add(message.id); last = Math.max(last, message.id);
      list.scrollTop = list.scrollHeight;
    }
    if (pending && message.nonce === pending.nonce) {
      if (input.value.trim() === pending.content) input.value = '';
      pending = null; button.disabled = socket.readyState !== WebSocket.OPEN; error.textContent = '';
    }
  }
  async function catchUp() {
    let cursor = last;
    for (let page = 0; page < 20; page++) {
      const response = await fetch(chat.dataset.history + '?after=' + cursor, {headers:{Accept:'application/json'}});
      if (!response.ok || response.redirected) throw new Error('Войдите в аккаунт заново.');
      const data = await response.json(); data.messages.forEach(append);
      if (data.messages.length) cursor = data.messages[data.messages.length - 1].id;
      if (!data.more) return;
    }
    throw new Error('Обновите страницу, чтобы загрузить историю.');
  }
  function connect() {
    if (stopped) return;
    socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/messages/' + chat.dataset.conversation + '/');
    socket.onopen = async () => {
      attempts = 0; status.textContent = 'На связи';
      try { await catchUp(); if (pending) socket.send(JSON.stringify(pending)); else button.disabled = false; }
      catch (e) { error.textContent = e.message; }
    };
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.type === 'message') append(message);
      if (message.type === 'error') { error.textContent = message.error; pending = null; button.disabled = false; }
    };
    socket.onclose = event => {
      button.disabled = true;
      if ([4400,4403].includes(event.code)) { stopped = true; status.textContent = 'Нужно войти заново'; return; }
      status.textContent = 'Восстанавливаем связь…';
      timer = setTimeout(connect, Math.min(1000 * 2 ** attempts++, 10000));
    };
  }
  function send() {
    if (pending || !input.value.trim() || socket.readyState !== WebSocket.OPEN) return;
    pending = {content:input.value.trim(), nonce:crypto.randomUUID()};
    button.disabled = true; error.textContent = ''; socket.send(JSON.stringify(pending));
  }
  form.addEventListener('submit', event => { event.preventDefault(); send(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
  });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); socket?.close(); });
  list.scrollTop = list.scrollHeight; connect();
})();

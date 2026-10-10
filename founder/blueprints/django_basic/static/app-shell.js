(() => {
  const frame = document.querySelector('iframe.prototype');
  const routes = new Set(JSON.parse(document.getElementById('lab-app-routes')?.textContent || '[]'));
  const byId = id => document.getElementById(id);
  const dialog = byId('app-action-dialog'), form = byId('app-action-form');
  if (!dialog || !form) return;
  const status = byId('app-action-status'), submit = byId('app-action-submit');
  const recipient = byId('app-recipient'), search = byId('app-recipient-search');
  const login = byId('app-action-login'), fields = byId('app-recipient-fields');
  const paths = Object.freeze({session:'/app-api/session/', results:'/app-api/results/',
    save:'/app-api/results/save/', share:'/app-api/results/share/', recipients:'/app-api/recipients/'});
  let current = null, searchTimer, searchRevision = 0;
  const reads = new Set();
  const error = (text, code) => Object.assign(new Error(text), {code});
  const api = async (action, data, query = '') => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(paths[action] + query, {method:data ? 'POST' : 'GET',
        credentials:'same-origin', signal:controller.signal, headers:data ? {'Content-Type':'application/json',
          'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value} : {},
        ...(data ? {body:JSON.stringify(data)} : {})});
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw error(value.error || 'Действие не подтверждено сервером. Попробуйте снова.', value.code);
      return value;
    } catch (exc) {
      if (exc.name === 'AbortError') throw error('Ответ сервера не получен. Проверьте историю перед повторной отправкой.', 'TIMEOUT');
      throw exc;
    } finally { clearTimeout(timer); }
  };
  const reply = (id, ok, value) => {
    frame?.contentWindow.postMessage({type:'cofounder:app-result', id, ok,
      ...(ok ? {value} : {error:value.message, code:value.code || 'ACTION_FAILED'})}, '*');
  };
  const setBusy = busy => {
    submit.disabled = busy;
    byId('app-action-cancel').disabled = busy;
    byId('app-action-close').disabled = busy;
  };
  const finish = (ok, value) => {
    if (!current) return;
    const job = current; current = null; ++searchRevision;
    clearTimeout(searchTimer); dialog.close();
    if (job.id) reply(job.id, ok, value);
    else if (ok) location.assign(value.chat_url);
  };
  const showError = exc => {
    status.textContent = exc.message || 'Действие не выполнено.';
    login.hidden = exc.code !== 'AUTH_REQUIRED';
  };
  const cancel = () => { if (current && !current.busy) finish(false, error('Действие отменено.', 'CANCELLED')); };
  byId('app-action-close').addEventListener('click', cancel);
  byId('app-action-cancel').addEventListener('click', cancel);
  dialog.addEventListener('cancel', event => {event.preventDefault(); cancel();});
  const loadRecipients = async (job, query = '') => {
    const revision = ++searchRevision;
    const value = await api('recipients', null, '?q=' + encodeURIComponent(query));
    if (current !== job || revision !== searchRevision) return;
    const selected = recipient.value;
    recipient.replaceChildren();
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Выберите получателя'; recipient.append(empty);
    for (const user of value.recipients) {
      const option = document.createElement('option'); option.value = String(user.id); option.textContent = '@' + user.username; recipient.append(option);
    }
    if ([...recipient.options].some(option => option.value === selected)) recipient.value = selected;
    if (!value.recipients.length) status.textContent = 'Участники не найдены. Получателю нужен аккаунт на этом сайте.';
  };
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    const job = current;
    searchTimer = setTimeout(() => {
      if (job && current === job) loadRecipients(job, search.value).catch(exc => {if (current === job) showError(exc);});
    }, 250);
  });
  const openAction = async (id, method, payload) => {
    if (current) throw error('Завершите уже открытое действие.', 'BUSY');
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw error('Некорректный результат.');
    const keys = Object.keys(payload);
    if (keys.some(key => !['title','content','resultId'].includes(key))) throw error('Неподдерживаемые поля результата.');
    const stored = typeof payload.resultId === 'string' && /^[a-f0-9-]{36}$/i.test(payload.resultId);
    if (stored ? keys.length !== 1 :
        typeof payload.title !== 'string' || !payload.title.trim() || payload.title.length > 120 ||
        typeof payload.content !== 'string' || !payload.content.trim() || payload.content.length > 1800 || keys.includes('resultId')) {
      throw error('Укажите название до 120 и текст до 1800 символов.');
    }
    if (method === 'results.save' && stored) throw error('Этот результат уже сохранён.');
    const job = {id, method, payload, nonce:crypto.randomUUID(), busy:false}; current = job;
    setBusy(true); login.hidden = true; search.value = ''; recipient.replaceChildren();
    fields.hidden = method !== 'chat.shareResult';
    byId('app-action-heading').textContent = method === 'chat.shareResult' ? 'Отправить результат в чат' : 'Сохранить результат';
    submit.textContent = method === 'chat.shareResult' ? 'Отправить и сохранить' : 'Сохранить';
    byId('app-result-title').textContent = stored ? 'Сохранённый результат' : payload.title;
    byId('app-result-content').textContent = stored ? 'Загрузка…' : payload.content;
    status.textContent = 'Проверяю аккаунт сайта…'; dialog.showModal();
    try {
      const session = await api('session');
      if (current !== job) return;
      if (!session.authenticated) throw error('Войдите в аккаунт этого сайта, чтобы сохранить или отправить результат.', 'AUTH_REQUIRED');
      if (stored) {
        const value = await api('results', null, '?id=' + encodeURIComponent(payload.resultId));
        if (current !== job) return;
        const result = value.results.find(row => row.id === payload.resultId);
        if (!result) throw error('Сохранённый результат не найден.');
        byId('app-result-title').textContent = result.title;
        byId('app-result-content').textContent = result.content;
      }
      if (method === 'chat.shareResult') {
        if (!session.capabilities.chat) throw error('Чаты не подключены к этому сайту.', 'MODULE_DISABLED');
        status.textContent = 'Результат получит выбранный участник. Он также сохранится в «Мои результаты».';
        await loadRecipients(job);
      } else status.textContent = 'Результат сохранится в вашем аккаунте и будет виден только вам.';
      if (current === job) setBusy(false);
    } catch (exc) {
      if (current === job) {setBusy(false); submit.disabled = true; showError(exc);}
    }
  };
  form.addEventListener('submit', async event => {
    event.preventDefault(); const job = current;
    if (!job || job.busy || submit.disabled) return;
    const sharing = job.method === 'chat.shareResult';
    if (sharing && !recipient.value) {status.textContent = 'Выберите получателя.'; recipient.focus(); return;}
    job.busy = true; setBusy(true); status.textContent = sharing ? 'Отправляю…' : 'Сохраняю…';
    try {
      const data = job.payload.resultId ? {result_id:job.payload.resultId} : {...job.payload};
      const value = await api(sharing ? 'share' : 'save', {...data, nonce:job.nonce,
        ...(sharing ? {recipient_id:Number(recipient.value)} : {})});
      if (value.persisted !== true || !value.result?.id || sharing && !Number.isInteger(value.message_id)) {
        throw error('Сервер не подтвердил запись. Проверьте историю перед повтором.');
      }
      if (current === job) finish(true, value);
    } catch (exc) {
      if (current === job) {job.busy = false; setBusy(false); showError(exc);}
    }
  });
  window.addEventListener('message', event => {
    if (!frame || event.source !== frame.contentWindow || event.origin !== 'null') return;
    const data = event.data;
    if (data?.type === 'cofounder:app-navigation' && routes.has(data.path)) location.assign(data.path);
    else if (data?.type === 'cofounder:app-height' && Number.isFinite(data.height) && data.height > 0 && data.height < 20000) {
      frame.style.height = Math.max(innerHeight, data.height) + 'px';
    } else if (data?.type === 'cofounder:app-cancel' && current?.id === data.id) cancel();
    else if (data?.type === 'cofounder:app-call') {
      if (typeof data.id !== 'string' || !/^[a-f0-9-]{36}$/i.test(data.id)) return;
      if (data.method === 'auth.getSession' || data.method === 'results.list') {
        if (reads.has(data.id) || reads.size >= 4) {reply(data.id, false, error('Дождитесь предыдущего действия.', 'BUSY')); return;}
        reads.add(data.id);
        api(data.method === 'auth.getSession' ? 'session' : 'results').then(value => reply(data.id, true, value), exc => reply(data.id, false, exc)).finally(() => reads.delete(data.id));
      } else if (data.method === 'auth.login') location.assign('/login/?next=/');
      else if (data.method === 'results.save' || data.method === 'chat.shareResult') {
        openAction(data.id, data.method, data.payload).catch(exc => reply(data.id, false, exc));
      } else reply(data.id, false, error('Этот инструмент не поддерживается.', 'UNSUPPORTED'));
    }
  });
  document.querySelectorAll('[data-saved-result]').forEach(button => button.addEventListener('click', () => {
    openAction(null, 'chat.shareResult', {resultId:button.dataset.savedResult}).catch(exc => {status.textContent = exc.message;});
  }));
})();

(() => {
  const form = document.getElementById('lab-form');
  if (!form?.dataset.routeUrl) return;
  const input = form.querySelector('[name="prompt"]');
  const mode = document.getElementById('lab-message-mode');
  const thread = document.getElementById('lab-thread');
  const status = document.getElementById('lab-status');
  const send = form.querySelector('button[type="submit"]');
  let pending = false, editApproved = false, retryId = null, retryPrompt = '';
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  function suggestion(text) {
    input.value = text;
    mode.value = 'edit';
    input.focus();
    input.dispatchEvent(new Event('input', {bubbles:true}));
    status.textContent = 'Правка подготовлена. Проверьте текст и отправьте её.';
  }
  document.addEventListener('click', event => {
    const proposed = event.target.closest('[data-lab-suggestion]');
    if (proposed && !pending) suggestion(proposed.dataset.labSuggestion);
    const question = event.target.closest('[data-lab-question]');
    if (question && !pending) {
      input.value = question.dataset.labQuestion;
      mode.value = 'discuss';
      input.focus();
      input.dispatchEvent(new Event('input', {bubbles:true}));
    }
  });
  input.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      form.requestSubmit(send);
    }
  });
  async function post(url, body) {
    const response = await fetch(url, {method:'POST', body, credentials:'same-origin', headers:{Accept:'application/json'}});
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Не удалось получить ответ. Обновите страницу; текст сообщения сохранён.');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось завершить запрос.');
    return data;
  }
  function append(data) {
    if (thread.querySelector(`[data-lab-message-id="${CSS.escape(data.id)}"]`)) return;
    const article = node('article', 'lab-discussion');
    article.dataset.labMessageId = data.id;
    const user = node('div', 'lab-user-message');
    user.append(node('small', '', 'Вы'), node('p', '', data.prompt));
    const bruno = node('div', 'lab-bruno-message');
    bruno.append(node('small', '', 'Бруно · Обсуждение'), node('p', 'lab-answer-text', data.reply.answer));
    if (data.reply.suggestion) {
      const button = node('button', 'lab-suggestion', 'Подготовить эту правку ↗');
      button.type = 'button'; button.dataset.labSuggestion = data.reply.suggestion;
      bruno.append(button);
    }
    bruno.append(node('small', 'lab-message-usage', data.reply.usage));
    article.append(user, bruno); thread.append(article); thread.scrollTop = thread.scrollHeight;
  }
  form.addEventListener('submit', async event => {
    if (event.submitter?.hasAttribute('formaction') || editApproved) return;
    event.preventDefault();
    if (pending || !form.checkValidity()) return;
    pending = true;
    input.readOnly = true;
    send.disabled = true;
    status.textContent = 'Бруно разбирает сообщение…';
    const body = new FormData(form);
    try {
      const routed = await post(form.dataset.routeUrl, body);
      if (routed.route === 'edit') {
        // The server rechecks the route on the editor endpoint too.
        status.textContent = 'Бруно дорабатывает сайт…';
        editApproved = true; send.disabled = false;
        form.requestSubmit(send);
        return;
      }
      if (routed.route !== 'discuss') throw new Error('Не удалось определить действие. Выберите «Только обсудить» или «Изменить сайт».');
      input.readOnly = false;
      status.textContent = 'Бруно отвечает. Предпросмотр доступен.';
      if (!retryId || retryPrompt !== input.value) { retryId = crypto.randomUUID(); retryPrompt = input.value; }
      body.set('request_id', retryId);
      const answer = await post(form.dataset.discussUrl, body);
      append(answer);
      if (input.value === retryPrompt) {
        input.value = ''; input.dispatchEvent(new Event('input', {bubbles:true}));
        mode.value = 'auto';
      }
      retryId = null; status.textContent = 'Ответ сохранён в истории. Сайт не изменялся.';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      if (!editApproved) { pending = false; send.disabled = false; input.readOnly = false; }
    }
  });
})();

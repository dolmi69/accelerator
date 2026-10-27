/* The browser observes durable jobs; navigation never owns the model call. */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('lab-form');
  if (!form?.dataset.jobsUrl) return;
  const input = form.querySelector('[name="prompt"]');
  const send = form.querySelector('button[type="submit"]');
  const status = document.getElementById('lab-status');
  const modelRoute = document.getElementById('lab-model-route');
  const modelHint = modelRoute?.textContent || '';
  const initial = document.getElementById('lab-job-state');
  const thread = document.getElementById('lab-thread');
  const active = job => job && ['queued', 'running'].includes(job.status);
  let current = initial ? JSON.parse(initial.textContent) : null;
  let submitting = false, polling = false, requestId = null, requestText = '', timer;
  let watched = active(current) ? current.id : null;

  function show(job) {
    current = job;
    if (modelRoute) {
      if (job?.model_route) {
        const route = job.model_route;
        modelRoute.textContent = `${route.phase === 'analysis' ? 'Разбор' : 'Задача'}: ${route.label} · ${route.reason}`;
      } else {
        modelRoute.textContent = submitting || active(job) ? 'Выбираем модель для запроса…' : modelHint;
      }
    }
    const busy = submitting || active(job);
    input.readOnly = busy;
    send.disabled = busy;
    send.textContent = busy ? 'Бруно работает…' : 'Отправить ↗';
    form.querySelectorAll('select').forEach(node => { node.disabled = busy; });
    document.querySelectorAll('.lab-quick-questions button, [data-lab-suggestion]').forEach(node => { node.disabled = busy; });
    document.dispatchEvent(new CustomEvent('lab:job-state', {detail: {active: active(job), mode: job?.mode}}));
    if (active(job)) {
      status.textContent = job.mode === 'auto' ? 'Бруно разбирает запрос. Можно уйти со страницы — работа продолжится.' : job.mode === 'edit' ? 'Бруно создаёт сайт. Можно уйти со страницы — результат сохранится.' : 'Бруно отвечает. Можно уйти со страницы — ответ сохранится.';
    } else if (job?.status === 'failed') {
      if (requestId === job.id) requestId = null;
      status.textContent = job.error;
      if (!input.value) { input.value = job.prompt; input.dispatchEvent(new Event('input', {bubbles: true})); }
    }
    let pending = document.getElementById('lab-pending-job');
    if (job && (active(job) || job.status === 'failed')) {
      if (!pending) {
        pending = document.createElement('article'); pending.id = 'lab-pending-job';
        const user = document.createElement('div'); user.className = 'lab-user-message';
        const label = document.createElement('small'); label.textContent = 'Вы';
        const prompt = document.createElement('p'); prompt.dataset.jobPrompt = '';
        user.append(label, prompt);
        const message = document.createElement('p'); message.dataset.jobMessage = ''; message.setAttribute('role', 'status');
        pending.append(user, message); thread?.append(pending);
      }
      pending.querySelector('[data-job-prompt]').textContent = job.prompt;
      pending.querySelector('[data-job-message]').textContent = job.status === 'failed' ? job.error : 'Бруно работает над запросом…';
    } else pending?.remove();
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(poll, active(current) || submitting ? 2000 : 8000);
  }
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch(form.dataset.jobStatusUrl, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error();
      const {job} = await response.json();
      if (active(job)) watched = job.id;
      if (watched && job?.id === watched && job.status === 'succeeded') {
        const url = new URL(job.url, location.href);
        if (url.origin !== location.origin) throw new Error();
        location.replace(url.href);
        return;
      }
      show(job);
    } catch (_) {
      if (active(current) || submitting) status.textContent = 'Восстанавливаем связь. Запрос продолжает выполняться в фоне.';
    } finally { polling = false; schedule(); }
  }
  form.addEventListener('submit', async event => {
    if (event.submitter?.hasAttribute('formaction')) return;
    event.preventDefault();
    if (submitting || active(current) || !form.checkValidity()) return;
    if (!requestId || requestText !== input.value) { requestId = crypto.randomUUID(); requestText = input.value; }
    const body = new FormData(form);
    body.set('request_id', requestId);
    submitting = true;
    show(current);
    status.textContent = 'Сохраняем запрос…';
    try {
      const response = await fetch(form.dataset.jobsUrl, {method: 'POST', credentials: 'same-origin', body, headers: {Accept: 'application/json'}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось отправить запрос.');
      watched = result.job.id;
      requestId = null;
      submitting = false;
      if (input.value === requestText) { input.value = ''; input.dispatchEvent(new Event('input', {bubbles: true})); }
      show(result.job);
      if (thread) thread.scrollTop = thread.scrollHeight;
      await poll();
    } catch (error) {
      submitting = false;
      show(current);
      status.textContent = error.message || 'Не удалось подтвердить отправку. Проверяем состояние запроса…';
      await poll();
    } finally { schedule(); }
  });
  window.addEventListener('pageshow', poll);
  window.addEventListener('focus', poll);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  show(current);
  poll();
});

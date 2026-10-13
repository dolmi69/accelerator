(() => {
  const form = document.getElementById('lab-workspace-session');
  if (!form) return;
  const projectId = form.dataset.projectId;
  const key = `lab-workspace:${form.dataset.userId}`;
  let revision = 0, pending = null;
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(key) : null;
  const workspace = {active: true, token: null, ready: null, activate};
  window.labWorkspace = workspace;

  function notify() {
    document.dispatchEvent(new CustomEvent('lab:workspace-change', {detail: {active: workspace.active}}));
  }

  function receive(message) {
    if (!message || typeof message.projectId !== 'string' || !Number.isSafeInteger(message.revision)
        || message.revision < revision) return;
    revision = message.revision;
    workspace.active = message.projectId === projectId;
    notify();
  }

  if (channel) channel.addEventListener('message', event => receive(event.data));
  window.addEventListener('storage', event => {
    if (event.key !== key || !event.newValue) return;
    try { receive(JSON.parse(event.newValue)); } catch (_) {}
  });

  function activate() {
    if (pending) return pending;
    pending = (async () => {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: {'Accept': 'application/json'},
      });
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Не удалось переключить предпросмотр. Обновите страницу.');
      }
      const selected = await response.json();
      if (!response.ok) throw new Error(selected.error || 'Не удалось переключить предпросмотр.');
      if (selected.project_id !== projectId || typeof selected.token !== 'string'
          || !Number.isSafeInteger(selected.revision)) throw new Error('Получен некорректный ответ лаборатории.');
      if (selected.revision < revision) throw new Error('Предпросмотр закрыт: вы перешли в другой проект.');
      revision = selected.revision;
      workspace.token = selected.token;
      workspace.active = true;
      notify();
      const message = {projectId, revision};
      channel?.postMessage(message);
      try { localStorage.setItem(key, JSON.stringify(message)); } catch (_) {}
      return selected;
    })().finally(() => { pending = null; });
    return pending;
  }

  // Autostart awaits selection; a dashboard of another project selects it too.
  workspace.ready = activate().catch(error => ({error}));
})();

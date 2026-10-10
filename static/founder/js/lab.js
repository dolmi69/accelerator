(() => {
  const form = document.getElementById('lab-form');
  if (!form) return;

  function selectSection(section, push = false) {
    section = section === 'globalization' ? section : 'development';
    document.querySelectorAll('[data-lab-panel]').forEach(panel => {
      panel.hidden = panel.dataset.labPanel !== section;
    });
    document.querySelectorAll('[data-lab-section]').forEach(link => {
      if (link.dataset.labSection === section) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    document.querySelectorAll('[data-lab-version]').forEach(link => {
      const url = new URL(link.href);
      if (section === 'globalization') url.searchParams.set('section', section);
      else url.searchParams.delete('section');
      link.href = url.href;
    });
    if (push) {
      const url = new URL(window.location.href);
      if (section === 'globalization') url.searchParams.set('section', section);
      else url.searchParams.delete('section');
      url.hash = '';
      history.pushState(history.state, '', url);
    }
    document.dispatchEvent(new Event('lab:preview-state'));
  }
  const sectionFromUrl = () => location.hash === '#lab-results' || new URL(location.href).searchParams.get('section') === 'globalization'
    ? 'globalization' : 'development';
  document.querySelectorAll('[data-lab-section]').forEach(link => {
    link.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); selectSection(link.dataset.labSection, true);
    });
  });
  window.addEventListener('popstate', () => selectSection(sectionFromUrl()));
  window.addEventListener('hashchange', () => selectSection(sectionFromUrl()));
  selectSection(sectionFromUrl());
  // Keep old bookmarks to feedback or constructor settings usable.
  if (['#backend-title', '#lab-free-title'].includes(location.hash)) {
    document.getElementById('lab-tools').open = true;
  }
  if (location.hash === '#lab-results') document.getElementById('lab-results')?.scrollIntoView();

  let generating = false;
  const forge = document.getElementById('lab-forge');
  function lockPrototype() {
    generating = true;
    forge.hidden = false;
    const reply = document.getElementById('lab-reply');
    if (reply) {
      reply.hidden = true;
    }
    const preview = document.getElementById('lab-site-frame');
    if (preview) {
      preview.inert = true;
      preview.setAttribute('aria-hidden', 'true');
    }
    document.querySelectorAll('.lab-preview-actions, .lab-site-details, #lab-publication, .lab-editor').forEach(node => { node.inert = true; });
    document.querySelectorAll('[data-lab-expand]').forEach(node => { node.disabled = true; });
    document.querySelector('.lab-preview-card')?.setAttribute('aria-busy', 'true');
    document.dispatchEvent(new Event('lab:preview-state'));
  }
  form.addEventListener('submit', event => {
    if (event.submitter?.hasAttribute('formaction')) return;
    if (generating) { event.preventDefault(); return; }
    if (!form.checkValidity()) return;
    lockPrototype();
    const button = event.submitter;
    if (button) { button.disabled = true; button.textContent = 'Бруно работает…'; }
    document.getElementById('lab-status').textContent = 'Ожидаем ответ. Это может занять до нескольких минут.';
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
  });

  const dialog = document.getElementById('lab-viewer');
  if (!dialog) return;
  // One iframe stays in place: fullscreen changes its container's size only.
  // Navigated pages, entered forms and WebSocket connections stay intact.
  const frame = document.getElementById('lab-site-frame');
  const status = document.getElementById('lab-preview-status');
  const retry = document.getElementById('lab-preview-retry');
  const previewRun = document.getElementById('lab-preview-run');
  const staticUrl = previewRun ? null : frame.getAttribute('src');
  const closeButton = dialog.querySelector('.lab-viewer-close');
  let previousFocus, previousScroll, expectedOrigin = 'null', pending = false;

  function openViewer(trigger) {
    if (generating || dialog.open || !dialog.showModal) return;
    previousFocus = trigger;
    previousScroll = {x: window.scrollX, y: window.scrollY};
    document.body.classList.add('lab-viewer-open');
    dialog.showModal();
    document.dispatchEvent(new Event('lab:preview-state'));
    closeButton.focus({preventScroll: true});
  }

  function closeViewer() {
    if (!dialog.open) return;
    dialog.close();
    document.dispatchEvent(new Event('lab:preview-state'));
    document.body.classList.remove('lab-viewer-open');
    previousFocus?.focus({preventScroll: true});
    const position = previousScroll || {x: 0, y: 0};
    requestAnimationFrame(() => window.scrollTo(position.x, position.y));
  }

  function showSite(url, backend) {
    expectedOrigin = backend ? new URL(url).origin : 'null';
    status.hidden = true;
    // A repeated launch of the same build must not reset an open inner page.
    if (frame.dataset.siteUrl !== url) {
      frame.setAttribute('sandbox', backend
        ? 'allow-scripts allow-same-origin allow-forms allow-downloads' : 'allow-scripts');
      frame.src = url;
      frame.dataset.siteUrl = url;
    }
    frame.hidden = false;
  }

  closeButton.addEventListener('click', () => closeViewer());
  dialog.addEventListener('cancel', event => { event.preventDefault(); closeViewer(); });
  window.addEventListener('keydown', event => {
    if (dialog.open && event.key === 'Escape' && !event.isComposing) {
      event.preventDefault(); closeViewer();
    }
  });
  window.addEventListener('message', event => {
    if (dialog.open && !frame.hidden && event.source === frame.contentWindow
        && event.origin === expectedOrigin && event.data?.type === 'cofounder:lab-exit') closeViewer();
  });

  document.querySelectorAll('[data-lab-expand]').forEach(button => {
    button.addEventListener('click', async () => {
      if (window.labWorkspace && !window.labWorkspace.active) await reopenSite();
      openViewer(button);
    });
  });
  document.addEventListener('lab:workspace-change', event => {
    if (event.detail.active) return;
    closeViewer();
    frame.hidden = true;
    frame.removeAttribute('src');
    delete frame.dataset.siteUrl;
    status.querySelector('p').textContent = 'Предпросмотр закрыт: вы перешли в другой проект.';
    status.hidden = false;
    retry.hidden = false;
  });
  async function launchSite(run, reactivate = false) {
    if (generating || pending) return;
    pending = true;
    retry.hidden = true;
    frame.hidden = true;
    status.querySelector('p').textContent = 'Запускаем сайт. Это может занять несколько секунд…';
    status.hidden = false;
    try {
      const workspace = window.labWorkspace;
      if (workspace) {
        const selected = await (reactivate ? workspace.activate() : workspace.ready);
        if (selected.error) throw selected.error;
        if (!workspace.active) return;
      }
      const dataForm = new FormData(run);
      if (workspace?.token) dataForm.set('selection_token', workspace.token);
      const response = await fetch(run.action, {
        method: 'POST', body: dataForm, credentials: 'same-origin',
        headers: {'Accept': 'application/json'},
      });
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Не удалось открыть сайт. Обновите лабораторию и попробуйте ещё раз.');
      }
      const data = await response.json();
      if (workspace && !workspace.active) return;
      if (!response.ok) throw new Error(data.error || 'Не удалось запустить сайт.');
      const url = new URL(data.url);
      if (url.protocol !== 'http:' || url.hostname !== window.location.hostname
          || Number(url.port) < 1024 || url.origin === window.location.origin
          || url.username || url.password) {
        throw new Error('Получен некорректный адрес предпросмотра.');
      }
      showSite(url.href, true);
      const labUrl = new URL(window.location.href);
      if (labUrl.searchParams.has('paused')) {
        labUrl.searchParams.delete('paused');
        history.replaceState(history.state, '', labUrl);
      }
      // Closing fullscreen during startup keeps the loaded site in the small
      // preview, without unexpectedly reopening the viewer.
    } catch (error) {
      status.querySelector('p').textContent = window.labWorkspace && !window.labWorkspace.active
        ? 'Предпросмотр закрыт: вы перешли в другой проект.' : error.message;
      retry.hidden = !previewRun;
    } finally {
      pending = false;
    }
  }
  async function reopenSite() {
    if (previewRun) return launchSite(previewRun, true);
    try {
      await window.labWorkspace?.activate();
      if (staticUrl) showSite(staticUrl, false);
    } catch (error) {
      status.querySelector('p').textContent = error.message;
      status.hidden = false;
    }
  }
  retry.addEventListener('click', reopenSite);
  document.querySelectorAll('.lab-run-form').forEach(run => {
    run.addEventListener('submit', event => {
      event.preventDefault();
      if (generating || pending) return;
      openViewer(run.querySelector('button'));
      launchSite(run, true);
    });
  });
  if (previewRun) {
    if (previewRun.dataset.autostart === '1') launchSite(previewRun);
    else retry.hidden = false;
  }
})();

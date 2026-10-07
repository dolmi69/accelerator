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

  form.addEventListener('submit', () => {
    if (!form.checkValidity()) return;
    const button = document.querySelector('button[form="lab-form"]');
    button.disabled = true;
    button.textContent = 'Применяем запрос…';
    document.getElementById('lab-status').textContent = 'Ожидаем ответ. Это может занять до нескольких минут.';
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
  });

  const dialog = document.getElementById('lab-viewer');
  if (!dialog?.showModal) return; // Normal POST still opens a site with a return link.
  const frame = document.getElementById('lab-viewer-frame');
  const status = document.getElementById('lab-viewer-status');
  const closeButton = dialog.querySelector('.lab-viewer-close');
  let previousFocus, previousScroll, expectedOrigin, launchId = 0, pending = false;

  function openViewer(trigger) {
    if (dialog.open) return;
    previousFocus = trigger;
    previousScroll = {x: window.scrollX, y: window.scrollY};
    document.body.classList.add('lab-viewer-open');
    dialog.showModal();
    closeButton.focus({preventScroll: true});
  }

  function closeViewer() {
    if (!dialog.open) return;
    launchId += 1; // A pending server launch may finish, but must not reopen a closed viewer.
    dialog.close();
    document.body.classList.remove('lab-viewer-open');
    previousFocus?.focus({preventScroll: true});
    const position = previousScroll;
    requestAnimationFrame(() => window.scrollTo(position.x, position.y));
  }

  function showSite(url, backend) {
    expectedOrigin = backend ? new URL(url).origin : 'null';
    status.hidden = true;
    // Retain the embedded site's page and form state when reopened at the same URL.
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

  document.querySelectorAll('[data-lab-preview]').forEach(button => {
    button.addEventListener('click', () => {
      if (pending) return;
      openViewer(button);
      showSite(button.dataset.labPreview, false);
    });
  });
  document.querySelectorAll('.lab-run-form').forEach(run => {
    run.addEventListener('submit', async event => {
      event.preventDefault();
      if (pending) return;
      pending = true;
      const button = run.querySelector('button');
      const label = button.textContent;
      button.disabled = true;
      button.textContent = 'Запускаем…';
      openViewer(button);
      frame.hidden = true;
      status.textContent = 'Запускаем сайт. Это может занять несколько секунд…';
      status.hidden = false;
      const requestId = ++launchId;
      try {
        const response = await fetch(run.action, {
          method: 'POST', body: new FormData(run), credentials: 'same-origin',
          headers: {'Accept': 'application/json'},
        });
        if (!response.headers.get('content-type')?.includes('application/json')) {
          throw new Error('Не удалось открыть сайт. Обновите лабораторию и попробуйте ещё раз.');
        }
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Не удалось запустить сайт.');
        const url = new URL(data.url);
        if (url.protocol !== 'http:' || url.hostname !== window.location.hostname || Number(url.port) < 1024) {
          throw new Error('Получен некорректный адрес предпросмотра.');
        }
        if (dialog.open && requestId === launchId) showSite(url.href, true);
      } catch (error) {
        if (dialog.open && requestId === launchId) status.textContent = error.message;
      } finally {
        pending = false;
        button.disabled = false;
        button.textContent = label;
      }
    });
  });
})();

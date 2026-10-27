(() => {
  document.querySelectorAll('[data-work-filter]').forEach(filter => {
    filter.addEventListener('toggle', () => {
      filter.classList.remove('work-filter-up');
      if (!filter.open) return;
      const menu = filter.querySelector('.work-filter-menu');
      if (!menu) return;
      const panel = menu.getBoundingClientRect();
      if (panel.bottom > window.innerHeight - 12 && filter.getBoundingClientRect().top > panel.height + 12) {
        filter.classList.add('work-filter-up');
      }
    });
    document.addEventListener('click', event => {
      if (!filter.contains(event.target)) filter.open = false;
    });
    filter.addEventListener('keydown', event => {
      if (event.key === 'Escape' && filter.open) {
        filter.open = false;
        filter.querySelector('summary').focus();
        event.preventDefault();
      }
    });
  });
  document.querySelectorAll('[data-busy-form]').forEach(form => {
    form.addEventListener('submit', event => {
      // Нажатая кнопка, а не первая: у формы карточки две кнопки с разными action.
      const button = event.submitter || form.querySelector('button');
      if (button.name) {
        // Выключенная кнопка не отправляет своё значение, поэтому переносим его в скрытое поле.
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = button.name;
        hidden.value = button.value;
        form.appendChild(hidden);
      }
      button.disabled = true;
      button.textContent = button.dataset.busyLabel || 'Сохраняем…';
      const status = form.querySelector('[role="status"]');
      if (status) status.textContent = 'Это может занять до минуты.';
    });
  });
  const revealTarget = () => {
    let id;
    try { id = decodeURIComponent(window.location.hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    if (!target) return;
    for (let node = target; node; node = node.parentElement) {
      if (node.tagName === 'DETAILS') node.open = true;
    }
  };
  revealTarget();
  window.addEventListener('hashchange', revealTarget);
  document.querySelectorAll('[data-task-row]').forEach(row => {
    const details = row.querySelector('.priority-execution-details');
    const toggle = row.querySelector('[data-task-toggle]');
    if (!details || !toggle) return;
    const sync = () => toggle.setAttribute('aria-expanded', String(details.open));
    toggle.addEventListener('click', () => { details.open = !details.open; sync(); });
    row.addEventListener('click', event => {
      if (event.target.closest('a, button, input, select, textarea, label, form, details, summary') ||
          window.getSelection()?.toString()) return;
      details.open = !details.open;
      sync();
    });
    details.addEventListener('toggle', sync);
    sync();
  });
  window.addEventListener('pageshow' , event => { if (event.persisted) window.location.reload(); });
})();

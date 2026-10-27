(() => {
  const form = document.querySelector('[data-card-editor]');
  if (!form) return;
  const leave = window.CardLeave;
  const resizeFields = () => {
    form.querySelectorAll('.card-edit-document textarea').forEach(field => {
      field.style.height = 'auto'; field.style.height = `${field.scrollHeight + 2}px`;
    });
  };
  window.addEventListener('resize', resizeFields);
  const preview = () => {
    resizeFields();
    document.querySelectorAll('[data-preview]').forEach(node => {
      const value = form.elements[node.dataset.preview]?.value.trim();
      node.textContent = value || (node.dataset.preview === 'name' ? 'Ваш проект' : '');
    });
    const monogram = document.querySelector('[data-preview-monogram]');
    if (monogram) monogram.textContent = (form.elements.name.value || 'П').slice(0, 1).toUpperCase();
  };
  form.addEventListener('input', preview);
  preview();

  // Снимок того, что сохранено: после сохранения страница перезагружается и снимок берётся заново.
  const generated = form.dataset.generated === '1';
  const snapshot = leave ? leave.serialize(form.elements) : '';
  const isDirty = () => Boolean(leave) && leave.isDirty(snapshot, form.elements, generated);
  let busy = false, leaving = false, pendingHref = '';
  const markDirty = () => {
    const state = document.querySelector('[data-card-publish-state]');
    if (state && isDirty()) state.textContent = 'Есть несохранённые изменения';
  };
  form.addEventListener('input', markDirty);
  form.addEventListener('change', markDirty);

  const dialog = document.getElementById('card-leave-dialog');
  const leaveNow = () => { leaving = true; window.location.href = pendingHref; };

  // Переход в другой раздел внутри приложения: спрашиваем до ухода со страницы.
  document.addEventListener('click', event => {
    if (!dialog || !leave || busy || leaving || !isDirty()) return;
    const anchor = event.target.closest ? event.target.closest('a[href]') : null;
    if (!leave.shouldInterceptClick(event, anchor, window.location.href)) return;
    event.preventDefault();
    pendingHref = anchor.href;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else if (window.confirm('Изменения не сохранены. Уйти без сохранения?')) leaveNow();
  }, true);
  dialog?.querySelector('[data-leave-stay]')?.addEventListener('click', () => dialog.close());
  dialog?.querySelector('[data-leave-go]')?.addEventListener('click', leaveNow);

  // Закрытие вкладки или обновление страницы.
  window.addEventListener('beforeunload', event => {
    if (!busy && !leaving && isDirty()) { event.preventDefault(); event.returnValue = ''; }
  });

  form.addEventListener('submit', event => {
    if (busy) { event.preventDefault(); return; }
    busy = true;
    // Preserve the submitter's name/value while disabling further clicks.
    const button = event.submitter;
    if (button?.name) {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = button.name; input.value = button.value; form.append(input);
    }
    form.querySelectorAll('button').forEach(node => { node.disabled = true; });
    if (button?.dataset.busyLabel) button.textContent = button.dataset.busyLabel;
    document.querySelector('[data-card-status]').textContent = 'Сохраняем… Если работает Бруно, это может занять около минуты.';
  });
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();

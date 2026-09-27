(() => {
  const form = document.querySelector('[data-card-editor]');
  if (!form) return;
  const preview = () => {
    document.querySelectorAll('[data-preview]').forEach(node => {
      const value = form.elements[node.dataset.preview]?.value.trim();
      node.textContent = value || (node.dataset.preview === 'name' ? 'Ваш проект' : '');
    });
    document.querySelector('[data-preview-monogram]').textContent = (form.elements.name.value || 'П').slice(0, 1).toUpperCase();
    document.querySelector('[data-preview-stage]').textContent = form.elements.stage.selectedOptions[0].textContent;
  };
  form.addEventListener('input', preview);
  preview();
  let dirty = form.dataset.generated === '1', busy = false;
  form.addEventListener('input', () => { dirty = true; });
  window.addEventListener('beforeunload', event => { if (dirty && !busy) { event.preventDefault(); event.returnValue = ''; } });
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

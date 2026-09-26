(() => {
  document.querySelectorAll('[data-busy-form]').forEach(form => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button');
      button.disabled = true;
      button.textContent = button.dataset.busyLabel || 'Сохраняем…';
      const status = form.querySelector('[role="status"]');
      if (status) status.textContent = 'Это может занять до минуты.';
    });
  });
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();

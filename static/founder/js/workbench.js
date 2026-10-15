(() => {
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
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();

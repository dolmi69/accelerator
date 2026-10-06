(() => {
  const form = document.getElementById('lab-form');
  if (!form) return;
  form.addEventListener('submit', () => {
    if (!form.checkValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = 'Создаём сайт…';
    document.getElementById('lab-status').textContent = 'Ожидаем ответ. Это может занять до нескольких минут.';
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
  });
})();

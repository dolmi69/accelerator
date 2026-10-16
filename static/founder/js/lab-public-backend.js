/* Only this trusted launcher runs on the platform origin. Model code stays in
   a separate, per-version origin and cannot access platform cookies or DOM. */
(() => {
  const form = document.getElementById('backend-launch-form');
  if (!form) return;
  const status = document.getElementById('backend-launch-status');
  const button = form.querySelector('button');
  async function launch() {
    if (button.disabled) return;
    button.disabled = true;
    button.hidden = true;
    status.textContent = 'Запускаем сайт…';
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form),
        credentials: 'same-origin', headers: {'Accept': 'application/json'}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Не удалось запустить сайт.');
      const url = new URL(result.url);
      if (url.protocol !== 'http:' || !/^lab-[0-9a-f-]{36}\.localhost$/.test(url.hostname) || !url.port || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
        throw new Error('Получен некорректный адрес сайта.');
      }
      document.getElementById('backend-site-frame').src = url.href;
      document.getElementById('backend-open-tab').href = url.href;
      document.getElementById('backend-launch').hidden = true;
      document.getElementById('backend-site').hidden = false;
    } catch (error) {
      button.hidden = false;
      status.textContent = error.message || 'Не удалось запустить сайт. Попробуйте ещё раз.';
    } finally { button.disabled = false; }
  }
  form.addEventListener('submit', event => { event.preventDefault(); launch(); });
  launch();
})();

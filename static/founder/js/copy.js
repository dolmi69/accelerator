// Копирование ответа Бруно и ссылок на карточки: кнопка с data-copy-text (селектор блока с текстом)
// или data-copy-url (адрес страницы). Итог показывает короткое уведомление, его же читает экранный диктор.
(() => {
  const CHECK = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
  // Область role="status" стоит в DOM с загрузки, иначе диктор пропускает первое уведомление.
  const region = document.createElement('div');
  region.className = 'toast-region';
  region.setAttribute('role', 'status');
  document.body.append(region);
  let hideTimer;

  function toast(text, ok = true) {
    clearTimeout(hideTimer);
    const item = document.createElement('div');
    item.className = ok ? 'toast' : 'toast is-error';
    if (ok) item.innerHTML = CHECK;
    item.append(text);
    region.replaceChildren(item);
    hideTimer = setTimeout(() => {
      item.classList.add('is-leaving');
      setTimeout(() => item.remove(), 200);
    }, 2400);
  }
  window.appToast = toast;

  async function writeText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Без HTTPS (или в старом WebView Telegram) Clipboard API нет: копируем через скрытое поле.
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
      document.body.append(area);
      area.select();
      let copied = false;
      try { copied = document.execCommand('copy'); } catch { copied = false; }
      area.remove();
      return copied;
    }
  }

  document.addEventListener('click', async event => {
    const button = event.target.closest?.('[data-copy-text], [data-copy-url]');
    if (!button) return;
    const isLink = button.hasAttribute('data-copy-url');
    const text = isLink
      ? new URL(button.dataset.copyUrl, location.origin).href
      : document.querySelector(button.dataset.copyText)?.innerText.trim();
    if (!text) return;
    if (await writeText(text)) {
      toast(isLink ? 'Ссылка скопирована' : 'Текст скопирован');
      button.classList.add('is-copied');
      setTimeout(() => button.classList.remove('is-copied'), 1600);
    } else {
      toast(isLink ? `Скопируйте ссылку вручную: ${text}` : 'Браузер не дал скопировать. Выделите текст вручную.', false);
    }
  });
})();

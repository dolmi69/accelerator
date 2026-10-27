// Предупреждение о несохранённых изменениях карточки проекта.
// Чистые функции: cards.js подключает их к странице, tests/card_leave.test.cjs проверяет без браузера.
(() => {
  // Служебные поля и поле доработки Бруно не считаются содержимым карточки.
  const IGNORED = new Set(['csrfmiddlewaretoken', 'action', 'instruction']);

  // Снимок значений формы: то, что сохранится при отправке.
  function serialize(fields) {
    return JSON.stringify(Array.from(fields)
      .filter(field => field.name && !IGNORED.has(field.name) && !['submit', 'button'].includes(field.type))
      .map(field => [field.name, field.type === 'checkbox' ? (field.checked ? '1' : '0') : String(field.value ?? '')]));
  }

  // Предложения Бруно (generated) тоже несохранены, даже если совпадают с полями.
  function isDirty(snapshot, fields, generated) {
    return Boolean(generated) || serialize(fields) !== snapshot;
  }

  // Перехватываем обычный клик по ссылке внутри приложения, а не новую вкладку и не якорь на той же странице.
  function shouldInterceptClick(event, anchor, currentHref) {
    if (!anchor || !anchor.href) return false;
    if (event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return false;
    if (anchor.target && anchor.target !== '_self') return false;
    if (anchor.hasAttribute && anchor.hasAttribute('download')) return false;
    let target;
    try { target = new URL(anchor.href, currentHref); } catch (_) { return false; }
    const here = new URL(currentHref);
    if (target.origin !== here.origin) return false;
    if (target.pathname === here.pathname && target.search === here.search) return false;
    return true;
  }

  const api = { serialize, isDirty, shouldInterceptClick };
  if (typeof window !== 'undefined') window.CardLeave = api;
  if (typeof module !== 'undefined') module.exports = api;
})();

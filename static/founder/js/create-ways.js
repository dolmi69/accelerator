// Способы создания проекта (анкета или Мастерская идей): переключение без перезагрузки страницы.
// Нужный вариант страницы загружается целиком, на месте меняется только блок под карточками — старый гаснет,
// новый проявляется, подсветка карточки переезжает плавно (app.css). Без fetch или при ошибке — обычный переход.
(() => {
  const nav = document.querySelector('.create-ways');
  if (!nav || !window.fetch || !window.DOMParser) return;
  const links = [...nav.querySelectorAll('a.create-way')];
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const wayOf = url => (new URL(url, location.href).searchParams.get('way') === 'idea' ? 'idea' : 'form');
  let busy = false;

  const markCurrent = url => {
    const way = wayOf(url);
    links.forEach(link => {
      if (wayOf(link.href) === way) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  };

  async function show(url, push) {
    if (busy) return;
    busy = true;
    nav.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok || response.redirected) throw new Error('fallback');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = doc.querySelector('.create-ways')?.nextElementSibling;
      const old = nav.nextElementSibling;
      if (!next || !old) throw new Error('fallback');
      markCurrent(url);
      if (!reduce.matches) {
        await old.animate([{ opacity: 1 }, { opacity: 0, transform: 'translateY(4px)' }],
          { duration: 140, easing: 'ease-in', fill: 'forwards' }).finished;
      }
      const node = document.importNode(next, true);
      old.replaceWith(node);
      if (push) history.pushState({ createWay: wayOf(url) }, '', url);
      document.title = doc.title;
      if (!reduce.matches) {
        node.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
          { duration: 280, easing: 'cubic-bezier(.22,1,.36,1)' });
      }
      // Скрипты Мастерской идей запускаются заново для нового блока.
      doc.querySelectorAll('script[src*="workbench.js"], script[src*="idea-picker.js"]').forEach(source => {
        const script = document.createElement('script');
        script.src = source.getAttribute('src');
        document.body.append(script);
      });
    } catch (_) {
      location.href = url;
    } finally {
      busy = false;
      nav.removeAttribute('aria-busy');
    }
  }

  nav.addEventListener('click', event => {
    const link = event.target.closest('a.create-way');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.getAttribute('aria-current') !== 'page') show(link.href, true);
  });
  history.replaceState({ createWay: wayOf(location.href) }, '', location.href);
  window.addEventListener('popstate', event => {
    if (event.state?.createWay) show(location.href, false);
  });
})();

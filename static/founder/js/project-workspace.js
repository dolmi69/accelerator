(() => {
  const nav = document.querySelector('.project-workspace-nav');
  if (!nav) return;
  const menus = Array.from(document.querySelectorAll('.project-switcher, .workspace-more'));
  const sidebar = document.getElementById('project-sidebar');
  const mobileToggle = document.querySelector('.project-menu-toggle');
  const collapse = document.querySelector('[data-sidebar-collapse]');
  const scrim = document.querySelector('[data-sidebar-dismiss]');
  const mobile = window.matchMedia('(max-width:900px)');
  document.body.classList.add('js-project-shell');
  try { document.body.classList.toggle('sidebar-collapsed', localStorage.getItem('project-sidebar') === 'collapsed'); } catch (_) {}
  function syncSidebar() {
    const open = document.body.classList.contains('sidebar-open');
    if (sidebar) sidebar.inert = mobile.matches && !open;
    if (mobileToggle) { mobileToggle.hidden = false; mobileToggle.setAttribute('aria-expanded', String(open)); }
    if (scrim) scrim.hidden = !mobile.matches || !open;
    if (collapse) {
      collapse.hidden = false;
      const expanded = !document.body.classList.contains('sidebar-collapsed');
      collapse.textContent = mobile.matches ? '×' : expanded ? '‹' : '›';
      collapse.setAttribute('aria-expanded', String(expanded));
      collapse.setAttribute('aria-label', mobile.matches ? 'Закрыть разделы проекта' : expanded ? 'Свернуть боковое меню' : 'Развернуть боковое меню');
    }
  }
  function closeSidebar() { document.body.classList.remove('sidebar-open'); syncSidebar(); }
  mobileToggle?.addEventListener('click', () => { document.body.classList.toggle('sidebar-open'); syncSidebar(); });
  scrim?.addEventListener('click', closeSidebar);
  collapse?.addEventListener('click', () => {
    if (mobile.matches) { closeSidebar(); mobileToggle?.focus(); return; }
    document.body.classList.toggle('sidebar-collapsed');
    try { localStorage.setItem('project-sidebar', document.body.classList.contains('sidebar-collapsed') ? 'collapsed' : 'expanded'); } catch (_) {}
    syncSidebar();
  });
  mobile.addEventListener('change', () => { closeSidebar(); syncSidebar(); });
  const topbar = document.querySelector('.topbar');
  const updateTopbar = () => { if (topbar) document.body.style.setProperty('--project-topbar-height', `${topbar.offsetHeight}px`); };
  if (topbar && typeof ResizeObserver !== 'undefined') new ResizeObserver(updateTopbar).observe(topbar);
  updateTopbar(); syncSidebar();
  document.addEventListener('click', event => {
    menus.forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    menus.forEach(menu => { if (menu.open) { menu.open = false; menu.querySelector('summary').focus(); } });
    if (mobile.matches && document.body.classList.contains('sidebar-open')) { closeSidebar(); mobileToggle?.focus(); }
  });
  function reveal(element) {
    for (let node = element; node; node = node.parentElement) {
      if (node instanceof HTMLDetailsElement) node.open = true;
    }
  }
  function revealAnchor() {
    if (!location.hash) return;
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    if (target) {
      reveal(target);
      target.scrollIntoView({block:'start'});
    }
  }
  revealAnchor();
  window.addEventListener('hashchange', revealAnchor);
  // Browser validation must be able to focus fields inside folded sections.
  document.querySelectorAll('form').forEach(form => {
    form.addEventListener('invalid', event => reveal(event.target), true);
  });
  document.querySelectorAll('.field-error,.errorlist,.form-error').forEach(error => {
    if (error.textContent.trim()) reveal(error);
  });
})();

// Телефон: аватар в шапке открывает меню с разделами сайта. На компьютере ссылки стоят в строку, кнопка скрыта стилями.
(() => {
  const nav = document.querySelector('.topnav');
  const toggle = nav?.querySelector('.account-menu-toggle');
  if (!toggle) return;
  const wide = window.matchMedia('(min-width:681px)');
  document.body.classList.add('js-topnav');
  toggle.hidden = false;
  const setOpen = open => {
    nav.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
  };
  toggle.addEventListener('click', () => setOpen(!nav.classList.contains('is-open')));
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !nav.classList.contains('is-open')) return;
    setOpen(false);
    toggle.focus();
  });
  document.addEventListener('click', event => { if (!nav.contains(event.target)) setOpen(false); });
  wide.addEventListener('change', event => { if (event.matches) setOpen(false); });
})();

// Карточки-свёртки (<details>): при раскрытии человеком содержимое проявляется (app.css, details.is-opening).
document.addEventListener('click', event => {
  const summary = event.target.closest?.('summary');
  const details = summary?.parentElement;
  if (!(details instanceof HTMLDetailsElement) || details.open || details.querySelector(':scope > summary') !== summary) return;
  details.classList.add('is-opening');
});
document.addEventListener('animationend', event => {
  if (event.animationName === 'fold-in' || event.animationName === 'fold-fade') event.target.parentElement?.classList.remove('is-opening');
});
document.addEventListener('toggle', event => {
  if (event.target instanceof HTMLDetailsElement && !event.target.open) event.target.classList.remove('is-opening');
}, true);

// Переходы между страницами (View Transitions, app.css). Обновление той же страницы — без анимации,
// а открытая из сообщества карточка проекта «перетекает» в страницу проекта.
window.addEventListener('pageswap', event => {
  const transition = event.viewTransition;
  const url = event.activation?.entry?.url;
  if (!transition || !url) return;
  const target = new URL(url);
  if (target.pathname === location.pathname) { transition.skipTransition(); return; }
  const link = Array.from(document.querySelectorAll('.discovery-card a[href]')).find(a => a.pathname === target.pathname);
  const card = link?.closest('.discovery-card');
  if (!card) return;
  card.style.viewTransitionName = 'project-card';
  transition.finished.finally(() => { card.style.viewTransitionName = ''; });
});

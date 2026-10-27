// Тема: «Как в системе», «Светлая», «Тёмная». Выбор живёт в браузере (localStorage) и ставится
// скриптом в <head> до первой отрисовки; здесь — кнопки выбора, цвет панели браузера и тема Telegram.
(() => {
  const root = document.documentElement;
  const COLORS = {light: '#f5f4ef', dark: '#131920'};
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const stored = () => {
    try { return localStorage.getItem('bl-theme') || 'system'; } catch { return 'system'; }
  };
  // В Telegram «как в системе» значит «как в Telegram»: у приложения своя тема, не всегда совпадающая с телефоном.
  const telegram = () => window.Telegram?.WebApp?.colorScheme;

  function apply(choice) {
    const forced = choice === 'system' ? telegram() : choice;
    if (forced === 'light' || forced === 'dark') root.dataset.theme = forced;
    else delete root.dataset.theme;
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => {
      meta.content = COLORS[forced] || COLORS[(meta.media || '').includes('dark') ? 'dark' : 'light'];
    });
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === choice));
    });
    // Шапка и фон Mini App красятся в цвета текущей темы.
    const app = window.Telegram?.WebApp;
    if (app?.isVersionAtLeast?.('6.1')) {
      const style = getComputedStyle(root);
      app.setHeaderColor(style.getPropertyValue('--surface').trim() || '#ffffff');
      app.setBackgroundColor(style.getPropertyValue('--bg').trim() || '#f5f4ef');
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest?.('[data-theme-choice]');
    if (!button) return;
    const choice = button.dataset.themeChoice;
    try {
      if (choice === 'system') localStorage.removeItem('bl-theme');
      else localStorage.setItem('bl-theme', choice);
    } catch { /* приватный режим: тема действует до перезагрузки */ }
    // Цвета перетекают за 250 мс, чтобы смена темы читалась как ответ на нажатие, а не как мигание.
    if (!reduce.matches) {
      root.classList.add('theme-switching');
      setTimeout(() => root.classList.remove('theme-switching'), 320);
    }
    apply(choice);
  });

  window.Telegram?.WebApp?.onEvent?.('themeChanged', () => apply(stored()));
  apply(stored());
})();

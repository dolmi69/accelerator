// Движение интерфейса (DESIGN.md → Движение). Всё здесь — отклик на действие; при «уменьшить движение» ничего не запускается.
(() => {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');

  // Кнопка формы с долгим запросом получает бегущий блик, пока страница ждёт ответ (workbench.js меняет её подпись).
  document.addEventListener('submit', event => {
    if (!event.target.matches?.('[data-busy-form]')) return;
    (event.submitter || event.target.querySelector('button[type="submit"]'))?.classList.add('is-busy');
  });

  if (reduce.matches) return;

  // 1. Числа досчитываются при открытии страницы: общий балл радара, баллы направлений, плитки прогресса.
  // Переключатель «Первая оценка / Сейчас» ведёт radar-compare.js, он берёт числа из data-атрибутов, а не из текста.
  const DURATION = 800;
  const ease = t => 1 - Math.pow(1 - t, 3);
  function countUp(node) {
    const target = Number.parseInt(node.textContent.trim(), 10);
    if (!Number.isFinite(target) || target <= 0) return;
    const started = performance.now();
    node.textContent = '0';
    const step = now => {
      const progress = Math.min(1, (now - started) / DURATION);
      node.textContent = String(Math.round(target * ease(progress)));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    // Если вкладка в фоне и кадры не идут, число всё равно должно встать на место.
    setTimeout(() => { node.textContent = String(target); }, DURATION + 400);
  }
  document.querySelectorAll('[data-score-value], .radar-axis-value, [data-count-up]').forEach(countUp);

  // 2. Монета летит в счётчик в шапке. coins.js при начислении ставит чипу класс is-earning — это и есть сигнал.
  let lastPointer = null;
  document.addEventListener('pointerdown', event => { lastPointer = {x: event.clientX, y: event.clientY}; }, {passive: true});
  function flyCoin(chip) {
    const target = chip.getBoundingClientRect();
    if (!target.width) return;
    const from = lastPointer || {x: window.innerWidth / 2, y: window.innerHeight * 0.6};
    const coin = document.createElement('span');
    coin.className = 'coin-fly';
    coin.setAttribute('aria-hidden', 'true');
    document.body.append(coin);
    const dx = target.left + 14 - from.x;
    const dy = target.top + target.height / 2 - from.y;
    coin.animate([
      {transform: `translate(${from.x}px, ${from.y}px) scale(.6)`, opacity: 0},
      {transform: `translate(${from.x + dx * 0.25}px, ${from.y + dy * 0.25 - 60}px) scale(1.15)`, opacity: 1, offset: 0.3},
      {transform: `translate(${from.x + dx}px, ${from.y + dy}px) scale(.55)`, opacity: 1},
    ], {duration: 700, easing: 'cubic-bezier(.45,0,.55,1)'}).finished.finally(() => coin.remove());
  }
  document.querySelectorAll('.coin-chip').forEach(chip => {
    new MutationObserver(() => { if (chip.classList.contains('is-earning')) flyCoin(chip); })
      .observe(chip, {attributes: true, attributeFilter: ['class']});
  });

  // 3. Название проекта перелетает из списка «Мои проекты» в заголовок страницы проекта (View Transitions).
  // Имя перехода получает только нажатая строка: на странице оно должно быть одно.
  document.addEventListener('click', event => {
    const link = event.target.closest?.('.project-row a[href]');
    const title = link?.closest('.project-row')?.querySelector('h2');
    if (title) title.style.viewTransitionName = 'project-title';
  });
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('.project-row h2').forEach(title => { title.style.viewTransitionName = ''; });
  });
})();

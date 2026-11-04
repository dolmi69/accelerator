// «О проекте»: радар примера дорисовывается от первой оценки к оценке через три недели (640 мс, как на радаре проекта).
// Один раз проигрывается сам, когда блок впервые виден; дальше только по переключателю. При reduced motion без движения.
(() => {
  const figure = document.querySelector('[data-about-radar]');
  const polygon = figure?.querySelector('[data-about-polygon]');
  if (!polygon) return;
  const score = figure.querySelector('[data-about-score]');
  const buttons = [...figure.querySelectorAll('[data-about-view]')];
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const parse = value => value.trim().split(/\s+/).map(point => point.split(',').map(Number));
  const shapes = {first: parse(polygon.dataset.first), now: parse(polygon.dataset.now)};
  const totals = {first: Number(score.dataset.first), now: Number(score.dataset.now)};
  let current = {shape: shapes.now, total: totals.now};
  let frame = 0;

  const draw = state => {
    polygon.setAttribute('points', state.shape.map(point => point.map(value => value.toFixed(1)).join(',')).join(' '));
    score.textContent = Math.round(state.total);
  };
  const mark = view => {
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.aboutView === view)));
    figure.classList.toggle('is-first', view === 'first');
  };
  const show = (view, animate = !reduce.matches) => {
    cancelAnimationFrame(frame);
    mark(view);
    const from = current;
    const to = {shape: shapes[view], total: totals[view]};
    if (!animate) {
      current = to;
      figure.classList.remove('is-animating');
      draw(current);
      return;
    }
    const started = performance.now();
    figure.classList.add('is-animating');
    const step = now => {
      const t = Math.min(1, (now - started) / 640);
      const eased = 1 - Math.pow(1 - t, 3);
      current = {
        shape: from.shape.map((point, i) => point.map((value, j) => value + (to.shape[i][j] - value) * eased)),
        total: from.total + (to.total - from.total) * eased,
      };
      draw(current);
      if (t < 1) frame = requestAnimationFrame(step);
      else figure.classList.remove('is-animating');
    };
    frame = requestAnimationFrame(step);
  };

  buttons.forEach(button => button.addEventListener('click', () => show(button.dataset.aboutView)));

  if (reduce.matches || !('IntersectionObserver' in window)) return;
  const observer = new IntersectionObserver(entries => {
    if (!entries.some(entry => entry.isIntersecting)) return;
    observer.disconnect();
    show('first', false);
    setTimeout(() => show('now'), 360);
  }, {threshold: 0.6});
  observer.observe(figure);
})();

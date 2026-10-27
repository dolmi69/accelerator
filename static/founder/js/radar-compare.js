// «Первая оценка / Сейчас»: многоугольник радара перетекает от первой оценки проекта к выбранной за 640 мс,
// точки едут вместе с ним, общий балл меняется. Так видно, как доказательства двигают радар.
(() => {
  const radar = document.querySelector('[data-radar-compare]')?.closest('[data-interactive-radar]');
  const polygon = radar?.querySelector('.radar-value[data-first]');
  if (!polygon || !polygon.dataset.first) return;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const parse = value => value.trim().split(/\s+/).map(point => point.split(',').map(Number));
  const shapes = {now: parse(polygon.dataset.now), first: parse(polygon.dataset.first)};
  const circles = [...radar.querySelectorAll('circle[data-first]')];
  const circleShapes = {
    now: circles.map(circle => circle.dataset.now.split(',').map(Number)),
    first: circles.map(circle => circle.dataset.first.split(',').map(Number)),
  };
  const badge = radar.querySelector('[data-radar-score]');
  const scoreNode = badge?.querySelector('[data-score-value]');
  const scores = {now: Number(badge?.dataset.now), first: Number(badge?.dataset.first)};
  const legend = radar.querySelector('[data-radar-legend]');
  const legends = {now: legend?.textContent.trim(), first: radar.querySelector('[data-radar-compare]').dataset.firstLabel};
  const previousLegend = radar.querySelector('[data-radar-previous-legend]');
  // Точки и названия осей открывают подробности выбранной оценки: на первой оценке они бы показывали не те баллы.
  const details = [...radar.querySelectorAll('.radar-point, .radar-axis-label')];
  let current = {polygon: shapes.now, circles: circleShapes.now, score: scores.now};
  let frame;

  const draw = state => {
    if (scoreNode && Number.isFinite(state.score)) scoreNode.textContent = Math.round(state.score);
    polygon.setAttribute('points', state.polygon.map(point => point.map(value => value.toFixed(1)).join(',')).join(' '));
    circles.forEach((circle, index) => {
      circle.setAttribute('cx', state.circles[index][0].toFixed(1));
      circle.setAttribute('cy', state.circles[index][1].toFixed(1));
    });
  };
  const mix = (from, to, t) => from.map((point, index) => point.map((value, axis) => value + (to[index][axis] - value) * t));

  function show(view) {
    radar.querySelectorAll('[data-radar-view]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.radarView === view));
    });
    radar.dataset.radarShowing = view;
    if (legend) legend.textContent = legends[view];
    const status = radar.querySelector('[data-radar-status]');
    if (status && Number.isFinite(scores[view])) status.textContent = `${legends[view]}: ${scores[view]} из 100`;
    if (previousLegend) previousLegend.hidden = view === 'first';
    details.forEach(node => { node.inert = view === 'first'; });
    const from = current;
    const to = {polygon: shapes[view], circles: circleShapes[view], score: scores[view]};
    cancelAnimationFrame(frame);
    if (reduce.matches) {
      current = to;
      draw(current);
      return;
    }
    const start = performance.now();
    const step = now => {
      const t = Math.min(1, (now - start) / 640);
      const eased = 1 - Math.pow(1 - t, 3);
      current = {polygon: mix(from.polygon, to.polygon, eased), circles: mix(from.circles, to.circles, eased),
        score: from.score + (to.score - from.score) * eased};
      draw(current);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  }

  radar.querySelectorAll('[data-radar-view]').forEach(button => {
    button.addEventListener('click', () => show(button.dataset.radarView));
  });
})();

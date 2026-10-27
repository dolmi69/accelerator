// «Первая оценка / Сейчас»: многоугольник радара перетекает от первой оценки проекта к выбранной за 640 мс,
// точки едут вместе с ним, общий балл и баллы в списке направлений досчитываются. Так видно, как доказательства двигают радар.
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
  const rowScores = [...radar.querySelectorAll('[data-radar-row-score]')];
  const scores = {
    now: [Number(badge?.dataset.now), ...rowScores.map(node => Number(node.dataset.now))],
    first: [Number(badge?.dataset.first), ...rowScores.map(node => Number(node.dataset.first))],
  };
  const labels = Object.fromEntries([...radar.querySelectorAll('[data-radar-view]')]
    .map(button => [button.dataset.radarView, button.textContent.trim()]));
  // Точки, названия осей и строки списка открывают подробности выбранной оценки: на первой оценке они бы показывали не те баллы.
  const details = [...radar.querySelectorAll('.radar-point, .radar-axis-label, .radar-axis-row')];
  let current = {polygon: shapes.now, circles: circleShapes.now, scores: scores.now};
  let frame;

  const draw = state => {
    [scoreNode, ...rowScores].forEach((node, index) => {
      if (node && Number.isFinite(state.scores[index])) node.textContent = Math.round(state.scores[index]);
    });
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
    const status = radar.querySelector('[data-radar-status]');
    if (status && Number.isFinite(scores[view][0])) status.textContent = `${labels[view]}: ${scores[view][0]} из 100`;
    details.forEach(node => { node.inert = view === 'first'; });
    const from = current;
    const to = {polygon: shapes[view], circles: circleShapes[view], scores: scores[view]};
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
        scores: from.scores.map((value, index) => value + (to.scores[index] - value) * eased)};
      draw(current);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  }

  radar.querySelectorAll('[data-radar-view]').forEach(button => {
    button.addEventListener('click', () => show(button.dataset.radarView));
  });
})();

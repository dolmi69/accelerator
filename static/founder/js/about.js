// «О проекте»: пример радара, который можно двигать. Точки ходят по своим осям, общий балл и совет Бруно
// пересчитываются. Данные вымышленные, это показано подписью «Пример» на карточке.
(() => {
  const root = document.querySelector('[data-demo-radar]');
  const svg = root?.querySelector('svg');
  const shape = root?.querySelector('[data-demo-shape]');
  if (!svg || !shape) return;

  const CENTER = {x: 110, y: 110};
  const RADIUS = 82;
  const FIRST_AVERAGE = 29;
  const AXES = [-90, -18, 54, 126, 198].map(deg => {
    const rad = deg * Math.PI / 180;
    return {x: Math.cos(rad), y: Math.sin(rad)};
  });
  const TIPS = [
    'Слабее всего продукт. Покажите первым пользователям хотя бы черновик.',
    'Слабее всего рынок. Найдите трёх человек, у которых эта проблема есть сейчас.',
    'Слабее всего экономика. Назовите цену и проверьте её на трёх семьях.',
    'Слабее всего команда. Решите, кто отвечает за продажи, а кто за продукт.',
    'Слабее всего ясность идеи. Уложите её в одно предложение без слова «платформа».',
  ];
  const points = [...root.querySelectorAll('.demo-point')];
  const scores = points.map(point => Number(point.getAttribute('aria-valuenow')));
  const scoreNode = root.querySelector('[data-demo-score]');
  const deltaNode = root.querySelector('[data-demo-delta]');
  const noteNode = root.querySelector('[data-demo-note]');

  const place = index => ({
    x: CENTER.x + AXES[index].x * RADIUS * scores[index] / 100,
    y: CENTER.y + AXES[index].y * RADIUS * scores[index] / 100,
  });

  function render() {
    const spots = scores.map((_, index) => place(index));
    shape.setAttribute('points', spots.map(spot => `${spot.x.toFixed(1)},${spot.y.toFixed(1)}`).join(' '));
    points.forEach((point, index) => {
      point.querySelectorAll('circle').forEach(circle => {
        circle.setAttribute('cx', spots[index].x.toFixed(1));
        circle.setAttribute('cy', spots[index].y.toFixed(1));
      });
      point.setAttribute('aria-valuenow', String(scores[index]));
    });
    const average = Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length);
    const delta = average - FIRST_AVERAGE;
    scoreNode.textContent = String(average);
    deltaNode.textContent = `${delta >= 0 ? '+' : '−'}${Math.abs(delta)} с первой оценки`;
    const weakest = scores.indexOf(Math.min(...scores));
    if (noteNode.textContent !== TIPS[weakest]) noteNode.textContent = TIPS[weakest];
  }

  const set = (index, value) => {
    scores[index] = Math.max(8, Math.min(100, Math.round(value)));
    render();
  };

  // Положение указателя переводится в координаты SVG и проецируется на ось точки.
  function scoreAt(event, index) {
    const matrix = svg.getScreenCTM();
    if (!matrix) return scores[index];
    const spot = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    const along = (spot.x - CENTER.x) * AXES[index].x + (spot.y - CENTER.y) * AXES[index].y;
    return along / RADIUS * 100;
  }

  points.forEach((point, index) => {
    point.addEventListener('pointerdown', event => {
      point.setPointerCapture(event.pointerId);
      point.classList.add('is-dragging');
      root.classList.add('is-touched');
      set(index, scoreAt(event, index));
    });
    point.addEventListener('pointermove', event => {
      if (point.hasPointerCapture(event.pointerId)) set(index, scoreAt(event, index));
    });
    const release = () => point.classList.remove('is-dragging');
    point.addEventListener('pointerup', release);
    point.addEventListener('pointercancel', release);
    point.addEventListener('keydown', event => {
      const step = {ArrowUp: 5, ArrowRight: 5, ArrowDown: -5, ArrowLeft: -5}[event.key];
      if (!step) return;
      event.preventDefault();
      root.classList.add('is-touched');
      set(index, scores[index] + step);
    });
  });
})();

// Заставка: Бруно доходит до середины (CSS, 0,8 с), останавливается и машет. Клик или клавиша показывают итог сразу.
(() => {
  const root = document.documentElement;
  const scene = document.querySelector('[data-welcome]');
  const bear = scene?.querySelector('.bruno-art');
  if (!bear) return;
  const wave = animate => window.Bruno?.pose('wave', bear, {animate, base: true});
  if (root.classList.contains('welcome-static')) { wave(false); return; }
  const arrive = setTimeout(() => wave(true), 800);
  const skip = () => {
    clearTimeout(arrive);
    root.classList.add('welcome-static');
    wave(false);
  };
  window.addEventListener('keydown', skip, {once: true});
  scene.addEventListener('pointerdown', skip, {once: true});
  setTimeout(() => {
    window.removeEventListener('keydown', skip);
    scene.removeEventListener('pointerdown', skip);
  }, 1800);
})();

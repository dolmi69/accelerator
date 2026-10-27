// Заставка при заходе: Бруно доходит до середины (CSS, 0,8 с), останавливается и машет; потом заставка
// растворяется сама (CSS). Клик или клавиша убирают её сразу.
(() => {
  const root = document.documentElement;
  const intro = document.querySelector('[data-intro]');
  if (!intro) return;
  if (!root.classList.contains('intro-on')) { intro.remove(); return; }
  const bear = intro.querySelector('.bruno-art');
  const arrive = setTimeout(() => window.Bruno?.pose('wave', bear, {base: true}), 800);
  const finish = () => {
    clearTimeout(arrive);
    root.classList.remove('intro-on');
    intro.remove();
  };
  const skip = () => {
    root.classList.add('intro-skip');
    setTimeout(finish, 220);
  };
  intro.addEventListener('animationend', event => { if (event.target === intro) finish(); });
  intro.addEventListener('pointerdown', skip, {once: true});
  window.addEventListener('keydown', skip, {once: true});
  // Подстраховка: заставка не остаётся на экране, даже если анимации не сработали.
  setTimeout(finish, 3200);
})();

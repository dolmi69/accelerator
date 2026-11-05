// Заставка при заходе: Бруно доходит до середины (CSS, 1,2 с), останавливается и машет; потом заставка
// растворяется сама (CSS). Клик или клавиша убирают её сразу.
(() => {
  const root = document.documentElement;
  const intro = document.querySelector('[data-intro]');
  if (!intro) return;
  if (!root.classList.contains('intro-on')) { intro.remove(); return; }
  const bear = intro.querySelector('.bruno-art');
  const arrive = setTimeout(() => window.Bruno?.pose('wave', bear, {base: true}), 1200);
  // Страница под заставкой проявляется одновременно с её растворением (app.css: intro-hold → intro-release).
  const release = () => {
    root.classList.add('intro-release');
    root.classList.remove('intro-hold');
  };
  const fade = setTimeout(release, 2300);
  const finish = () => {
    clearTimeout(arrive);
    clearTimeout(fade);
    release();
    root.classList.remove('intro-on');
    intro.remove();
    setTimeout(() => root.classList.remove('intro-release'), 900);
  };
  const skip = () => {
    clearTimeout(fade);
    release();
    root.classList.add('intro-skip');
    setTimeout(finish, 420);
  };
  intro.addEventListener('animationend', event => { if (event.target === intro) finish(); });
  intro.addEventListener('pointerdown', skip, {once: true});
  window.addEventListener('keydown', skip, {once: true});
  // Подстраховка: заставка не остаётся на экране, даже если анимации не сработали.
  setTimeout(finish, 3800);
})();

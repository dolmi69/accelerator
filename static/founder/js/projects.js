(() => {
  const carousel = document.querySelector('.project-carousel');
  if (!carousel) return;
  const track = carousel.querySelector('.project-slides');
  const slides = [...track.querySelectorAll('.project-slide')];
  const previous = carousel.querySelector('[data-carousel-prev]');
  const next = carousel.querySelector('[data-carousel-next]');
  const counter = carousel.querySelector('[data-carousel-counter]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let active = 0;
  let scrollTimer;

  const position = index => slides[index].offsetLeft - slides[0].offsetLeft;
  function update() {
    active = slides.reduce((best, slide, index) =>
      Math.abs(position(index) - track.scrollLeft) < Math.abs(position(best) - track.scrollLeft) ? index : best, 0);
    previous.disabled = active === 0;
    next.disabled = active === slides.length - 1;
    counter.textContent = `${active + 1} / ${slides.length}`;
    // Ссылки вне видимого слайда исключаем из последовательного Tab-перехода.
    slides.forEach((slide, index) => { slide.inert = index !== active; });
  }
  function move(index, smooth = true) {
    const target = Math.max(0, Math.min(slides.length - 1, index));
    track.scrollTo({left: position(target), behavior: smooth && !reducedMotion.matches ? 'smooth' : 'instant'});
  }
  previous.addEventListener('click', () => move(active - 1));
  next.addEventListener('click', () => move(active + 1));
  track.addEventListener('scroll', () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(update, 100);
  }, {passive: true});
  track.addEventListener('keydown', event => {
    if (event.target !== track || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    move(active + (event.key === 'ArrowRight' ? 1 : -1));
  });
  new ResizeObserver(() => move(active, false)).observe(track);
  update();
})();

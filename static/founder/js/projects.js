(() => {
  const carousel = document.querySelector('.project-carousel');
  if (!carousel) return;
  const track = carousel.querySelector('.project-slides');
  const allSlides = [...track.querySelectorAll('.project-slide')];
  const previous = carousel.querySelector('[data-carousel-prev]');
  const next = carousel.querySelector('[data-carousel-next]');
  const counter = carousel.querySelector('[data-carousel-counter]');
  const controls = carousel.querySelector('[data-carousel-controls]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let slides = allSlides;
  let active = 0;
  let scrollTimer;

  const position = index => slides[index].offsetLeft - slides[0].offsetLeft;
  function update() {
    if (!slides.length) return;
    active = slides.reduce((best, slide, index) =>
      Math.abs(position(index) - track.scrollLeft) < Math.abs(position(best) - track.scrollLeft) ? index : best, 0);
    previous.disabled = active === 0;
    next.disabled = active === slides.length - 1;
    counter.textContent = `${active + 1} / ${slides.length}`;
    // Ссылки вне видимого слайда исключаем из последовательного Tab-перехода.
    slides.forEach((slide, index) => { slide.inert = index !== active; });
  }
  function move(index, smooth = true) {
    if (!slides.length) return;
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

  // Поиск по названию: прячет чужие слайды, карусель листает только найденные.
  const input = carousel.querySelector('[data-project-search]');
  if (!input) return;
  const empty = carousel.querySelector('[data-project-search-empty]');
  const status = carousel.querySelector('[data-project-search-status]');
  const clear = carousel.querySelector('.project-search-clear');
  const normalize = text => text.toLocaleLowerCase('ru').replaceAll('ё', 'е').replace(/\s+/g, ' ').trim();
  const names = allSlides.map(slide => normalize(slide.dataset.projectName || ''));
  const plural = count => {
    const mod10 = count % 10, mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return 'проект';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'проекта';
    return 'проектов';
  };

  function filter() {
    const query = normalize(input.value);
    allSlides.forEach((slide, index) => { slide.hidden = Boolean(query) && !names[index].includes(query); });
    slides = allSlides.filter(slide => !slide.hidden);
    slides.forEach((slide, index) => {
      slide.setAttribute('aria-label', `${index + 1} из ${slides.length}: ${slide.dataset.projectName}`);
    });
    const found = slides.length > 0;
    track.hidden = !found;
    controls.hidden = !found;
    empty.hidden = found;
    clear.hidden = !input.value;
    carousel.querySelector('[data-project-search-query]').textContent = input.value.trim();
    status.textContent = !query ? '' : found ? `Найдено: ${slides.length} ${plural(slides.length)}` : 'Ничего не нашлось';
    if (found) {
      track.scrollLeft = 0;
      update();
    }
  }

  function reset() {
    input.value = '';
    filter();
    input.focus();
  }

  input.addEventListener('input', filter);
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape' && input.value) {
      event.preventDefault();
      reset();
    }
  });
  carousel.querySelectorAll('[data-project-search-reset]').forEach(button => button.addEventListener('click', reset));
  // Браузер мог восстановить текст поля при возврате «Назад».
  if (input.value) filter();
})();

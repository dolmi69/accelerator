// «Мои проекты»: поиск по названию. Найденные строки переезжают на новые места плавно (FLIP),
// при пустом результате — сообщение и сброс; Escape и крестик очищают поле.
(() => {
  const section = document.querySelector('[data-projects]');
  const input = section?.querySelector('[data-project-search]');
  if (!input) return;
  const rows = [...section.querySelectorAll('.project-row')];
  const list = section.querySelector('[data-project-list]');
  const empty = section.querySelector('[data-project-search-empty]');
  const status = section.querySelector('[data-project-search-status]');
  const clear = section.querySelector('.project-search-clear');
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const normalize = text => text.toLocaleLowerCase('ru').replaceAll('ё', 'е').replace(/\s+/g, ' ').trim();
  const names = rows.map(row => normalize(row.dataset.projectName || ''));
  const plural = count => {
    const mod10 = count % 10, mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return 'проект';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'проекта';
    return 'проектов';
  };

  function filter() {
    const query = normalize(input.value);
    // FLIP: запомнить, где строки были, спрятать лишние, сдвинуть оставшиеся из старых мест в новые.
    const before = new Map(rows.filter(row => !row.hidden).map(row => [row, row.getBoundingClientRect().top]));
    rows.forEach((row, index) => { row.hidden = Boolean(query) && !names[index].includes(query); });
    const visible = rows.filter(row => !row.hidden);
    if (!reduce.matches) {
      visible.forEach(row => {
        const top = before.get(row);
        if (top === undefined) return;
        const shift = top - row.getBoundingClientRect().top;
        if (!shift) return;
        row.animate([{transform: `translateY(${shift}px)`}, {transform: 'none'}], {duration: 240, easing: 'cubic-bezier(.2,.7,.2,1)'});
      });
    }
    const found = visible.length > 0;
    list.hidden = !found;
    empty.hidden = found;
    clear.hidden = !input.value;
    section.querySelector('[data-project-search-query]').textContent = input.value.trim();
    status.textContent = !query ? status.dataset.total : found ? `Найдено: ${visible.length} ${plural(visible.length)}` : 'Ничего не нашлось';
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
  section.querySelectorAll('[data-project-search-reset]').forEach(button => button.addEventListener('click', reset));
  // Браузер мог восстановить текст поля при возврате «Назад».
  if (input.value) filter();
})();

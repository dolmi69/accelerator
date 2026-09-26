(() => {
  const characters = [...document.querySelectorAll('.bruno-art')];
  if (!characters.length) return;
  const visible = new Set(characters);
  const updatePlayback = () => characters.forEach(character => {
    character.toggleAttribute('data-paused', document.hidden || !visible.has(character));
  });
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) visible.add(entry.target);
        else visible.delete(entry.target);
      });
      updatePlayback();
    });
    characters.forEach(character => observer.observe(character));
  }
  document.addEventListener('visibilitychange', updatePlayback);
  updatePlayback();
  characters.forEach(character => {
    const button = character.querySelector('.bruno-greet');
    if (!button) return;
    button.hidden = false;
    let greetingTimer;
    button.addEventListener('click', () => {
      if (character.classList.contains('bruno-greeting')) return;
      character.classList.add('bruno-greeting');
      clearTimeout(greetingTimer);
      greetingTimer = setTimeout(() => character.classList.remove('bruno-greeting'), 1400);
    });
  });
})();

(() => {
  const pet = document.querySelector('[data-bruno-pet]');
  if (!pet) return;
  const toggle = pet.querySelector('.pet-toggle');
  const key = `bruno-pet-collapsed:${pet.dataset.user}`;
  const setCollapsed = collapsed => {
    pet.classList.toggle('is-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', collapsed ? 'Показать Бруно' : 'Свернуть Бруно');
    toggle.textContent = collapsed ? 'Бруно' : '−';
  };
  try { setCollapsed(localStorage.getItem(key) === '1'); } catch (_) { /* Storage can be disabled. */ }
  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    const collapsed = !pet.classList.contains('is-collapsed');
    setCollapsed(collapsed);
    try { localStorage.setItem(key, collapsed ? '1' : '0'); } catch (_) { /* Keep working without persistence. */ }
  });
})();

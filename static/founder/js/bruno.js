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
  const projectLink = pet.querySelector('.pet-project');
  const setCollapsed = collapsed => {
    pet.classList.toggle('is-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', collapsed ? 'Показать Бруно' : 'Свернуть Бруно');
    toggle.textContent = collapsed ? 'Бруно' : '−';
  };
  // Every page starts with Bruno visible in the lower-right corner.
  setCollapsed(false);
  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    const collapsed = !pet.classList.contains('is-collapsed');
    setCollapsed(collapsed);
    if (pet.style.left) moveTo(parseFloat(pet.style.left), parseFloat(pet.style.top));
  });

  const edge = 12;
  const clamp = (value, max) => Math.min(Math.max(value, edge), Math.max(edge, max));
  const moveTo = (left, top) => {
    pet.style.left = `${clamp(left, window.innerWidth - pet.offsetWidth - edge)}px`;
    pet.style.top = `${clamp(top, window.innerHeight - pet.offsetHeight - edge)}px`;
    pet.style.right = 'auto';
    pet.style.bottom = 'auto';
  };

  let drag = null;
  let suppressClick = false;
  projectLink.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const rect = pet.getBoundingClientRect();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY,
      left: rect.left, top: rect.top, moved: false };
    suppressClick = false;
    projectLink.setPointerCapture(event.pointerId);
  });
  projectLink.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 6) return;
    drag.moved = true;
    pet.classList.add('is-dragging');
    moveTo(drag.left + dx, drag.top + dy);
  });
  const finishDrag = event => {
    if (!drag || event.pointerId !== drag.id) return;
    suppressClick = drag.moved && event.type === 'pointerup';
    drag = null;
    pet.classList.remove('is-dragging');
    if (suppressClick) window.setTimeout(() => { suppressClick = false; }, 300);
  };
  projectLink.addEventListener('pointerup', finishDrag);
  projectLink.addEventListener('pointercancel', finishDrag);
  projectLink.addEventListener('lostpointercapture', finishDrag);
  projectLink.addEventListener('click', event => {
    if (!suppressClick) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    suppressClick = false;
  }, true);
  window.addEventListener('resize', () => {
    if (pet.style.left) moveTo(parseFloat(pet.style.left), parseFloat(pet.style.top));
  });
})();

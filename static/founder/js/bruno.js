// Бруно оживает: моргает, меняет позы по событиям (думает, отвечает, радуется монетам) и откликается на касания.
// Позы — области листа bruno-reference.png в пикселях PNG; рамка — место позы на сцене 300×300 (как в _bruno.html).
(() => {
  const SVG = 'http://www.w3.org/2000/svg';
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const POSES = {
    wave: {frame: [10, 0, 280, 295], view: '30 25 680 690', clip: 'M30 25 H635 V295 H705 V485 H625 V720 H30 Z',
      eyes: [[249, 297, 56, 66], [434, 243, 57, 67]], lid: '#b5653a'},
    happy: {frame: [10, 5, 280, 285], view: '640 60 340 322', clip: 'M640 60 H980 V160 H962 V390 H710 V275 H640 Z'},
    reading: {frame: [30, 20, 240, 273], view: '690 730 248 275', eyes: [[777, 828, 20, 23], [851, 821, 18, 23]], lid: '#ad5f36'},
    sleepy: {frame: [5, 127, 290, 165], view: '20 790 370 204'},
    // Слева от Бруно с сердечками — лапа большого Бруно: её отрезаем.
    love: {frame: [10, 35, 280, 257], view: '660 424 292 268', clip: 'M705 424 H952 V692 H660 V488 H705 Z'},
    wink: {frame: [10, 18, 280, 274], view: '978 430 272 266'},
    honey: {frame: [10, 2, 280, 290], view: '402 722 256 266'},
    walk: {frame: [16, 2, 268, 290], view: '990 738 236 256'},
    serious: {frame: [20, 25, 260, 265], view: '967 112 287 275', clip: 'M997 112 H1254 V390 H967 V150 H997 Z'},
  };
  const MOOD_POSE = {curious: 'wave', confident: 'happy', focused: 'reading', sleepy: 'sleepy'};
  const make = (tag, attrs) => {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  };
  // Веко — пятно цвета шерсти вокруг глаза, опускается сверху; при закрытых глазах видна линия ресниц.
  const eyelid = ([cx, cy, rx, ry], color) => {
    const eye = make('g', {class: 'bruno-eye'});
    eye.append(make('ellipse', {class: 'bruno-lid', cx, cy, rx: rx + 4, ry: ry + 4, fill: color}));
    eye.append(make('path', {class: 'bruno-lash', fill: 'none', stroke: '#4a2516', 'stroke-linecap': 'round',
      'stroke-width': Math.max(4, Math.round(rx * 0.16)),
      d: `M${cx - rx * 0.8} ${cy + ry * 0.15} Q${cx} ${cy + ry * 0.75} ${cx + rx * 0.8} ${cy + ry * 0.15}`}));
    return eye;
  };

  let uid = 0;
  function setup(art) {
    if (art.classList.contains('bruno-investor')) return null;
    const poseSvg = art.querySelector('.bruno-character > svg:not(.bruno-accessories)');
    const image = poseSvg?.querySelector(':scope > image');
    if (!image) return null;
    const mood = (art.className.match(/bruno-(curious|confident|focused|sleepy)\b/) || [])[1];
    const base = POSES[art.dataset.pose] ? art.dataset.pose : MOOD_POSE[mood] || 'wave';
    const clip = make('clipPath', {id: `bruno-pose-${++uid}`});
    const clipPath = make('path', {});
    clip.append(clipPath);
    const defs = make('defs', {});
    defs.append(clip);
    poseSvg.prepend(defs);
    const lids = make('g', {class: 'bruno-lids'});
    poseSvg.append(lids);
    const state = {art, poseSvg, image, clip, clipPath, lids, base, current: null};
    apply(state, base);
    return state;
  }

  function apply(state, name) {
    const pose = POSES[name];
    const [x, y, width, height] = pose.frame;
    Object.entries({x, y, width, height, viewBox: pose.view}).forEach(([key, value]) => state.poseSvg.setAttribute(key, value));
    const [vx, vy, vw, vh] = pose.view.split(' ').map(Number);
    state.clipPath.setAttribute('d', pose.clip || `M${vx} ${vy} H${vx + vw} V${vy + vh} H${vx} Z`);
    state.image.setAttribute('clip-path', `url(#${state.clip.id})`);
    state.lids.replaceChildren(...(pose.eyes || []).map(eye => eyelid(eye, pose.lid)));
    state.current = name;
    state.target = name;
    state.art.dataset.pose = name;
    // Аксессуары из гардероба подогнаны под постоянную позу: на время другой позы их не видно.
    state.art.toggleAttribute('data-pose-temp', name !== state.base);
  }

  // Смена позы: Бруно на миг приседает, поза меняется, он распрямляется.
  function pose(state, name, animate = true) {
    // Сравниваем с позой, к которой Бруно уже идёт: иначе быстрый «туда и обратно» застревает в первой.
    if (!state || !POSES[name] || state.target === name) return;
    clearTimeout(state.swapTimer);
    state.target = name;
    state.art.classList.remove('is-swapping');
    if (state.current === name) return;
    if (!animate || reduce.matches) { apply(state, name); return; }
    state.art.classList.remove('is-popping');
    state.art.classList.add('is-swapping');
    state.swapTimer = setTimeout(() => {
      apply(state, name);
      state.art.classList.remove('is-swapping');
      void state.art.offsetWidth;
      state.art.classList.add('is-popping');
      state.swapTimer = setTimeout(() => state.art.classList.remove('is-popping'), 340);
    }, 110);
  }

  function react(state, name, duration) {
    clearTimeout(state.reactTimer);
    pose(state, name);
    state.reactTimer = setTimeout(() => pose(state, state.base), duration);
  }

  function blink(state) {
    state.art.classList.remove('is-blinking');
    void state.art.offsetWidth;
    state.art.classList.add('is-blinking');
    setTimeout(() => state.art.classList.remove('is-blinking'), 260);
  }
  // Моргает в случайный момент раз в 2,5–6 с, иногда дважды подряд; за экраном и в скрытой вкладке — нет.
  function scheduleBlink(state) {
    state.blinkTimer = setTimeout(() => {
      const art = state.art;
      if (!reduce.matches && !art.hasAttribute('data-paused') && !art.classList.contains('eyes-closed') && POSES[state.current].eyes) {
        blink(state);
        if (Math.random() < 0.18) setTimeout(() => blink(state), 330);
      }
      scheduleBlink(state);
    }, 2500 + Math.random() * 3500);
  }

  const states = [...document.querySelectorAll('.bruno-art')].map(setup).filter(Boolean);
  const byArt = new Map(states.map(state => [state.art, state]));
  const pick = scope => scope ? [byArt.get(scope.closest?.('.bruno-art') || scope.querySelector?.('.bruno-art'))].filter(Boolean) : states;
  states.forEach(scheduleBlink);

  window.Bruno = {
    // Поставить позу; base: true делает её постоянной (к ней Бруно возвращается после реакций).
    pose(name, scope, {animate = true, base = false} = {}) {
      pick(scope).forEach(state => {
        clearTimeout(state.reactTimer);
        if (base && POSES[name]) state.base = name;
        pose(state, name || state.base, animate);
      });
    },
    // Короткая реакция на событие: поза на duration мс, потом обратно.
    react(name, duration = 1600, scope) { pick(scope).forEach(state => react(state, name, duration)); },
    // Состояние разговора: thinking — читает книгу, talking — машет и покачивается, idle — как обычно.
    state(name) {
      states.forEach(state => {
        clearTimeout(state.reactTimer);
        state.art.classList.toggle('is-thinking', name === 'thinking');
        state.art.classList.toggle('is-talking', name === 'talking');
        pose(state, name === 'thinking' ? 'reading' : name === 'talking' ? 'wave' : state.base);
      });
    },
    closeEyes(closed, scope) { pick(scope).forEach(state => state.art.classList.toggle('eyes-closed', closed)); },
  };

  // Видимость: за экраном и в скрытой вкладке анимации стоят.
  const visible = new Set(states.map(state => state.art));
  const updatePlayback = () => states.forEach(({art}) => {
    art.toggleAttribute('data-paused', document.hidden || !visible.has(art));
  });
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) visible.add(entry.target);
        else visible.delete(entry.target);
      });
      updatePlayback();
    });
    states.forEach(({art}) => observer.observe(art));
  }
  document.addEventListener('visibilitychange', updatePlayback);
  updatePlayback();

  // «Поздороваться»: Бруно подмигивает и машет.
  states.forEach(state => {
    const button = state.art.querySelector('.bruno-greet');
    if (!button) return;
    button.hidden = false;
    let greetingTimer;
    button.addEventListener('click', () => {
      if (state.art.classList.contains('bruno-greeting')) return;
      state.art.classList.add('bruno-greeting');
      react(state, 'wink', 1500);
      clearTimeout(greetingTimer);
      greetingTimer = setTimeout(() => state.art.classList.remove('bruno-greeting'), 1500);
    });
  });

  // После успешного действия (сообщение «готово» сверху страницы) Бруно показывает сердечки.
  if (document.querySelector('.flash-success')) setTimeout(() => window.Bruno.react('love', 1800), 450);
})();

(() => {
  const pet = document.querySelector('[data-bruno-pet]');
  if (!pet) return;
  const art = pet.querySelector('.bruno-art');
  const toggle = pet.querySelector('.pet-toggle');
  const projectLink = pet.querySelector('.pet-project');
  const setCollapsed = collapsed => {
    pet.classList.toggle('is-collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-label', collapsed ? 'Показать Бруно' : 'Свернуть Бруно');
    toggle.textContent = collapsed ? 'Бруно' : '−';
  };
  // On a phone Bruno starts folded into a small button so he never covers the page; on a computer he is visible.
  setCollapsed(window.matchMedia('(max-width:680px)').matches);
  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    const collapsed = !pet.classList.contains('is-collapsed');
    pet.classList.add('is-toggling');
    setTimeout(() => pet.classList.remove('is-toggling'), 320);
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

  // Бруно, которого несут, радуется и раскачивается против движения; после броска качается и успокаивается.
  let drag = null;
  let suppressClick = false;
  projectLink.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const rect = pet.getBoundingClientRect();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, lastX: event.clientX,
      left: rect.left, top: rect.top, moved: false };
    suppressClick = false;
    projectLink.setPointerCapture(event.pointerId);
  });
  projectLink.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 6) return;
    if (!drag.moved) window.Bruno?.pose('happy', art);
    drag.moved = true;
    pet.classList.add('is-dragging');
    art?.style.setProperty('--swing', `${Math.max(-16, Math.min(16, (drag.lastX - event.clientX) * 1.4))}deg`);
    drag.lastX = event.clientX;
    moveTo(drag.left + dx, drag.top + dy);
  });
  const finishDrag = event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (drag.moved) window.Bruno?.pose(null, art);
    suppressClick = drag.moved && event.type === 'pointerup';
    drag = null;
    pet.classList.remove('is-dragging');
    art?.style.setProperty('--swing', '0deg');
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

  // Минуту без действий Бруно задрёмывает; первое движение будит его, и он потягивается.
  const IDLE_MS = 60000;
  let idleTimer;
  let lastActivity = 0;
  const doze = () => {
    if (document.hidden || pet.classList.contains('is-collapsed') || drag) { idleTimer = setTimeout(doze, IDLE_MS); return; }
    pet.classList.add('is-dozing');
    window.Bruno?.pose('sleepy', art);
  };
  const wake = () => {
    const now = Date.now();
    if (now - lastActivity < 400 && !pet.classList.contains('is-dozing')) return;
    lastActivity = now;
    clearTimeout(idleTimer);
    if (pet.classList.contains('is-dozing')) {
      pet.classList.remove('is-dozing');
      window.Bruno?.pose(null, art);
      art?.classList.add('is-waking');
      setTimeout(() => art?.classList.remove('is-waking'), 900);
    }
    idleTimer = setTimeout(doze, IDLE_MS);
  };
  ['pointermove', 'pointerdown', 'keydown', 'scroll', 'touchstart'].forEach(type => window.addEventListener(type, wake, {passive: true}));
  wake();
})();

// Вход и регистрация: Бруно следит за вводом и закрывает глаза, пока вы печатаете пароль.
(() => {
  const form = document.querySelector('[data-bruno-watch]');
  const art = document.querySelector('[data-bruno-watcher] .bruno-art');
  if (!form || !art || !window.Bruno) return;
  const passwordFocused = () => document.activeElement?.type === 'password' && form.contains(document.activeElement);
  form.addEventListener('focusin', () => window.Bruno.closeEyes(passwordFocused(), art));
  form.addEventListener('focusout', () => setTimeout(() => window.Bruno.closeEyes(passwordFocused(), art), 0));
  // Бруно поворачивается к тексту: чем длиннее строка, тем дальше вправо он смотрит.
  form.addEventListener('input', event => {
    if (event.target.type === 'password') return;
    const length = event.target.value?.length || 0;
    art.style.setProperty('--bruno-look', `${Math.max(-5, Math.min(5, length * 0.4 - 4))}deg`);
  });
  form.addEventListener('focusout', () => art.style.removeProperty('--bruno-look'));
  if (document.querySelector('.form-error, .field-error')) window.Bruno.react('serious', 2200, art);
})();

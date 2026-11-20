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
    // Remove background specks and the one-pixel matte only around the walking cutout.
    const walkFilter = make('filter', {id: `bruno-walk-edge-${uid}`, x: 990, y: 738,
      width: 236, height: 256, filterUnits: 'userSpaceOnUse', 'color-interpolation-filters': 'sRGB'});
    const alpha = make('feComponentTransfer', {in: 'SourceAlpha', result: 'solid-alpha'});
    alpha.append(make('feFuncA', {type: 'linear', slope: 1.25, intercept: -0.15}));
    walkFilter.append(alpha,
      make('feMorphology', {in: 'solid-alpha', operator: 'erode', radius: 1, result: 'clean-alpha'}),
      make('feComposite', {in: 'SourceGraphic', in2: 'clean-alpha', operator: 'in'}));
    defs.append(walkFilter);
    // «След» прежней позы под текущей: при смене позы старый кадр растворяется, новый проявляется поверх.
    const ghostClip = make('clipPath', {id: `bruno-ghost-${uid}`});
    const ghostClipPath = make('path', {});
    ghostClip.append(ghostClipPath);
    defs.append(ghostClip);
    poseSvg.prepend(defs);
    const ghost = make('svg', {class: 'bruno-ghost', overflow: 'hidden', opacity: 0, 'aria-hidden': 'true'});
    const ghostImage = make('image', {href: image.getAttribute('href'), width: image.getAttribute('width'),
      height: image.getAttribute('height')});
    ghost.append(ghostImage);
    poseSvg.before(ghost);
    const lids = make('g', {class: 'bruno-lids'});
    poseSvg.append(lids);
    // Дыхание разных Бруно на одной странице не совпадает по фазе.
    art.style.setProperty('--bruno-phase', `${(-Math.random() * 4.8).toFixed(2)}s`);
    const state = {art, stage: art.querySelector('.bruno-stage'), poseSvg, image, clip, clipPath, walkFilter, lids,
      ghost, ghostImage, ghostClip, ghostClipPath, fades: [], base, current: null};
    apply(state, base);
    return state;
  }

  // Рамка, область листа и вырез позы на любом слое: текущем или «следе».
  function paint(svg, clip, clipPath, image, walkFilter, name) {
    const pose = POSES[name];
    const [x, y, width, height] = pose.frame;
    Object.entries({x, y, width, height, viewBox: pose.view}).forEach(([key, value]) => svg.setAttribute(key, value));
    const [vx, vy, vw, vh] = pose.view.split(' ').map(Number);
    clipPath.setAttribute('d', pose.clip || `M${vx} ${vy} H${vx + vw} V${vy + vh} H${vx} Z`);
    image.setAttribute('clip-path', `url(#${clip.id})`);
    if (name === 'walk') image.setAttribute('filter', `url(#${walkFilter.id})`);
    else image.removeAttribute('filter');
  }

  function apply(state, name) {
    const pose = POSES[name];
    paint(state.poseSvg, state.clip, state.clipPath, state.image, state.walkFilter, name);
    state.lids.replaceChildren(...(pose.eyes || []).map(eye => eyelid(eye, pose.lid)));
    state.current = name;
    state.target = name;
    state.art.dataset.pose = name;
    // Аксессуары из гардероба подогнаны под постоянную позу: на время другой позы их не видно.
    state.art.toggleAttribute('data-pose-temp', name !== state.base);
  }

  // Смена позы без мигания: новая поза проявляется поверх прежней, а прежняя уходит чуть позже,
  // поэтому фигура не просвечивает посередине. Бруно при этом мягко приседает и распрямляется.
  // Раньше вся фигура гасла до 30% и «выпрыгивала».
  const FADE_IN = {duration: 180, easing: 'cubic-bezier(0,0,.2,1)'};
  const FADE_OUT = {duration: 200, delay: 40, easing: 'cubic-bezier(.4,0,1,1)', fill: 'both'};
  const SETTLE = {duration: 380, easing: 'ease-in-out'};
  const stopFades = state => {
    state.fades.forEach(animation => animation.cancel());
    state.fades = [];
  };

  function pose(state, name, animate = true) {
    if (!state || !POSES[name] || state.target === name) return;
    state.target = name;
    if (state.current === name) return;
    stopFades(state);
    const from = state.current;
    if (!animate || reduce.matches || !from || typeof state.poseSvg.animate !== 'function') { apply(state, name); return; }
    paint(state.ghost, state.ghostClip, state.ghostClipPath, state.ghostImage, state.walkFilter, from);
    apply(state, name);
    state.fades = [
      state.ghost.animate([{opacity: 1}, {opacity: 0}], FADE_OUT),
      state.poseSvg.animate([{opacity: 0}, {opacity: 1}], FADE_IN),
      state.stage.animate([{transform: 'none'}, {transform: 'scale(.975, 1.02)', offset: 0.3}, {transform: 'none'}], SETTLE),
    ];
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
  // A choice made with the toggle is remembered, otherwise a folded Bruno unfolds again on every page.
  const remembered = (() => {
    try { return localStorage.getItem('bl-pet-collapsed'); } catch (_) { return null; }
  })();
  setCollapsed(remembered === null ? window.matchMedia('(max-width:680px)').matches : remembered === '1');
  toggle.hidden = false;
  toggle.addEventListener('click', () => {
    const collapsed = !pet.classList.contains('is-collapsed');
    pet.classList.add('is-toggling');
    setTimeout(() => pet.classList.remove('is-toggling'), 320);
    setCollapsed(collapsed);
    try { localStorage.setItem('bl-pet-collapsed', collapsed ? '1' : '0'); } catch (_) {}
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
  // Качание — затухающая пружина в requestAnimationFrame: угол следует за скоростью руки без рывков
  // и сам возвращается к нулю. Шаг считается по реальному времени кадра, поэтому одинаков при 60 и 120 Гц.
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const swing = {angle: 0, velocity: 0, target: 0, frame: 0, last: 0};
  // Маятник с периодом ~0,6 с и затуханием ζ≈0,3: после броска два заметных качания (12° → −4,7° → +1,9°), покой за 1,5 с.
  const STIFFNESS = 1.4e-4, DAMPING = 4e-4, MAX_SWING = 16;
  const swingStep = now => {
    const dt = Math.min(48, now - (swing.last || now - 16));
    swing.last = now;
    // Пока Бруно держат на месте, он повисает ровно.
    swing.target *= Math.pow(0.9, dt / 16);
    swing.velocity += ((swing.target - swing.angle) * STIFFNESS - swing.velocity * DAMPING * 16) * dt;
    swing.angle += swing.velocity * dt;
    const resting = Math.abs(swing.angle) < 0.05 && Math.abs(swing.velocity) < 0.0005 && Math.abs(swing.target) < 0.05;
    if (resting) {
      swing.angle = swing.velocity = swing.target = 0;
      swing.frame = 0;
      swing.last = 0;
    }
    art?.style.setProperty('--swing', `${swing.angle.toFixed(2)}deg`);
    if (!resting) swing.frame = requestAnimationFrame(swingStep);
  };
  const push = target => {
    if (reduceMotion.matches) return;
    swing.target = Math.max(-MAX_SWING, Math.min(MAX_SWING, target));
    if (!swing.frame) swing.frame = requestAnimationFrame(swingStep);
  };
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
    push((drag.lastX - event.clientX) * 1.4);
    drag.lastX = event.clientX;
    moveTo(drag.left + dx, drag.top + dy);
  });
  const finishDrag = event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (drag.moved) window.Bruno?.pose(null, art);
    suppressClick = drag.moved && event.type === 'pointerup';
    drag = null;
    pet.classList.remove('is-dragging');
    // Отпустили: пружина сама докачает Бруно до покоя.
    push(0);
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

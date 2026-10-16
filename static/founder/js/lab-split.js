/* Resize the existing panes in place: never remount the site or the composer. */
(() => {
  const layout = document.querySelector('.lab-workspace-layout');
  const splitter = document.getElementById('lab-splitter');
  if (!layout || !splitter) return;

  const storageKey = 'cofounder.lab.chat-share.v3';
  const mobile = window.matchMedia('(max-width: 1000px)');
  const defaultShare = 28;
  let share = defaultShare;
  let drag = null;
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved !== null && saved.trim() && Number.isFinite(Number(saved))) share = Number(saved);
  } catch (_) { /* The layout also works with storage disabled. */ }

  function bounds() {
    if (mobile.matches) return {min: 20, max: 80};
    const width = Math.max(1, layout.getBoundingClientRect().width - splitter.offsetWidth);
    // Both panes remain usable on narrower desktops.
    return {min: Math.min(40, Math.max(20, 280 / width * 100)),
      max: Math.max(40, Math.min(80, (width - 320) / width * 100))};
  }
  function apply(value) {
    const {min, max} = bounds();
    share = Math.max(min, Math.min(max, value));
    layout.style.setProperty('--lab-chat-share', String(share));
    splitter.setAttribute('aria-valuemin', String(Math.ceil(min)));
    splitter.setAttribute('aria-valuemax', String(Math.floor(max)));
    splitter.setAttribute('aria-valuenow', String(Math.round(share)));
    splitter.setAttribute('aria-valuetext', `Чат ${Math.round(share)}%, сайт ${100 - Math.round(share)}%`);
  }
  function save() {
    try { localStorage.setItem(storageKey, String(share)); } catch (_) { /* Optional preference. */ }
  }
  function finish(cancelled = false) {
    if (!drag) return;
    const previous = drag.previous;
    drag = null;
    layout.classList.remove('is-resizing');
    document.body.classList.remove('lab-split-dragging');
    if (cancelled) apply(previous);
    else save();
  }
  splitter.addEventListener('pointerdown', event => {
    if (mobile.matches || event.button !== 0) return;
    event.preventDefault();
    const rect = layout.getBoundingClientRect();
    drag = {left: rect.left, width: rect.width - splitter.offsetWidth, previous: share};
    splitter.setPointerCapture(event.pointerId);
    splitter.focus({preventScroll: true});
    layout.classList.add('is-resizing');
    document.body.classList.add('lab-split-dragging');
  });
  splitter.addEventListener('pointermove', event => {
    if (!drag) return;
    apply((event.clientX - drag.left - splitter.offsetWidth / 2) / drag.width * 100);
  });
  splitter.addEventListener('pointerup', () => finish());
  splitter.addEventListener('pointercancel', () => finish(true));
  splitter.addEventListener('lostpointercapture', () => finish());
  splitter.addEventListener('dblclick', () => { apply(defaultShare); save(); });
  splitter.addEventListener('keydown', event => {
    if (event.key === 'Escape') { finish(true); return; }
    const step = event.shiftKey ? 10 : 2;
    const {min, max} = bounds();
    const values = {ArrowLeft: share - step, ArrowRight: share + step, Home: min, End: max, Enter: defaultShare};
    if (!(event.key in values)) return;
    event.preventDefault();
    apply(values[event.key]); save();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && drag) finish(true); });
  window.addEventListener('blur', () => finish(true));
  window.addEventListener('resize', () => { if (!mobile.matches) apply(share); });
  mobile.addEventListener('change', () => { finish(true); if (!mobile.matches) apply(share); });
  apply(share);
})();

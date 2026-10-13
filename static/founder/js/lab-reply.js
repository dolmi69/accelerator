(() => {
  document.querySelectorAll('[data-lab-report-link]').forEach(link => {
    link.addEventListener('click', event => {
      const report = document.getElementById(link.hash.slice(1));
      const history = document.getElementById('lab-history');
      if (!report || !history) return;
      event.preventDefault();
      history.open = true;
      report.open = true;
      report.scrollIntoView({block: 'center'});
      report.querySelector('summary').focus({preventScroll: true});
    });
  });

  const reply = document.getElementById('lab-reply');
  // The greeting before a first request is guidance, not a timed report.
  if (!reply?.dataset.reportKey) return;
  const key = `lab-report:${reply.dataset.reportKey}`;
  const duration = 60_000;
  let remaining = duration, startedAt = null, timer = null, inView = false, finished = false;
  try {
    const saved = sessionStorage.getItem(key);
    if (saved !== null && Number.isFinite(Number(saved))) {
      remaining = Math.max(0, Math.min(duration, Number(saved)));
    }
  } catch (_) { /* The report still works when browser storage is unavailable. */ }

  function remember() {
    try { sessionStorage.setItem(key, String(remaining)); } catch (_) {}
  }

  function pause() {
    if (startedAt !== null) {
      remaining = Math.max(0, remaining - (performance.now() - startedAt));
      startedAt = null;
      clearTimeout(timer);
      timer = null;
      remember();
    }
  }

  function hide() {
    finished = true;
    remaining = 0;
    remember();
    // Do not leave keyboard focus inside a disappearing report.
    if (reply.contains(document.activeElement)) {
      document.querySelector('[data-lab-report-link]')?.focus({preventScroll: true});
    }
    reply.hidden = true;
    observer?.disconnect();
  }

  function sync() {
    if (finished) return;
    pause();
    if (remaining <= 0) { hide(); return; }
    if (!inView || reply.hidden || reply.closest('[hidden]')
        || document.visibilityState !== 'visible'
        || document.querySelector('.lab-viewer[open]')
        || document.querySelector('.lab-preview-card[aria-busy="true"]')) return;
    startedAt = performance.now();
    timer = setTimeout(sync, remaining);
  }

  const observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    inView = entries[0].isIntersecting && entries[0].intersectionRatio >= 0.35;
    sync();
  }, {threshold: [0, 0.35]}) : null;

  if (remaining <= 0) { hide(); return; }
  if (observer) observer.observe(reply);
  else {
    const checkPosition = () => {
      const rect = reply.getBoundingClientRect();
      const visible = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
      inView = rect.height > 0 && visible / rect.height >= 0.35;
      sync();
    };
    window.addEventListener('scroll', checkPosition, {passive: true});
    window.addEventListener('resize', checkPosition);
    checkPosition();
  }
  document.addEventListener('visibilitychange', sync);
  document.addEventListener('lab:preview-state', sync);
  window.addEventListener('pagehide', pause);
})();

(() => {
  'use strict';
  const start = document.getElementById('lab-start-form');
  if (!start) return;
  const frame = document.getElementById('lab-test-frame');
  const active = document.getElementById('lab-test-active');
  const review = document.getElementById('lab-test-review');
  const status = document.getElementById('lab-test-status');
  const csrf = start.querySelector('[name="csrfmiddlewaretoken"]').value;
  let session, sequence = 0, queue = [], flushing, stopped = false, paused = false;
  let duration = 0, lastTick = performance.now(), began = 0, visible = !document.hidden;
  const say = text => { status.hidden = !text; status.textContent = text; };
  const request = async (url, payload) => {
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin', headers: {
      'Content-Type': 'application/json', 'X-CSRFToken': csrf,
    }, body: JSON.stringify(payload)});
    const data = await response.json().catch(() => ({error: 'Перезагрузите страницу и начните тест снова.'}));
    if (!response.ok || data.saved !== true) { const error = new Error(data.error || 'Результат пока не сохранён. Попробуйте снова.'); error.status = response.status; throw error; }
    return data;
  };
  const tick = () => {
    const now = performance.now();
    if (session && !stopped && !paused && visible) duration += Math.min(now - lastTick, 5000) / 1000;
    lastTick = now;
  };
  const flush = () => {
    if (flushing) return flushing;
    flushing = (async () => {
      while (queue.length && session && !stopped) {
        const batch = queue.slice(0, 25);
        try { await request(session.events, {events: batch}); }
        catch (error) {
          if (error.status === 409) { queue = []; say('Время записи действий закончилось. Вы ещё можете отправить отзыв.'); return; }
          throw error;
        }
        queue.splice(0, batch.length);
      }
    })().finally(() => { flushing = null; });
    return flushing;
  };
  start.addEventListener('submit', async event => {
    event.preventDefault();
    const button = start.querySelector('button');
    button.disabled = true;
    try {
      const response = await fetch(start.action, {method: 'POST', credentials: 'same-origin', body: new FormData(start)});
      const data = await response.json();
      if (!response.ok || !data.session || !data.preview) throw new Error(data.error || 'Не удалось начать тест. Войдите в аккаунт и повторите.');
      session = data; lastTick = performance.now(); began = lastTick;
      frame.src = data.preview;
      document.getElementById('lab-test-intro').hidden = true;
      active.hidden = false; say('');
    } catch (error) { say(error.message); button.disabled = false; }
  });
  window.addEventListener('message', event => {
    const data = event.data;
    if (!session || stopped || paused || document.hidden || event.source !== frame.contentWindow || event.origin !== 'null') return;
    if (!data || data.source !== 'bruno-lab' || data.channel !== session.session || sequence >= 200) return;
    if (!['ready', 'click', 'scroll', 'form', 'error'].includes(data.kind)) return;
    // Whitelist a small schema; never forward arbitrary iframe messages.
    queue.push({sequence: ++sequence, kind: data.kind,
      target: typeof data.target === 'string' ? data.target.slice(0, 160) : '',
      label: typeof data.label === 'string' ? data.label.slice(0, 80) : '',
      depth: Number.isInteger(data.depth) && data.depth >= 0 && data.depth <= 100 ? data.depth : 0});
  });
  setInterval(() => {
    tick();
    if (session && !stopped) flush().catch(error => say('Связь прервалась: ' + error.message));
    if (session && !stopped && !paused && performance.now() - began >= 1800000) showReview();
  }, 4000);
  document.addEventListener('visibilitychange', () => { tick(); visible = !document.hidden; lastTick = performance.now(); });
  const showReview = () => { tick(); paused = true; active.hidden = true; review.hidden = false; review.scrollIntoView({behavior: 'smooth', block: 'start'}); };
  document.getElementById('lab-leave-test').addEventListener('click', showReview);
  document.getElementById('lab-resume-test').addEventListener('click', () => { paused = false; lastTick = performance.now(); review.hidden = true; active.hidden = false; });
  document.getElementById('lab-review-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.target, button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await flush();
      const result = await request(session.finish, {rating: form.rating.value ? Number(form.rating.value) : null,
        feedback: form.feedback.value.trim(), duration: Math.min(1800, Math.floor(duration))});
      stopped = true; frame.removeAttribute('src'); review.hidden = true; active.hidden = true;
      document.getElementById('lab-test-thanks').hidden = false; say('');
      window.coinsEarned?.(result.coins_earned, result.coins);
    } catch (error) { say(error.message); button.disabled = false; }
  });
  window.addEventListener('pagehide', () => {
    tick();
    if (!session || stopped) return;
    // A small final batch, without cookies or input content from the prototype.
    if (queue.length) fetch(session.events, {method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify({events: queue.slice(0, 25)})}).catch(() => {});
  });
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();

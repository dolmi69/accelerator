// Баланс монет в шапке и короткое уведомление о начислении.
(() => {
  const plural = n => (n % 10 === 1 && n % 100 !== 11) ? 'монета'
    : (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) ? 'монеты' : 'монет';

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  const COIN = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="#f6c768"/><circle cx="12" cy="12" r="5"/></svg>';

  // Баланс плавно досчитывает до нового значения, чип монет коротко «подпрыгивает».
  const countUp = (node, from, to) => {
    if (reduce.matches || from === to) { node.textContent = to; return; }
    const start = performance.now();
    const step = now => {
      const t = Math.min(1, (now - start) / 700);
      node.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - t, 3)));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  const show = (earned, balance) => {
    document.querySelectorAll('[data-coin-balance]').forEach(node => countUp(node, balance - earned, balance));
    document.querySelectorAll('.coin-chip').forEach(chip => {
      chip.classList.remove('is-earning'); void chip.offsetWidth; chip.classList.add('is-earning');
    });
    const toast = document.createElement('div');
    toast.className = 'coin-toast';
    toast.setAttribute('role', 'status');
    toast.innerHTML = COIN;
    toast.append(`+${earned} ${plural(earned)}`);
    document.body.append(toast);
    setTimeout(() => toast.classList.add('is-leaving'), 3200);
    setTimeout(() => toast.remove(), 3500);
  };

  // Чат перезагружает страницу после ответа, поэтому начисление переживает перезагрузку.
  window.coinsEarned = (earned, balance, {afterReload = false} = {}) => {
    if (!earned) return;
    if (!afterReload) return show(earned, balance);
    try { sessionStorage.setItem('coins-earned', JSON.stringify({earned, balance})); } catch (error) { /* приватный режим */ }
  };

  try {
    const pending = JSON.parse(sessionStorage.getItem('coins-earned') || 'null');
    sessionStorage.removeItem('coins-earned');
    if (pending) show(pending.earned, pending.balance);
  } catch (error) { /* хранилище недоступно — уведомление не обязательно */ }
})();

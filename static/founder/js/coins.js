// Баланс монет в шапке и короткое уведомление о начислении.
(() => {
  const plural = n => (n % 10 === 1 && n % 100 !== 11) ? 'монета'
    : (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) ? 'монеты' : 'монет';

  const show = (earned, balance) => {
    document.querySelectorAll('[data-coin-balance]').forEach(node => { node.textContent = balance; });
    const toast = document.createElement('div');
    toast.className = 'coin-toast';
    toast.setAttribute('role', 'status');
    toast.textContent = `🪙 +${earned} ${plural(earned)}`;
    document.body.append(toast);
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

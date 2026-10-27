// Кнопка «показать/скрыть пароль» на страницах входа и регистрации.
// Скрипт подключается только на этих страницах; без JS поля остаются обычными паролями.
(() => {
  // Что показывать на кнопке и в атрибутах доступности для текущего состояния поля.
  function describeToggle(shown) {
    return {
      type: shown ? 'text' : 'password',
      label: shown ? 'Скрыть пароль' : 'Показать пароль',
      pressed: Boolean(shown),
    };
  }

  // В node-тестах document нет: отдаём только чистую функцию.
  if (typeof document === 'undefined') {
    module.exports = { describeToggle };
    return;
  }

  const EYE ='<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M1 12s4-7.5 11-7.5S23 12 23 12s-4 7.5-11 7.5S1 12 1 12Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M1 12s4-7.5 11-7.5S23 12 23 12s-4 7.5-11 7.5S1 12 1 12Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 3l18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

  document.querySelectorAll('input[type="password"]').forEach(input => {
    if (input.dataset.toggleReady) return;
    input.dataset.toggleReady = '1';

    const wrap = document.createElement('span');
    wrap.className = 'password-field';
    input.parentNode.insertBefore(wrap, input);
    wrap.append(input);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'password-toggle';
    wrap.append(button);

    const apply = shown => {
      const state = describeToggle(shown);
      input.type = state.type;
      button.setAttribute('aria-label', state.label);
      button.setAttribute('title', state.label);
      button.setAttribute('aria-pressed', String(state.pressed));
      button.innerHTML = shown ? EYE_OFF : EYE;
    };

    button.addEventListener('click', () => {
      apply(input.type === 'password');
      input.focus({ preventScroll: true });
    });
    apply(false);
  });
})();

(() => {
  // Only the trusted host can confirm these named server actions.
  const pending = new Map();
  const call = (method, payload = {}) => new Promise((resolve, reject) => {
    if (pending.size >= 8) {reject(Object.assign(new Error('Дождитесь предыдущего действия.'), {code:'BUSY'})); return;}
    if (parent === window) { reject(new Error('Откройте работающий сайт в лаборатории.')); return; }
    const id = crypto.randomUUID();
    const timer = setTimeout(() => {
      pending.delete(id);
      parent.postMessage({type:'cofounder:app-cancel', id}, '*');
      reject(new Error('Подтверждение не получено. Проверьте историю перед повторной отправкой.'));
    }, 180000);
    pending.set(id, {resolve, reject, timer});
    parent.postMessage({type:'cofounder:app-call', id, method, payload}, '*');
  });
  window.addEventListener('message', event => {
    if (!event.isTrusted || event.source !== parent || event.data?.type !== 'cofounder:app-result') return;
    const request = pending.get(event.data.id);
    if (!request) return;
    pending.delete(event.data.id); clearTimeout(request.timer);
    if (event.data.ok) request.resolve(event.data.value);
    else request.reject(Object.assign(new Error(event.data.error || 'Действие не выполнено.'), {code:event.data.code}));
  });
  Object.defineProperty(window, 'BrunoApp', {value:Object.freeze({
    auth:Object.freeze({getSession:() => call('auth.getSession'), login:() => call('auth.login')}),
    results:Object.freeze({save:result => call('results.save', result), list:() => call('results.list')}),
    chat:Object.freeze({shareResult:result => call('chat.shareResult', result)}),
  }), writable:false, configurable:false});
  const notifyHeight = () => parent.postMessage({type:'cofounder:app-height', height:Math.ceil(document.documentElement.scrollHeight)}, '*');
  const ready = () => {
    // Custom calls to action use the same server allowlist as the inserted menu.
    document.addEventListener('click', event => {
      const link = event.target instanceof Element ? event.target.closest('a[data-app-route]') : null;
      if (!link) return;
      event.preventDefault(); event.stopImmediatePropagation();
      parent.postMessage({type:'cofounder:app-navigation', path:link.dataset.appRoute}, '*');
    });
    // Native submissions are disabled in the sandbox. Dispatch only the local
    // JS event so calculators and demos work without allowing network forms.
    const submitDemo = (form, submitter) => {
      if (!form.checkValidity()) { form.reportValidity(); return; }
      form.dispatchEvent(new SubmitEvent('submit', {bubbles:true, cancelable:true, submitter}));
    };
    document.addEventListener('click', event => {
      const button = event.target instanceof Element ? event.target.closest('button,input[type=submit]') : null;
      if (!button?.form || button.type !== 'submit' || event.defaultPrevented) return;
      event.preventDefault(); submitDemo(button.form, button);
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Enter' || event.isComposing || event.defaultPrevented || !(event.target instanceof HTMLInputElement) || !event.target.form) return;
      event.preventDefault(); submitDemo(event.target.form, null);
    });
    notifyHeight(); new ResizeObserver(notifyHeight).observe(document.body);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();
})();

(() => {
  if (!window.fetch) return;
  document.querySelectorAll('form[data-auth-form]').forEach(form => {
    let pending = false;
    let refreshed = false;
    form.addEventListener('submit', async event => {
      if (refreshed) {
        refreshed = false;
        return;
      }
      event.preventDefault();
      if (pending) return;
      pending = true;
      const submitter = event.submitter;
      const button = submitter || form.querySelector('button[type="submit"]');
      const previouslyDisabled = button?.disabled;
      if (button) button.disabled = true;
      let error = form.querySelector('[data-auth-error]');
      if (error) error.hidden = true;
      try {
        // A login in another tab rotates the cookie. Fetch its current masked
        // token immediately before the normal, still CSRF-protected POST.
        const response = await window.fetch(form.dataset.csrfUrl, {
          credentials: 'same-origin', cache: 'no-store', redirect: 'error',
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error('Token refresh failed');
        const data = await response.json();
        if (!/^[A-Za-z0-9]{64}$/.test(data.csrfToken || '')) throw new Error('Invalid token');
        const input = form.querySelector('input[name="csrfmiddlewaretoken"]');
        if (!input) throw new Error('Missing token input');
        input.value = data.csrfToken;
        refreshed = true;
        if (button) button.disabled = previouslyDisabled;
        if (form.requestSubmit) form.requestSubmit(submitter || undefined);
        else HTMLFormElement.prototype.submit.call(form);
      } catch (_error) {
        refreshed = false;
        if (!error) {
          error = document.createElement('p');
          error.dataset.authError = '';
          error.className = 'form-error';
          error.setAttribute('role', 'alert');
          form.prepend(error);
        }
        error.textContent = 'Не удалось отправить форму. Проверьте соединение и попробуйте ещё раз.';
        error.hidden = false;
        if (button) button.disabled = previouslyDisabled;
      } finally {
        pending = false;
      }
    });
  });
})();

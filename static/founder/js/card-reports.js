// Complaints about project cards: the "!" button asks for confirmation in a dialog,
// then reports the card and shows "Опасно!" above its name without reloading.
// Without JavaScript the button still works as a plain form (no confirmation step).
(() => {
  const forms = document.querySelectorAll('[data-report-form]');
  if (!forms.length || typeof HTMLDialogElement === 'undefined') return;

  const dialog = document.createElement('dialog');
  dialog.className = 'report-dialog';
  dialog.setAttribute('aria-labelledby', 'report-dialog-title');
  dialog.innerHTML = `
    <form method="dialog" class="report-dialog-body">
      <span class="report-dialog-icon" aria-hidden="true">!</span>
      <h2 id="report-dialog-title">Пожаловаться на карточку?</h2>
      <p class="report-dialog-text"></p>
      <p class="report-dialog-error" role="alert" hidden></p>
      <div class="report-dialog-actions">
        <button type="submit" value="cancel" class="button ghost" autofocus>Отмена</button>
        <button type="button" class="button report-confirm" data-report-confirm>Пожаловаться</button>
      </div>
    </form>`;
  document.body.append(dialog);
  const text = dialog.querySelector('.report-dialog-text');
  const error = dialog.querySelector('.report-dialog-error');
  const confirm = dialog.querySelector('[data-report-confirm]');
  let current = null;

  // Click on the dimmed backdrop closes the dialog, like a press on "Отмена".
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { current = null; });

  function markReported(form) {
    const card = form.closest('.discovery-card, .public-project');
    const done = document.createElement('span');
    done.className = 'report-button is-reported';
    done.setAttribute('role', 'img');
    done.setAttribute('aria-label', 'Вы пожаловались на эту карточку');
    done.title = 'Вы пожаловались на эту карточку';
    done.innerHTML = form.querySelector('svg').outerHTML;
    form.replaceWith(done);
    if (card && !card.querySelector('.danger-label')) {
      const label = document.createElement('p');
      label.className = 'danger-label is-new';
      label.textContent = 'Опасно!';
      card.querySelector('h1, h2')?.before(label);
    }
  }

  confirm.addEventListener('click', async () => {
    if (!current) return;
    const form = current;
    confirm.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.reported) {
        throw new Error(data.error || (response.status === 403
          ? 'Сессия устарела. Обновите страницу.' : 'Не удалось отправить жалобу. Попробуйте ещё раз.'));
      }
      dialog.close();
      markReported(form);
    } catch (problem) {
      error.textContent = problem.message;
      error.hidden = false;
    } finally {
      confirm.disabled = false;
    }
  });

  forms.forEach(form => form.addEventListener('submit', event => {
    event.preventDefault();
    current = form;
    text.textContent = `Вы уверены, что хотите пожаловаться на карточку «${form.dataset.cardName}»? `
      + 'Над её названием появится предупреждение «Опасно!». Отменить жалобу будет нельзя.';
    error.hidden = true;
    dialog.showModal();
  }));
})();

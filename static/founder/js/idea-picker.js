// Мастерская идей: переключение идей без перезагрузки и совет Бруно по выбранной идее.
(() => {
  const picker = document.querySelector('[data-idea-picker]');
  if (!picker) return;
  picker.classList.add('is-enhanced');
  const tabs = [...picker.querySelectorAll('.idea-tab')];
  const panels = [...picker.querySelectorAll('.idea-panel')];
  const actions = [...picker.querySelectorAll('.idea-panel-actions')];
  const adviceBox = picker.querySelector('.idea-advice');
  const adviceText = picker.querySelector('[data-advice-text]');
  const adviceStatus = picker.querySelector('[data-advice-status]');
  const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
  const advice = {};
  panels.forEach(panel => { advice[panel.dataset.index] = panel.dataset.advice || ''; });
  let current = String(Number(picker.dataset.pick || 1) - 1);
  let pending = false;

  const status = text => {
    adviceStatus.hidden = !text;
    adviceStatus.textContent = text || '';
  };

  const showAdvice = index => {
    adviceBox.classList.remove('is-updating');
    void adviceBox.offsetWidth; // перезапуск анимации при каждой смене текста
    adviceBox.classList.add('is-updating');
    if (advice[index]) {
      adviceText.textContent = advice[index];
      status('');
      return;
    }
    adviceText.textContent = '';
    status('Бруно анализирует эту идею…');
    request(index);
  };

  const setPick = pick => {
    picker.dataset.pick = String(pick);
    tabs.forEach(tab => {
      tab.querySelector('[data-pick-badge]').hidden = String(Number(tab.dataset.index) + 1) !== String(pick);
    });
  };

  const select = (index, focus) => {
    current = String(index);
    tabs.forEach(tab => {
      const on = tab.dataset.index === current;
      tab.classList.toggle('is-active', on);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      tab.tabIndex = on ? 0 : -1;
      if (on && focus) tab.focus();
    });
    panels.forEach(panel => panel.classList.toggle('is-active', panel.dataset.index === current));
    actions.forEach(block => block.classList.toggle('is-active', block.dataset.index === current));
    showAdvice(current);
  };

  async function request(index) {
    if (pending) return;
    const tab = tabs.find(item => item.dataset.index === index);
    if (!tab || !tab.dataset.adviceUrl || !csrf) {
      status('');
      return;
    }
    pending = true;
    let retry = false;
    try {
      const response = await fetch(tab.dataset.adviceUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': csrf.value, Accept: 'application/json'},
      });
      const body = await response.json().catch(() => ({}));
      if (response.ok && body.advice) {
        advice[index] = body.advice;
        setPick(body.pick);
      } else if (response.status === 429) {
        // Бруно ещё занят другим запросом этого аккаунта: подождём и спросим снова.
        retry = true;
        if (current === index) status('Бруно заканчивает другой запрос и сразу разберёт эту идею…');
      } else if (current === index) {
        status(body.error || 'Не получилось разобрать идею. Выберите её ещё раз.');
      }
    } catch (error) {
      if (current === index) status('Нет связи с сервером. Выберите идею ещё раз.');
    } finally {
      pending = false;
    }
    if (retry) {
      window.setTimeout(() => { if (current === index && !advice[index]) request(index); }, 4000);
    } else if (advice[current]) {
      if (current === index) showAdvice(current);
    } else if (current !== index) {
      request(current);
    }
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', () => select(tab.dataset.index));
    tab.addEventListener('keydown', event => {
      const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
      if (!step) return;
      event.preventDefault();
      const position = (tabs.findIndex(item => item.dataset.index === current) + step + tabs.length) % tabs.length;
      select(tabs[position].dataset.index, true);
    });
  });
  select(current);
})();

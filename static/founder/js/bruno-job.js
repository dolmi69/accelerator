// «Бруно работает»: пока идёт долгий AI-запрос, страница показывает это и обновляется, когда он закончит.
// Основатель мог уйти со страницы во время запроса: запрос доводится до конца на сервере, а здесь видно,
// что работа идёт, и результат появится сам.
(() => {
  const banner = document.querySelector('[data-bruno-job]');
  const wait = document.querySelector('[data-bruno-wait]');
  const node = banner || wait;
  if (!node) return;
  const url = node.dataset.url;
  const delay = 3000;

  const done = () => {
    if (wait) {
      window.location.href = wait.dataset.back || '/';
    } else {
      window.location.reload();
    }
  };

  const check = async () => {
    try {
      const response = await fetch(url, {credentials: 'same-origin', headers: {Accept: 'application/json'}});
      if (response.ok) {
        const job = await response.json();
        if (!job.busy) {
          done();
          return;
        }
        const text = node.querySelector('[data-bruno-job-label]');
        if (text && job.label) text.textContent = job.label;
      }
    } catch (error) {
      // Нет связи — попробуем позже.
    }
    window.setTimeout(check, delay);
  };
  window.setTimeout(check, delay);
})();

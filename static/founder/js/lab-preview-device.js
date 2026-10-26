(() => {
  const controls = document.querySelector('[data-lab-device-switch]');
  const stage = document.querySelector('[data-lab-preview-stage]');
  if (!controls || !stage) return;

  const buttons = [...controls.querySelectorAll('[data-lab-device]')];
  const storageKey = 'brunolab:lab-preview-device';
  const apply = device => {
    const selected = device === 'phone' ? 'phone' : 'desktop';
    // Resize the existing iframe; navigation, forms and its script state survive.
    stage.dataset.previewDevice = selected;
    buttons.forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.labDevice === selected));
    });
  };

  let saved;
  try { saved = localStorage.getItem(storageKey); } catch {}
  apply(saved);
  controls.hidden = false;
  buttons.forEach(button => {
    button.addEventListener('click', () => {
      apply(button.dataset.labDevice);
      try { localStorage.setItem(storageKey, stage.dataset.previewDevice); } catch {}
    });
  });
  document.addEventListener('lab:job-state', event => {
    const busy = Boolean(event.detail?.active && event.detail.mode === 'edit');
    buttons.forEach(button => { button.disabled = busy; });
  });
})();

(() => {
  const copy = document.querySelector('[data-copy-profile]');
  copy?.addEventListener('click', async () => {
    const url = new URL(copy.dataset.profileUrl, location.origin).href;
    const status = document.querySelector('[data-copy-status]');
    try { await navigator.clipboard.writeText(url); status.textContent = 'Ссылка на профиль скопирована'; }
    catch { status.textContent = `Ссылка на профиль: ${url}`; }
  });
  const form = document.querySelector('[data-profile-form]');
  if (!form) return;
  const handle = form.elements.handle;
  const handleStatus = form.querySelector('[data-handle-status]');
  const upload = form.elements.avatar_upload;
  const preview = form.querySelector('[data-avatar-preview]');
  const placeholder = form.querySelector('[data-avatar-placeholder]');
  const avatarError = form.querySelector('[data-avatar-error]');
  const remove = form.elements.remove_avatar;
  const originalImage = preview.getAttribute('src');
  let timer, controller, objectURL, dirty = false, busy = false;
  async function checkHandle() {
    controller?.abort(); controller = new AbortController();
    const value = handle.value;
    handleStatus.textContent = 'Проверяем тег…'; delete handleStatus.dataset.available;
    try {
      const url = new URL(form.dataset.handleUrl, location.origin); url.searchParams.set('handle', value);
      const result = await fetch(url, {signal: controller.signal, credentials: 'same-origin'});
      if (!result.ok) throw new Error('Unavailable');
      const data = await result.json();
      if (handle.value !== value) return;
      handleStatus.textContent = data.message; handleStatus.dataset.available = String(data.available);
    } catch (error) { if (error.name !== 'AbortError') handleStatus.textContent = 'Проверим тег при сохранении.'; }
  }
  handle.addEventListener('input', () => { clearTimeout(timer); controller?.abort(); handleStatus.textContent = ''; timer = setTimeout(checkHandle, 350); });
  const countBio = () => { form.querySelector('[data-bio-counter]').textContent = `${form.elements.bio.value.length} / 600`; };
  form.elements.bio.addEventListener('input', countBio); countBio();
  const restoreAvatar = () => {
    if (originalImage && !remove?.checked) { preview.src = originalImage; preview.hidden = false; placeholder.hidden = true; }
    else { preview.hidden = true; placeholder.hidden = false; }
  };
  upload.addEventListener('change', () => {
    avatarError.hidden = true; upload.setCustomValidity('');
    if (objectURL) URL.revokeObjectURL(objectURL);
    const file = upload.files[0];
    if (!file) { restoreAvatar(); return; }
    if (file.size > 5 * 1024 * 1024) {
      const text = 'Максимальный размер аватарки — 5 МБ.';
      avatarError.textContent = text; avatarError.hidden = false; upload.setCustomValidity(text); restoreAvatar(); return;
    }
    if (remove) remove.checked = false;
    objectURL = URL.createObjectURL(file); preview.src = objectURL; preview.hidden = false; placeholder.hidden = true;
  });
  preview.addEventListener('error', () => {
    if (!upload.files.length) { preview.hidden = true; placeholder.hidden = false; return; }
    avatarError.textContent = 'Предпросмотр недоступен. Поддерживаются JPG, PNG и WebP.';
    avatarError.hidden = false; preview.hidden = true; placeholder.hidden = false;
  });
  remove?.addEventListener('change', () => {
    if (remove.checked) { upload.value = ''; upload.setCustomValidity(''); avatarError.hidden = true; }
    restoreAvatar();
  });
  form.elements.display_name.addEventListener('input', () => {
    placeholder.textContent = (form.elements.display_name.value.trim() || 'Я').slice(0, 1).toUpperCase();
  });
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  form.addEventListener('submit', event => {
    if (busy) { event.preventDefault(); return; }
    busy = true; const button = form.querySelector('button[type=submit]'); button.disabled = true;
    button.textContent = 'Сохраняем…';
    form.querySelector('[data-profile-status]').textContent = 'Сохраняем профиль и загружаем фото…';
  });
  window.addEventListener('beforeunload', event => { if (dirty && !busy) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
})();

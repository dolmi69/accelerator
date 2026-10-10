// Files and photos in personal messages.
// Composer: a file is uploaded with a regular POST as soon as it is picked and
// shown as a card above the text field; the message itself (text + file ids)
// goes over the WebSocket in messages.js. Messages: photos as previews with a
// full-screen viewer, other files as download cards.
(() => {
  const ICON_CLIP = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21.4 11.6-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8l8.5-8.5a3.7 3.7 0 0 1 5.2 5.2l-8.5 8.5a1.8 1.8 0 0 1-2.6-2.6l7.8-7.8"/></svg>';
  const ICON_CLOSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  const ICON_DOWNLOAD = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11m0 0 4.5-4.5M12 15l-4.5-4.5M5 20h14"/></svg>';
  const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp'];

  const extension = name => {
    const dot = String(name).lastIndexOf('.');
    return dot > 0 ? String(name).slice(dot).toLowerCase() : '';
  };
  const formatSize = bytes => {
    if (!Number.isFinite(bytes)) return '';
    if (bytes < 1024) return `${bytes} Б`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
    return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
  };
  const badge = name => (extension(name).slice(1) || 'файл').slice(0, 4).toUpperCase();
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  // ---------- Full-screen photo viewer ----------
  let viewer = null;
  let lastFocus = null;
  function openViewer(url, name, downloadUrl) {
    if (!viewer) {
      viewer = el('div', 'photo-viewer');
      viewer.setAttribute('role', 'dialog');
      viewer.setAttribute('aria-modal', 'true');
      viewer.hidden = true;
      viewer.innerHTML = `<div class="photo-viewer-bar"><span class="photo-viewer-name"></span>
        <a class="photo-viewer-action" data-viewer-download aria-label="Скачать" title="Скачать">${ICON_DOWNLOAD}</a>
        <button type="button" class="photo-viewer-action" data-viewer-close aria-label="Закрыть" title="Закрыть (Esc)">${ICON_CLOSE}</button></div>
        <img class="photo-viewer-image" alt="">`;
      document.body.append(viewer);
      const close = () => {
        viewer.hidden = true;
        viewer.querySelector('img').removeAttribute('src');
        document.documentElement.classList.remove('has-photo-viewer');
        lastFocus?.focus?.();
      };
      viewer.addEventListener('click', event => {
        if (event.target === viewer || event.target.closest('[data-viewer-close]')) close();
      });
      document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !viewer.hidden) { event.preventDefault(); event.stopPropagation(); close(); }
      }, true);
    }
    lastFocus = document.activeElement;
    viewer.querySelector('.photo-viewer-name').textContent = name;
    const image = viewer.querySelector('img');
    image.alt = name;
    image.src = url;
    const download = viewer.querySelector('[data-viewer-download]');
    download.href = downloadUrl;
    download.setAttribute('download', name);
    viewer.hidden = false;
    document.documentElement.classList.add('has-photo-viewer');
    viewer.querySelector('[data-viewer-close]').focus();
  }

  // ---------- Attachments inside a message ----------
  function render(attachments) {
    const fragment = document.createDocumentFragment();
    const photos = attachments.filter(item => item.kind === 'image');
    const files = attachments.filter(item => item.kind !== 'image');
    if (photos.length) {
      const grid = el('div', `direct-media${photos.length > 1 ? ' is-grid' : ''}`);
      photos.forEach(item => {
        const link = el('a', 'direct-photo');
        link.href = item.url;
        link.setAttribute('aria-label', `Открыть фото ${item.name}`);
        const image = el('img');
        image.src = item.local_url || item.preview_url || item.url;
        image.alt = item.name;
        image.loading = 'lazy';
        image.decoding = 'async';
        if (item.width && item.height) {
          image.width = item.width;
          image.height = item.height;
          if (photos.length === 1) link.style.aspectRatio = `${item.width} / ${item.height}`;
        }
        link.append(image);
        if (!item.local_url) {
          link.addEventListener('click', event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
            event.preventDefault();
            openViewer(item.url, item.name, `${item.url}?download=1`);
          });
        } else {
          link.addEventListener('click', event => event.preventDefault());
        }
        grid.append(link);
      });
      fragment.append(grid);
    }
    if (files.length) {
      const list = el('div', 'direct-files');
      files.forEach(item => {
        const card = el('a', 'direct-file');
        card.href = `${item.url}?download=1`;
        card.setAttribute('download', item.name);
        card.title = `Скачать ${item.name}`;
        const text = el('span', 'direct-file-text');
        text.append(el('strong', '', item.name), el('small', '', formatSize(item.size)));
        const arrow = el('span', 'direct-file-download');
        arrow.innerHTML = ICON_DOWNLOAD;
        card.append(el('span', 'file-badge', badge(item.name)), text, arrow);
        list.append(card);
      });
      fragment.append(list);
    }
    return fragment;
  }

  // ---------- Composer: upload, preview, remove ----------
  function composer(form, { onChange = () => {} } = {}) {
    const tray = form.querySelector('[data-attachment-tray]');
    const button = form.querySelector('[data-attach-button]');
    const picker = form.querySelector('[data-attach-input]');
    const textarea = form.elements.content;
    const uploadUrl = form.dataset.uploadUrl;
    const deleteTemplate = form.dataset.deleteUrl;
    const maxBytes = Number(form.dataset.maxBytes) || 5 * 1024 * 1024;
    const maxFiles = Number(form.dataset.maxFiles) || 10;
    const allowed = new Set((form.dataset.allowed || '').split(',').filter(Boolean));
    const token = form.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
    const dropZone = form.closest('.direct-chat') || form;
    if (!tray || !button || !picker || !uploadUrl) return null;

    let items = [];
    let disabled = false;
    const deleteUrl = id => deleteTemplate.replace('00000000-0000-0000-0000-000000000000', id);
    const changed = () => {
      tray.hidden = !items.length;
      onChange();
    };

    function notify(message) {
      const note = el('div', 'attachment-note', message);
      note.setAttribute('role', 'alert');
      tray.hidden = false;
      tray.append(note);
      setTimeout(() => { note.remove(); tray.hidden = !items.length; }, 5000);
    }

    function removeItem(item, { keepOnServer = false } = {}) {
      items = items.filter(entry => entry !== item);
      item.xhr?.abort();
      item.node.remove();
      if (item.attachment && !keepOnServer) {
        fetch(deleteUrl(item.attachment.id), {
          method: 'POST', credentials: 'same-origin', keepalive: true,
          headers: { 'X-CSRFToken': token, Accept: 'application/json' },
        }).catch(() => {}); // Never-sent files are also cleaned up by the server after a day.
      }
      if (item.localUrl && !keepOnServer) URL.revokeObjectURL(item.localUrl);
      changed();
    }

    function upload(item) {
      const xhr = new XMLHttpRequest();
      item.xhr = xhr;
      xhr.open('POST', uploadUrl);
      xhr.responseType = 'json';
      xhr.setRequestHeader('X-CSRFToken', token);
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.upload.addEventListener('progress', event => {
        if (event.lengthComputable) item.node.style.setProperty('--progress', `${Math.round(event.loaded / event.total * 100)}%`);
      });
      xhr.addEventListener('load', () => {
        item.xhr = null;
        const data = xhr.response || {};
        if (xhr.status === 201 && data.id) {
          item.attachment = data;
          item.state = 'ready';
          item.node.classList.remove('is-uploading');
          item.node.querySelector('small').textContent = formatSize(data.size);
          if (data.name !== item.file.name) item.node.querySelector('strong')?.replaceChildren(data.name);
        } else {
          fail(item, data.error || (xhr.status === 413 ? `Файл больше ${formatSize(maxBytes)}.`
            : xhr.status === 403 ? 'Сессия устарела. Обновите страницу.' : 'Не удалось загрузить файл.'));
        }
        changed();
      });
      xhr.addEventListener('error', () => { item.xhr = null; fail(item, 'Нет связи. Уберите файл и попробуйте снова.'); changed(); });
      const body = new FormData();
      body.append('file', item.file, item.file.name);
      xhr.send(body);
    }

    function fail(item, message) {
      item.state = 'error';
      item.node.classList.remove('is-uploading');
      item.node.classList.add('is-error');
      item.node.title = message;
      item.node.querySelector('small').textContent = message;
    }

    function add(fileList) {
      if (disabled) return;
      for (const file of fileList) {
        if (items.length >= maxFiles) { notify(`Можно прикрепить до ${maxFiles} файлов.`); break; }
        const ext = extension(file.name || '');
        if (!allowed.has(ext)) { notify(`«${file.name}»: такой тип файла нельзя отправить.`); continue; }
        if (file.size > maxBytes) { notify(`«${file.name}» больше ${formatSize(maxBytes)}.`); continue; }
        if (!file.size) { notify(`«${file.name}» пустой.`); continue; }
        const image = IMAGE_EXTENSIONS.includes(ext);
        const item = { file, state: 'uploading', localUrl: image ? URL.createObjectURL(file) : null };
        const node = el('div', `attachment-chip ${image ? 'is-image' : 'is-file'} is-uploading`);
        if (image) {
          const thumb = el('img');
          thumb.src = item.localUrl;
          thumb.alt = file.name;
          node.append(thumb);
          const meta = el('span', 'sr-only');
          meta.append(el('strong', '', file.name), el('small', '', formatSize(file.size)));
          node.append(meta);
        } else {
          const text = el('span', 'attachment-chip-text');
          text.append(el('strong', '', file.name), el('small', '', formatSize(file.size)));
          node.append(el('span', 'file-badge', badge(file.name)), text);
        }
        const remove = el('button', 'attachment-remove');
        remove.type = 'button';
        remove.innerHTML = ICON_CLOSE;
        remove.setAttribute('aria-label', `Убрать ${file.name}`);
        remove.title = 'Убрать';
        remove.addEventListener('click', () => { removeItem(item); textarea?.focus(); });
        node.append(remove, el('span', 'attachment-progress'));
        item.node = node;
        items.push(item);
        tray.append(node);
        upload(item);
      }
      changed();
    }

    button.addEventListener('click', () => picker.click());
    picker.addEventListener('change', () => { add(picker.files); picker.value = ''; });
    textarea?.addEventListener('paste', event => {
      const files = [...(event.clipboardData?.files || [])];
      if (!files.length) return;
      event.preventDefault();
      add(files.map((file, index) => (file.name && file.name !== 'image.png') ? file
        : new File([file], `Снимок ${new Date().toLocaleString('ru-RU').replace(/[:/]/g, '-')}${index ? ` (${index + 1})` : ''}${extension(file.name) || '.png'}`, { type: file.type })));
    });
    let depth = 0;
    const hasFiles = event => [...(event.dataTransfer?.types || [])].includes('Files');
    dropZone.addEventListener('dragenter', event => {
      if (!hasFiles(event) || disabled) return;
      event.preventDefault(); depth += 1; dropZone.classList.add('is-dropping');
    });
    dropZone.addEventListener('dragover', event => { if (hasFiles(event) && !disabled) event.preventDefault(); });
    dropZone.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) dropZone.classList.remove('is-dropping'); });
    dropZone.addEventListener('drop', event => {
      if (!hasFiles(event) || disabled) return;
      event.preventDefault(); depth = 0; dropZone.classList.remove('is-dropping');
      add(event.dataTransfer.files);
    });

    return {
      busy: () => items.some(item => item.state === 'uploading'),
      count: () => items.length,
      failed: () => items.some(item => item.state === 'error'),
      ready: () => items.filter(item => item.state === 'ready')
        .map(item => ({ ...item.attachment, local_url: item.localUrl || undefined })),
      // After sending: the files now belong to the message, keep them on the server.
      takeAll() { [...items].forEach(item => { if (item.state === 'ready') removeItem(item, { keepOnServer: true }); }); },
      setDisabled(value) { disabled = value; button.disabled = value; },
    };
  }

  window.DirectAttachments = { composer, render, formatSize };
})();

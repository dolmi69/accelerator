// Telegram-style search inside a chat (Bruno and direct messages).
// The magnifier in the chat header opens a bar; the server searches the whole
// history, the list shows excerpts, arrows walk from newest to oldest match.
(() => {
  // Must match fold() in founder/services/chat_search.py: case-insensitive,
  // ё = е, and every character keeps its position so highlights line up.
  const fold = text => {
    let out = '';
    for (const char of text) {
      const lower = char.toLowerCase();
      const kept = lower.length === char.length ? lower : char;
      out += kept === 'ё' ? 'е' : kept;
    }
    return out;
  };
  const normalize = text => text.split(/\s+/).filter(Boolean).join(' ').slice(0, 100);

  const ICONS = {
    search: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/></svg>',
    up: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>',
    down: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
    close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  };

  function highlight(element, needle) {
    if (!needle) return [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const marks = [];
    nodes.forEach(node => {
      const text = node.nodeValue;
      const folded = fold(text);
      let from = 0;
      let index = folded.indexOf(needle);
      if (index === -1) return;
      const fragment = document.createDocumentFragment();
      while (index !== -1) {
        if (index > from) fragment.append(text.slice(from, index));
        const mark = document.createElement('mark');
        mark.className = 'chat-search-hit';
        mark.textContent = text.slice(index, index + needle.length);
        fragment.append(mark);
        marks.push(mark);
        from = index + needle.length;
        index = folded.indexOf(needle, from);
      }
      if (from < text.length) fragment.append(text.slice(from));
      node.replaceWith(fragment);
    });
    return marks;
  }

  function clearHighlights(scope) {
    scope.querySelectorAll('mark.chat-search-hit').forEach(mark => {
      const parent = mark.parentNode;
      mark.replaceWith(mark.textContent);
      parent.normalize();
    });
  }

  const formatDate = value => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    return date.toLocaleString('ru-RU', sameDay
      ? { hour: '2-digit', minute: '2-digit' }
      : { day: 'numeric', month: 'short', ...(date.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) });
  };

  function mount({ toggle, slot, endpoint, scope, textSelector, locate, reveal, initial }) {
    const box = document.createElement('div');
    box.className = 'chat-search';
    box.setAttribute('role', 'search');
    box.hidden = true;
    const listId = `chat-search-results-${Math.random().toString(36).slice(2)}`;
    box.innerHTML = `
      <div class="chat-search-bar">
        ${ICONS.search}
        <input class="chat-search-input" type="search" maxlength="100" autocomplete="off" spellcheck="false"
          enterkeyhint="search" placeholder="Поиск по сообщениям" aria-label="Поиск по сообщениям"
          aria-controls="${listId}" aria-expanded="false">
        <span class="chat-search-count" aria-live="polite"></span>
        <button type="button" data-search-older aria-label="Более раннее совпадение" title="Раньше (Enter)" disabled>${ICONS.up}</button>
        <button type="button" data-search-newer aria-label="Более позднее совпадение" title="Позже (Shift+Enter)" disabled>${ICONS.down}</button>
        <button type="button" data-search-close aria-label="Закрыть поиск" title="Закрыть (Esc)">${ICONS.close}</button>
      </div>
      <ol class="chat-search-results" id="${listId}" aria-label="Найденные сообщения" hidden></ol>`;
    slot.append(box);
    const input = box.querySelector('input');
    const count = box.querySelector('.chat-search-count');
    const olderButton = box.querySelector('[data-search-older]');
    const newerButton = box.querySelector('[data-search-newer]');
    const list = box.querySelector('ol');

    let results = [];
    let truncated = false;
    let current = -1;
    let query = '';
    let timer = null;
    let request = null;
    let jumpToken = 0;

    const needle = () => fold(query);
    const total = () => `${results.length}${truncated ? '+' : ''}`;

    function setList(open) {
      list.hidden = !open || !query;
      input.setAttribute('aria-expanded', String(!list.hidden));
    }

    function updateControls() {
      olderButton.disabled = !results.length || current >= results.length - 1;
      newerButton.disabled = current <= 0;
      if (!query) count.textContent = '';
      else if (!results.length) count.textContent = 'Не найдено';
      else count.textContent = current >= 0 ? `${current + 1} из ${total()}` : `Найдено: ${total()}`;
    }

    function paint() {
      clearHighlights(scope);
      const text = needle();
      if (!text) return;
      scope.querySelectorAll(textSelector).forEach(node => highlight(node, text));
      const active = current >= 0 ? locate(results[current].id) : null;
      active?.querySelectorAll('mark.chat-search-hit').forEach(mark => mark.classList.add('is-current'));
    }

    function renderList() {
      list.replaceChildren();
      if (!results.length) {
        const empty = document.createElement('li');
        empty.className = 'chat-search-empty';
        empty.textContent = `Сообщений с «${query}» нет.`;
        list.append(empty);
        return;
      }
      results.forEach((result, index) => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'chat-search-result';
        button.dataset.index = index;
        const meta = document.createElement('span');
        meta.className = 'chat-search-result-meta';
        const author = document.createElement('strong');
        author.textContent = result.author;
        const date = document.createElement('time');
        date.dateTime = result.created_at;
        date.textContent = formatDate(result.created_at);
        meta.append(author, date);
        const text = document.createElement('span');
        text.className = 'chat-search-result-text';
        text.textContent = result.snippet;
        highlight(text, needle());
        button.append(meta, text);
        button.addEventListener('click', () => jump(index));
        item.append(button);
        list.append(item);
      });
      if (truncated) {
        const more = document.createElement('li');
        more.className = 'chat-search-empty';
        more.textContent = 'Показаны последние 200 совпадений. Уточните запрос, чтобы найти более ранние.';
        list.append(more);
      }
    }

    async function run(value, { keepId = null, showList = true } = {}) {
      query = normalize(value);
      request?.abort();
      results = [];
      truncated = false;
      current = -1;
      jumpToken += 1;
      if (!query) {
        list.replaceChildren();
        setList(false);
        paint();
        updateControls();
        return;
      }
      count.textContent = 'Ищем…';
      const controller = new AbortController();
      request = controller;
      try {
        const url = new URL(endpoint, window.location.origin);
        url.searchParams.set('q', query);
        const response = await fetch(url, {
          headers: { Accept: 'application/json' }, credentials: 'same-origin', signal: controller.signal,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !Array.isArray(data.results)) {
          throw new Error(data.error || (response.redirected ? 'Войдите в аккаунт снова.' : 'Поиск недоступен.'));
        }
        if (request !== controller) return;
        results = data.results;
        truncated = Boolean(data.truncated);
        renderList();
        paint();
        updateControls();
        const keep = keepId === null ? -1 : results.findIndex(result => String(result.id) === String(keepId));
        if (keep >= 0) await jump(keep);
        else setList(showList && document.activeElement === input);
      } catch (error) {
        if (error.name === 'AbortError') return;
        count.textContent = error.message || 'Поиск недоступен.';
        list.replaceChildren();
        setList(false);
      } finally {
        if (request === controller) request = null;
      }
    }

    async function jump(index) {
      if (index < 0 || index >= results.length) return;
      current = index;
      const token = ++jumpToken;
      setList(false);
      list.querySelectorAll('.chat-search-result').forEach(node => {
        node.classList.toggle('is-active', Number(node.dataset.index) === index);
      });
      updateControls();
      const result = results[index];
      let target = locate(result.id);
      if (!target) {
        count.textContent = 'Загружаем…';
        target = await reveal(result.id, query);
        if (token !== jumpToken) return;
        updateControls();
      }
      if (!target) {
        count.textContent = 'Сообщение недоступно';
        return;
      }
      paint();
      // Far jumps are instant (like Telegram): a long smooth scroll is slow and can stall.
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      const distance = Math.abs(target.getBoundingClientRect().top - window.innerHeight / 2);
      target.scrollIntoView({ block: 'center', behavior: reduce || distance > 1200 ? 'auto' : 'smooth' });
      scope.querySelectorAll('.is-search-current').forEach(node => node.classList.remove('is-search-current'));
      const bubble = target.querySelector('.message-body') || target;
      void bubble.offsetWidth; // Restart the flash when the same message is chosen again.
      bubble.classList.add('is-search-current');
    }

    function open() {
      box.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      input.focus();
      input.select();
      if (query && results.length && current < 0) setList(true);
    }

    function close() {
      request?.abort();
      clearTimeout(timer);
      jumpToken += 1;
      box.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      setList(false);
      clearHighlights(scope);
      scope.querySelectorAll('.is-search-current').forEach(node => node.classList.remove('is-search-current'));
      toggle.focus();
    }

    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', () => (box.hidden ? open() : close()));
    box.querySelector('[data-search-close]').addEventListener('click', close);
    olderButton.addEventListener('click', () => jump(current + 1));
    newerButton.addEventListener('click', () => jump(current - 1));
    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => run(input.value), 250);
    });
    input.addEventListener('focus', () => { if (query && current < 0) setList(true); });
    input.addEventListener('click', () => { if (query) setList(true); });
    // Esc works wherever focus is (e.g. after a jump opened another history page).
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || box.hidden || event.defaultPrevented) return;
      const focus = document.activeElement;
      if (focus && focus !== document.body && focus !== toggle && !box.contains(focus)) return;
      event.preventDefault();
      if (!list.hidden) setList(false);
      else close();
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.isComposing) {
        event.preventDefault();
        if (normalize(input.value) !== query) {
          clearTimeout(timer);
          run(input.value).then(() => { if (results.length) jump(0); });
        } else if (event.shiftKey) jump(current - 1);
        else jump(current < 0 ? 0 : current + 1);
      }
    });
    document.addEventListener('pointerdown', event => {
      if (!box.contains(event.target)) setList(false);
    });
    // Newly loaded or received messages get highlighted too.
    scope.addEventListener('chat:updated', () => { if (!box.hidden && query) paint(); });

    if (initial?.query) {
      box.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      input.value = initial.query;
      run(initial.query, { keepId: initial.id, showList: false });
    }
    return { open, close };
  }

  // Chat with Bruno: older pages are separate URLs, so a far result opens its page.
  const brunoToggle = document.querySelector('[data-chat-search="bruno"]');
  const brunoList = document.getElementById('message-list');
  if (brunoToggle && brunoList) {
    const params = new URLSearchParams(window.location.search);
    const initial = params.get('search') ? { query: params.get('search'), id: params.get('message') } : null;
    if (initial) {
      params.delete('search');
      const rest = params.toString();
      window.history.replaceState(window.history.state, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`);
    }
    mount({
      toggle: brunoToggle,
      slot: document.querySelector('[data-chat-search-slot]'),
      endpoint: brunoToggle.dataset.searchUrl,
      scope: brunoList,
      textSelector: '.message-text',
      locate: id => document.getElementById(`message-${id}`),
      reveal: (id, query) => {
        const url = new URL(window.location.pathname, window.location.origin);
        url.searchParams.set('message', id);
        url.searchParams.set('search', query);
        url.hash = `message-${id}`;
        window.location.assign(url);
        return new Promise(() => {}); // The page is being replaced.
      },
      initial,
    });
  }

  // Direct messages: history arrives over the socket; messages.js loads the gap.
  const directToggle = document.querySelector('[data-chat-search="direct"]');
  const directList = document.querySelector('[data-direct-messages]');
  if (directToggle && directList) {
    mount({
      toggle: directToggle,
      slot: document.querySelector('[data-chat-search-slot]'),
      endpoint: directToggle.dataset.searchUrl,
      scope: directList,
      textSelector: '.direct-bubble:not([data-pending]) > p',
      locate: id => directList.querySelector(`[data-id="${id}"]`),
      reveal: id => window.directChat?.loadUntil(Number(id)) ?? Promise.resolve(null),
    });
  }

  window.ChatSearch = { mount, fold };
})();

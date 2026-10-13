// Unsent message drafts survive reloads, navigation and closed tabs.
// Stored only in this browser, per user and per conversation; removed on send,
// on logout and after DRAFT_TTL of inactivity.
(() => {
  const PREFIX = 'cofounder:draft:';
  const DRAFT_TTL = 14 * 24 * 60 * 60 * 1000;
  const MAX_LENGTH = 4000;

  const storage = (() => {
    try {
      const store = window.localStorage;
      const probe = `${PREFIX}probe`;
      store.setItem(probe, '1');
      store.removeItem(probe);
      return store;
    } catch (error) {
      return null; // Private mode or blocked storage: drafts are simply off.
    }
  })();

  const read = key => {
    try {
      const raw = storage.getItem(key);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (typeof data?.text !== 'string' || !(Date.now() - data.savedAt < DRAFT_TTL)) {
        storage.removeItem(key);
        return null;
      }
      return data.text;
    } catch (error) {
      try { storage.removeItem(key); } catch (ignored) { /* nothing to clean */ }
      return null;
    }
  };

  const keys = () => {
    const found = [];
    try {
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (key?.startsWith(PREFIX)) found.push(key);
      }
    } catch (error) { /* storage became unavailable */ }
    return found;
  };

  function clearAll() {
    if (!storage) return;
    keys().forEach(key => { try { storage.removeItem(key); } catch (error) { /* ignore */ } });
  }

  // Old drafts of abandoned conversations should not pile up.
  if (storage) keys().forEach(read);

  function attach(input, name) {
    const noop = { clear() {}, save() {}, active: false };
    if (!storage || !input || !name) return noop;
    const key = PREFIX + name;
    let timer = null;

    const save = () => {
      clearTimeout(timer);
      timer = null;
      const text = input.value.slice(0, MAX_LENGTH);
      try {
        if (text.trim()) storage.setItem(key, JSON.stringify({ text, savedAt: Date.now() }));
        else storage.removeItem(key);
      } catch (error) { /* quota exceeded or storage blocked */ }
    };
    const clear = () => {
      clearTimeout(timer);
      timer = null;
      try { storage.removeItem(key); } catch (error) { /* ignore */ }
    };

    // Never replace text the browser already restored or the user started typing.
    const saved = read(key);
    if (saved && !input.value.trim()) {
      input.value = saved;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(save, 400);
    });
    const flush = () => { if (timer) save(); };
    input.addEventListener('blur', flush);
    window.addEventListener('pagehide', flush);
    document.addEventListener('visibilitychange', () => { if (document.hidden) flush(); });

    // Another tab of the same conversation sent or edited the draft.
    window.addEventListener('storage', event => {
      if (event.key !== key || document.activeElement === input) return;
      const text = event.newValue ? read(key) : null;
      if (text === null && event.newValue) return;
      input.value = text || '';
    });

    return { clear, save, active: true };
  }

  window.composerDrafts = { attach, clearAll };

  // Shared computers: signing out removes every draft of this browser.
  document.addEventListener('submit', event => {
    if (event.target.matches?.('[data-clear-drafts]')) clearAll();
  }, true);
})();

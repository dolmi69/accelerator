// Run with `node --test tests/drafts.test.cjs` (no npm dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/founder/js/drafts.js'), 'utf8');

class Storage {
  constructor(entries = {}) { this.map = new Map(Object.entries(entries)); }
  get length() { return this.map.size; }
  key(index) { return [...this.map.keys()][index] ?? null; }
  getItem(key) { return this.map.has(key) ? this.map.get(key) : null; }
  setItem(key, value) { this.map.set(key, String(value)); }
  removeItem(key) { this.map.delete(key); }
}

class Target {
  constructor() { this.listeners = {}; }
  addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
  fire(name, event = {}) { (this.listeners[name] || []).forEach(handler => handler(event)); }
}

function load({ storage = new Storage(), now = 1_000_000 } = {}) {
  const window = new Target();
  Object.defineProperty(window, 'localStorage', { get: () => {
    if (storage === 'blocked') throw new Error('SecurityError');
    return storage;
  } });
  const document = new Target();
  document.hidden = false;
  const timers = [];
  const context = {
    window, document, JSON, Event: class { constructor(type) { this.type = type; } },
    Date: { now: () => now },
    setTimeout: fn => { timers.push(fn); return timers.length; },
    clearTimeout: id => { if (id) timers[id - 1] = null; },
  };
  vm.runInNewContext(source, context);
  const input = Object.assign(new Target(), { value: '', dispatchEvent() {} });
  const runTimers = () => { timers.splice(0).forEach(fn => fn && fn()); };
  return { api: window.composerDrafts, window, document, input, storage, runTimers };
}

const KEY = 'cofounder:draft:bruno:1:5';

test('typed text is saved after a pause and restored on the next page load', () => {
  const first = load();
  first.api.attach(first.input, 'bruno:1:5');
  first.input.value = 'Наш клиент — кофейни';
  first.input.fire('input');
  assert.equal(first.storage.getItem(KEY), null); // Debounced.
  first.runTimers();
  assert.equal(JSON.parse(first.storage.getItem(KEY)).text, 'Наш клиент — кофейни');

  const second = load({ storage: first.storage });
  second.api.attach(second.input, 'bruno:1:5');
  assert.equal(second.input.value, 'Наш клиент — кофейни');
});

test('leaving the page flushes a pending save immediately', () => {
  const app = load();
  app.api.attach(app.input, 'bruno:1:5');
  app.input.value = 'Не потеряй меня';
  app.input.fire('input');
  app.window.fire('pagehide');
  assert.equal(JSON.parse(app.storage.getItem(KEY)).text, 'Не потеряй меня');
});

test('clear removes the draft and erasing the text removes it too', () => {
  const app = load();
  const draft = app.api.attach(app.input, 'bruno:1:5');
  app.input.value = 'текст'; app.input.fire('input'); app.runTimers();
  draft.clear();
  assert.equal(app.storage.getItem(KEY), null);
  app.input.value = 'снова'; app.input.fire('input'); app.runTimers();
  app.input.value = '   '; app.input.fire('input'); app.runTimers();
  assert.equal(app.storage.getItem(KEY), null);
});

test('drafts are separate per conversation and never overwrite typed text', () => {
  const storage = new Storage({
    [KEY]: JSON.stringify({ text: 'для Бруно', savedAt: 1_000_000 }),
    'cofounder:draft:dm:1:9': JSON.stringify({ text: 'для Анны', savedAt: 1_000_000 }),
  });
  const app = load({ storage });
  app.api.attach(app.input, 'dm:1:9');
  assert.equal(app.input.value, 'для Анны');
  const other = load({ storage });
  other.input.value = 'уже набрано';
  other.api.attach(other.input, 'bruno:1:5');
  assert.equal(other.input.value, 'уже набрано');
});

test('expired drafts are discarded', () => {
  const day = 24 * 60 * 60 * 1000;
  const storage = new Storage({ [KEY]: JSON.stringify({ text: 'старое', savedAt: 0 }) });
  const app = load({ storage, now: 15 * day });
  app.api.attach(app.input, 'bruno:1:5');
  assert.equal(app.input.value, '');
  assert.equal(storage.getItem(KEY), null);
});

test('logout form clears every draft but leaves other site data', () => {
  const storage = new Storage({ [KEY]: JSON.stringify({ text: 'x', savedAt: 1_000_000 }), theme: 'dark' });
  const app = load({ storage });
  app.document.fire('submit', { target: { matches: selector => selector === '[data-clear-drafts]' } });
  assert.equal(storage.getItem(KEY), null);
  assert.equal(storage.getItem('theme'), 'dark');
});

test('blocked storage turns drafts off without breaking the chat', () => {
  const app = load({ storage: 'blocked' });
  const draft = app.api.attach(app.input, 'bruno:1:5');
  assert.equal(draft.active, false);
  draft.clear();
  app.input.value = 'текст';
  assert.doesNotThrow(() => app.input.fire('input'));
});

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../static/founder/js/lab-reply.js'), 'utf8');

function setup({store = new Map(), key = 'version-1', observerEnabled = true, storageBlocked = false} = {}) {
  let now = 0, nextId = 0, callback, disconnected = false;
  const timers = new Map(), docEvents = {}, windowEvents = {};
  const history = {open: false}, summary = {focus() { this.focused = true; }};
  const savedReport = {open: false, querySelector: () => summary,
    scrollIntoView() { this.scrolled = true; }};
  const reply = {dataset: {reportKey: key}, hidden: false, panelHidden: false,
    contains: node => node === reply, closest() { return this.panelHidden ? {} : null; },
    getBoundingClientRect: () => ({top: reply.top, bottom: reply.top + 200, height: 200}), top: 900};
  const link = {hash: '#saved-report', listeners: {},
    addEventListener(name, handler) { this.listeners[name] = handler; },
    focus() { this.focused = true; }};
  const document = {visibilityState: 'visible', activeElement: null, modal: false, busy: false,
    getElementById: id => ({'lab-reply': reply, 'lab-history': history, 'saved-report': savedReport})[id],
    querySelectorAll: () => [link],
    querySelector: selector => selector === '.lab-viewer[open]' ? document.modal
      : selector === '.lab-preview-card[aria-busy="true"]' ? document.busy : link,
    addEventListener: (name, handler) => { docEvents[name] = handler; }};
  const window = {innerHeight: 800, addEventListener: (name, handler) => { windowEvents[name] = handler; }};
  class Observer {
    constructor(handler) { callback = handler; }
    observe() {}
    disconnect() { disconnected = true; }
  }
  vm.runInNewContext(code, {document, window,
    IntersectionObserver: observerEnabled ? Observer : undefined,
    sessionStorage: {
      getItem(k) { if (storageBlocked) throw new Error(); return store.get(k) ?? null; },
      setItem(k, v) { if (storageBlocked) throw new Error(); store.set(k, v); },
    },
    performance: {now: () => now},
    setTimeout(fn, delay) { const id = ++nextId; timers.set(id, {fn, due: now + delay}); return id; },
    clearTimeout: id => timers.delete(id),
  });
  return {reply, link, history, savedReport, summary, document, store,
    disconnected: () => disconnected,
    seen(ratio = 1) { callback?.([{isIntersecting: ratio > 0, intersectionRatio: ratio}]); },
    event(name) { (docEvents[name] || windowEvents[name])?.(); },
    advance(ms) {
      const end = now + ms;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].due - b[1].due)[0];
        if (!next || next[1].due > end) break;
        now = next[1].due; timers.delete(next[0]); next[1].fn();
      }
      now = end;
    },
  };
}

test('starts when noticed, hides at 60 seconds, keeps archived report accessible', () => {
  const app = setup();
  app.advance(120000);
  app.seen(0.2); app.advance(70000);
  assert.equal(app.reply.hidden, false);
  app.seen(); app.advance(59999);
  assert.equal(app.reply.hidden, false);
  app.advance(1);
  assert.equal(app.reply.hidden, true);
  assert.equal(app.disconnected(), true);
  let prevented = false;
  app.link.listeners.click({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(app.history.open, true);
  assert.equal(app.savedReport.open, true);
  assert.equal(app.savedReport.scrolled, true);
  assert.equal(app.summary.focused, true);
});

test('scrolling away, hidden tab, another lab section and fullscreen pause reading time', () => {
  const app = setup();
  app.seen(); app.advance(10000);
  app.seen(0); app.advance(100000);
  app.seen(); app.advance(10000);
  app.document.visibilityState = 'hidden'; app.event('visibilitychange'); app.advance(100000);
  app.document.visibilityState = 'visible'; app.event('visibilitychange'); app.advance(10000);
  app.document.modal = true; app.event('lab:preview-state'); app.advance(100000);
  app.document.modal = false; app.event('lab:preview-state'); app.advance(10000);
  app.reply.panelHidden = true; app.event('lab:preview-state'); app.advance(100000);
  app.reply.panelHidden = false; app.event('lab:preview-state'); app.advance(19999);
  assert.equal(app.reply.hidden, false);
  app.advance(1);
  assert.equal(app.reply.hidden, true);
});

test('reload preserves remaining time and does not bring back an expired report', () => {
  const store = new Map();
  const first = setup({store}); first.seen(); first.advance(25000); first.event('pagehide');
  const second = setup({store}); second.seen(); second.advance(34999);
  assert.equal(second.reply.hidden, false);
  second.advance(1);
  assert.equal(second.reply.hidden, true);
  assert.equal(setup({store}).reply.hidden, true);
  const newVersion = setup({store, key: 'version-2'}); newVersion.seen(); newVersion.advance(59999);
  assert.equal(newVersion.reply.hidden, false);
});

test('generation hides old report without letting its clock run in the background', () => {
  const app = setup(); app.seen(); app.advance(20000);
  app.reply.hidden = true; app.document.busy = true; app.event('lab:preview-state');
  app.advance(100000);
  assert.equal(Number(app.store.get('lab-report:version-1')), 40000);
});

test('works without browser storage or IntersectionObserver', () => {
  const app = setup({storageBlocked: true, observerEnabled: false});
  app.advance(120000); assert.equal(app.reply.hidden, false);
  app.reply.top = 100; app.event('scroll'); app.advance(60000);
  assert.equal(app.reply.hidden, true);
});

test('greeting stays visible and expiration preserves keyboard focus', () => {
  const greeting = setup({key: ''}); greeting.advance(120000);
  assert.equal(greeting.reply.hidden, false);
  const app = setup(); app.document.activeElement = app.reply; app.seen(); app.advance(60000);
  assert.equal(app.link.focused, true);
});

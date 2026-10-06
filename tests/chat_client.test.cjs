// Run with `node --test tests/chat_client.test.cjs` (no npm dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() {
    this.children = []; this.listeners = {}; this.disabled = false;
    this.value = ''; this.textContent = ''; this.files = [];
    this.classList = { add() {} };
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  closest() { return this.parent.parent; }
  remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
  contains(node) { return this.children.includes(node); }
}

function setup(fetch) {
  const ids = ['chat-form', 'message-list', 'message-input', 'attachment-input', 'file-name', 'send-button'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  const input = elements['message-input'];
  input.value = 'Важное описание проекта';
  const file = elements['attachment-input'];
  file.value = 'example.txt'; file.files = [{ name: 'example.txt' }];
  let destination;
  const window = { location: { hash: '', pathname: '/chat/test/', assign(value) { destination = value; } } };
  const document = {
    getElementById: id => elements[id], querySelector: () => null,
    createElement: () => new Element(), createTextNode: text => ({ textContent: text }),
  };
  const context = { document, window, fetch, FormData: class {}, AbortController, TextDecoder, setTimeout, clearTimeout };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/founder/js/chat.js'), 'utf8'), context);
  const submit = () => elements['chat-form'].listeners.submit({ preventDefault() {} });
  return { elements, input, file, submit, destination: () => destination };
}

const rejectedResponse = (status, error) => ({ ok: false, status, json: async () => ({ error }) });
function streamResponse(events) {
  return {
    ok: true, headers: { get: () => 'text/event-stream' },
    body: new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('')));
      controller.close();
    } }),
  };
}

test('HTTP rejection retains text and attachment and allows correction', async () => {
  for (const status of [400, 403, 413, 429, 503]) {
    const app = setup(async () => rejectedResponse(status, 'Попробуйте позже'));
    await app.submit();
    assert.equal(app.input.value, 'Важное описание проекта');
    assert.equal(app.file.value, 'example.txt');
    assert.equal(app.elements['send-button'].disabled, false);
    assert.equal(app.elements['message-list'].children.length, 1); // No unsent user bubble.
  }
});

test('login redirect retains draft without trying to parse HTML as a stream', async () => {
  const app = setup(async () => ({ ok: true, redirected: true }));
  await app.submit();
  assert.equal(app.input.value, 'Важное описание проекта');
  assert.equal(app.elements['send-button'].disabled, false);
});

test('ambiguous connection failure keeps draft and prevents blind duplicate send', async () => {
  const app = setup(async () => { throw new Error('Network error'); });
  await app.submit();
  assert.equal(app.input.value, 'Важное описание проекта');
  assert.equal(app.input.disabled, false);
  assert.equal(app.elements['send-button'].disabled, true);
});

test('successful stream navigates to newest history page after persisted completion', async () => {
  const app = setup(async () => streamResponse([{ type: 'delta', text: 'Ответ' }, { type: 'done' }]));
  await app.submit();
  assert.equal(app.input.value, '');
  assert.equal(app.file.value, '');
  assert.equal(app.destination(), '/chat/test/');
});

test('incomplete stream requires refresh instead of resending a stored message', async () => {
  const app = setup(async () => streamResponse([{ type: 'delta', text: 'Часть ответа' }]));
  await app.submit();
  assert.equal(app.elements['send-button'].disabled, true);
  assert.equal(app.destination(), undefined);
  assert.equal(app.input.value, ''); // Saved server-side before the stream began.
});

test('double submit makes only one HTTP request', async () => {
  let resolve, calls = 0;
  const app = setup(() => { calls++; return new Promise(done => { resolve = done; }); });
  const first = app.submit();
  await app.submit();
  assert.equal(calls, 1);
  resolve(rejectedResponse(429, 'Подождите'));
  await first;
});

test('panel stream names each shark and gives the main reply its own bubble', async () => {
  const margarita = { type: 'speaker', speaker: 'margarita', name: 'Маргарита', title: 'финансист-скептик', initial: 'М' };
  const app = setup(async () => streamResponse([
    margarita,
    { type: 'speaker', speaker: 'timur', name: 'Тимур', title: 'продуктовик', initial: 'Т' },
    { type: 'delta', text: 'Дай человеку рассказать.' },
    margarita,
    { type: 'delta', text: 'Сколько стоит клиент?' },
    { type: 'done' },
  ]));
  await app.submit();
  const [, aside, main] = app.elements['message-list'].children;
  assert.equal(app.elements['message-list'].children.length, 3); // Founder, Timur's aside, Margarita.
  assert.equal(aside.className, 'message message-assistant shark-timur');
  assert.equal(aside.children[1].children[1].textContent, 'Дай человеку рассказать.');
  assert.equal(main.children[0].textContent, 'М');
  assert.equal(main.children[1].children[0].textContent, 'Маргарита · финансист-скептик');
  assert.equal(main.children[1].children[1].textContent, 'Сколько стоит клиент?');
  assert.equal(app.destination(), '/chat/test/');
});

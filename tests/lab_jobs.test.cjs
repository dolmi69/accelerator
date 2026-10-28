const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(require('node:path').join(__dirname, '../static/founder/js/lab-jobs.js'), 'utf8');
function setup(initial, statusJob = initial) {
  const events = {}, windowEvents = {}, requests = [], states = [], redirects = [];
  const node = () => ({value: '', textContent: '', disabled: false, children: [],
    append(...items) { this.children.push(...items); }, setAttribute() {}, dataset: {}, remove() {},
    querySelector(selector) { return this.children.flatMap(x => [x, ...(x.children || [])]).find(x => selector.includes('prompt') ? 'jobPrompt' in x.dataset : 'jobMessage' in x.dataset); }});
  const input = node(), send = node(), status = node(), modelRoute = node();
  const form = {dataset: {jobsUrl: '/jobs/', jobStatusUrl: '/status/'}, checkValidity: () => true,
    querySelector: key => key.includes('prompt') ? input : send, querySelectorAll: () => [],
    addEventListener: (key, fn) => { events[`form:${key}`] = fn; }};
  const elements = {'lab-form': form, 'lab-status': status, 'lab-model-route': modelRoute, 'lab-job-state': {textContent: JSON.stringify(initial)}, 'lab-thread': node()};
  const document = {hidden: false, getElementById: key => elements[key], createElement: node, querySelectorAll: () => [],
    addEventListener: (key, fn) => { events[key] = fn; }, dispatchEvent: event => states.push(event.detail)};
  let returned = statusJob;
  const context = {document, window: {addEventListener: (key, fn) => { windowEvents[key] = fn; }},
    fetch: async (url, options) => { requests.push({url, options}); return {ok: true, json: async () => ({job: returned})}; },
    CustomEvent: class {constructor(type, options) { this.type = type; this.detail = options.detail; }},
    Event: class {}, FormData: class {set() {}}, crypto: {randomUUID: () => 'new-job'}, URL,
    setTimeout() {}, clearTimeout() {}, location: {href: 'http://localhost/lab/', origin: 'http://localhost', replace: url => redirects.push(url)}};
  input.dispatchEvent = () => {};
  vm.runInNewContext(code, context);
  events.DOMContentLoaded();
  return {input, send, status, modelRoute, states, requests, redirects, events, windowEvents, setJob: job => { returned = job; }};
}
const running = {id: 'job1', status: 'running', mode: 'edit', prompt: 'Создай сайт', url: '/lab/'};
const flush = () => new Promise(resolve => setImmediate(resolve));

test('reload restores running animation and locks duplicate submission', async () => {
  const app = setup(running);
  await flush();
  assert.equal(app.send.disabled, true);
  assert.ok(app.states.some(state => state.active && state.mode === 'edit'));
  await app.events['form:submit']({preventDefault() {}});
  assert.equal(app.requests.filter(x => x.options.method === 'POST').length, 0);
});
test('returning to page discovers completion and opens saved result', async () => {
  const app = setup(running);
  await flush();
  app.setJob({...running, status: 'succeeded', url: '/lab/?version=result'});
  await app.windowEvents.focus();
  assert.deepEqual(app.redirects, ['http://localhost/lab/?version=result']);
});
test('persisted failure removes animation, restores prompt and allows retry', async () => {
  const app = setup({...running, status: 'failed', error: 'Model failed'});
  await flush();
  assert.equal(app.send.disabled, false);
  assert.equal(app.input.value, 'Создай сайт');
  assert.equal(app.status.textContent, 'Model failed');
  assert.equal(app.states.at(-1).active, false);
});
test('an already rendered completed job does not reload in a loop', async () => {
  const app = setup({...running, status: 'succeeded'});
  await flush();
  assert.equal(app.redirects.length, 0);
});

test('reload shows the persisted model and updates from analysis to generation', async () => {
  const app = setup({...running, mode:'auto', model_route:{phase:'analysis',label:'GPT‑6 Luna',reason:'Разбор запроса'}});
  await flush();
  assert.equal(app.modelRoute.textContent, 'Разбор: GPT‑6 Luna · Разбор запроса');
  app.setJob({...running, model_route:{phase:'code',label:'GPT‑6.1 Sol',reason:'Сложная задача'}});
  await app.windowEvents.focus();
  assert.equal(app.modelRoute.textContent, 'Задача: GPT‑6.1 Sol · Сложная задача');
});

test('a queued request does not claim a model has already been selected', async () => {
  const app = setup({...running, status:'queued', mode:'auto'});
  await flush();
  assert.equal(app.modelRoute.textContent, 'Выбираем модель для запроса…');
});

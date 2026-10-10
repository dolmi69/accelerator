const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../static/founder/js/lab-workspace.js'), 'utf8');
const channels = new Set();
class Channel {
  constructor(key) { this.key = key; channels.add(this); }
  addEventListener(_, listener) { this.listener = listener; }
  postMessage(data) {
    for (const channel of channels) if (channel !== this && channel.key === this.key) channel.listener({data});
  }
}
const response = (project_id, revision) => ({ok: true, headers: {get: () => 'application/json'},
  json: async () => ({project_id, revision, token: `token-${revision}`})});
function setup(projectId, fetch, userId = '1') {
  const form = {dataset: {projectId, userId}, action: '/activate/'};
  const events = [];
  const window = {addEventListener() {}};
  vm.runInNewContext(code, {window,
    document: {getElementById: () => form, dispatchEvent: event => events.push(event.detail.active)},
    fetch, FormData: class {}, CustomEvent: class {constructor(_, options) { this.detail = options.detail; }},
    localStorage: {setItem() {}}, BroadcastChannel: Channel,
  });
  return {workspace: window.labWorkspace, events};
}

test('selects project with authenticated POST before allowing launch', async () => {
  const calls = [];
  const app = setup('A', async (url, options) => { calls.push({url, options}); return response('A', 1); });
  await app.workspace.ready;
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(app.workspace.token, 'token-1');
  assert.equal(app.workspace.active, true);
});

test('switching projects closes previous tab, preserves other accounts, permits explicit reopening', async () => {
  let next = 1;
  const first = setup('A', async () => response('A', next++), 'switch-user');
  await first.workspace.ready;
  const foreign = setup('C', async () => response('C', 1), 'different-user');
  await foreign.workspace.ready;
  const second = setup('B', async () => response('B', next++), 'switch-user');
  await second.workspace.ready;
  assert.equal(first.workspace.active, false);
  assert.equal(first.events.at(-1), false);
  assert.equal(foreign.workspace.active, true);
  await first.workspace.activate();
  assert.equal(first.workspace.active, true);
  assert.equal(second.workspace.active, false);
});

test('late selection response cannot undo a newer project selection', async () => {
  let finish;
  const first = setup('A', () => new Promise(resolve => { finish = resolve; }), 'late-user');
  const second = setup('B', async () => response('B', 2), 'late-user');
  await second.workspace.ready;
  finish(response('A', 1));
  const result = await first.workspace.ready;
  assert.ok(result.error);
  assert.equal(first.workspace.active, false);
  assert.equal(second.workspace.active, true);
});

test('overlapping activation calls share a request rather than racing', async () => {
  let finish, calls = 0;
  const app = setup('A', () => { calls++; return new Promise(resolve => { finish = resolve; }); }, 'one-request');
  const again = app.workspace.activate();
  finish(response('A', 1));
  await Promise.all([app.workspace.ready, again]);
  assert.equal(calls, 1);
});

test('rejected selection is handled and never produces a launch token', async () => {
  const app = setup('A', async () => ({ok: false, headers: {get: () => 'application/json'},
    json: async () => ({error: 'Занято'})}), 'rejected-user');
  const result = await app.workspace.ready;
  assert.equal(result.error.message, 'Занято');
  assert.equal(app.workspace.token, null);
});

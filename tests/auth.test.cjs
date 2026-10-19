const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/founder/js/auth.js'), 'utf8');
const freshToken = 'F'.repeat(64);

function setup(fetch) {
  let handler;
  let error;
  let posts = 0;
  const button = { disabled: false };
  const token = { value: 'old-token' };
  const form = {
    dataset: { csrfUrl: '/auth/csrf/' },
    addEventListener(_type, fn) { handler = fn; },
    querySelector(selector) {
      if (selector === '[data-auth-error]') return error;
      if (selector.includes('csrfmiddlewaretoken')) return token;
      return button;
    },
    prepend(node) { error = node; },
    requestSubmit(submitter) {
      const event = { submitter, prevented: false, preventDefault() { this.prevented = true; } };
      handler(event);
      if (!event.prevented) posts += 1;
    },
  };
  vm.runInNewContext(source, {
    window: { fetch },
    document: {
      querySelectorAll() { return [form]; },
      createElement() { return { dataset: {}, setAttribute() {} }; },
    },
  });
  return { button, token, form,
    submit: () => handler({ submitter: button, preventDefault() {} }),
    get error() { return error; }, get posts() { return posts; } };
}

test('refreshes a stale form before its normal POST, including after returning Back', async () => {
  let calls = 0;
  const app = setup(async (url, options) => {
    assert.equal(url, '/auth/csrf/');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    calls += 1;
    return { ok: true, json: async () => ({ csrfToken: freshToken }) };
  });
  await app.submit();
  assert.equal(app.token.value, freshToken);
  assert.equal(app.posts, 1);
  await app.submit();
  assert.equal(calls, 2);
  assert.equal(app.posts, 2);
});

test('double clicks wait for the same refresh and submit only once', async () => {
  let complete;
  const app = setup(() => new Promise(resolve => { complete = resolve; }));
  const first = app.submit();
  await app.submit();
  assert.equal(app.button.disabled, true);
  complete({ ok: true, json: async () => ({ csrfToken: freshToken }) });
  await first;
  assert.equal(app.posts, 1);
  assert.equal(app.button.disabled, false);
});

test('network or invalid responses preserve form and allow a retry without posting', async () => {
  let succeed = false;
  const app = setup(async () => ({ ok: true, json: async () => ({ csrfToken: succeed ? freshToken : 'bad' }) }));
  await app.submit();
  assert.equal(app.posts, 0);
  assert.equal(app.token.value, 'old-token');
  assert.equal(app.error.hidden, false);
  assert.equal(app.button.disabled, false);
  succeed = true;
  await app.submit();
  assert.equal(app.posts, 1);
  assert.equal(app.error.hidden, true);
});

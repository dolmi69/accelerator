// Run with `node --test tests/password_toggle.test.cjs` (no npm dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { describeToggle } = require('../static/founder/js/password-toggle.js');

test('hidden password: field is type password, button offers to show it', () => {
  assert.deepEqual(describeToggle(false), { type: 'password', label: 'Показать пароль', pressed: false });
});

test('shown password: field is type text, button offers to hide it', () => {
  assert.deepEqual(describeToggle(true), { type: 'text', label: 'Скрыть пароль', pressed: true });
});

test('undefined state is treated as hidden', () => {
  assert.equal(describeToggle(undefined).type, 'password');
});

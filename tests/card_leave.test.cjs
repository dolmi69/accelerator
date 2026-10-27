// Run with `node --test tests/card_leave.test.cjs` (no npm dependencies).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { serialize, isDirty, shouldInterceptClick } = require('../static/founder/js/card-leave.js');

const field = (name, value, type = 'text') => ({ name, value, type });
const HERE = 'https://app.test/startups/1/card/';

test('serialize ignores csrf, action, instruction and buttons', () => {
  const base = [field('name', 'Clinic'), field('tagline', 'Расписание')];
  const noise = [
    field('csrfmiddlewaretoken', 'token-a'),
    field('action', 'save', 'submit'),
    field('instruction', 'короче'),
    field('', 'anonymous'),
  ];
  assert.equal(serialize([...base, ...noise]), serialize(base));
});

test('serialize encodes checkboxes by checked state, not by value', () => {
  const on = { name: 'share_radar', value: 'on', type: 'checkbox', checked: true };
  const off = { name: 'share_radar', value: 'on', type: 'checkbox', checked: false };
  assert.notEqual(serialize([on]), serialize([off]));
});

test('clean form with an unchanged snapshot is not dirty', () => {
  const fields = [field('name', 'Clinic'), field('summary', 'Текст')];
  const snapshot = serialize(fields);
  assert.equal(isDirty(snapshot, fields, false), false);
});

test('editing a field makes the form dirty, and reverting makes it clean again', () => {
  const fields = [field('name', 'Clinic')];
  const snapshot = serialize(fields);
  fields[0].value = 'Clinic 2';
  assert.equal(isDirty(snapshot, fields, false), true);
  fields[0].value = 'Clinic';
  assert.equal(isDirty(snapshot, fields, false), false);
});

test('a generated Bruno draft is dirty even when it matches the saved values', () => {
  const fields = [field('name', 'Clinic')];
  assert.equal(isDirty(serialize(fields), fields, true), true);
});

test('plain same-origin link to another section is intercepted', () => {
  const anchor = { href: 'https://app.test/startups/1/review/' };
  assert.equal(shouldInterceptClick({ button: 0 }, anchor, HERE), true);
});

test('modified or non-primary clicks are not intercepted', () => {
  const anchor = { href: 'https://app.test/startups/1/review/' };
  assert.equal(shouldInterceptClick({ button: 0, ctrlKey: true }, anchor, HERE), false);
  assert.equal(shouldInterceptClick({ button: 0, metaKey: true }, anchor, HERE), false);
  assert.equal(shouldInterceptClick({ button: 1 }, anchor, HERE), false);
});

test('new-tab, download, external and same-page links are not intercepted', () => {
  const ev = { button: 0 };
  const blank = { href: 'https://app.test/x/', target: '_blank' };
  const download = { href: 'https://app.test/x/', hasAttribute: name => name === 'download' };
  const external = { href: 'https://example.org/' };
  const samePage = { href: 'https://app.test/startups/1/card/' };
  assert.equal(shouldInterceptClick(ev, blank, HERE), false);
  assert.equal(shouldInterceptClick(ev, download, HERE), false);
  assert.equal(shouldInterceptClick(ev, external, HERE), false);
  assert.equal(shouldInterceptClick(ev, samePage, HERE), false);
});

test('missing anchor is not intercepted', () => {
  assert.equal(shouldInterceptClick({ button: 0 }, null, HERE), false);
});

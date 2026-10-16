const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(require('node:path').join(__dirname, '../static/founder/js/lab-split.js'), 'utf8');
function setup(saved = null, small = false) {
  const element = () => ({handlers: {}, attrs: {}, dataset: {}, classList: {add() {}, remove() {}},
    addEventListener(name, fn) { this.handlers[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = value; }});
  const layout = element(), splitter = element(), mobile = element(), window = element();
  const chat = element(), preview = element();
  chat.dataset.labPane = 'chat'; preview.dataset.labPane = 'preview';
  mobile.matches = small;
  layout.style = {setProperty() {}};
  layout.getBoundingClientRect = () => ({left: 100, width: small ? 390 : 1216});
  splitter.offsetWidth = 16;
  splitter.setPointerCapture = () => {};
  splitter.focus = () => {};
  window.matchMedia = () => mobile;
  const storage = {value: saved, getItem() { return this.value; }, setItem(_, value) { this.value = value; }};
  vm.runInNewContext(code, {window, localStorage: storage, document: {
    querySelector: () => layout, getElementById: () => splitter,
    querySelectorAll: () => [chat, preview], body: element(), addEventListener() {},
  }});
  const key = value => splitter.handlers.keydown({key: value, preventDefault() {}});
  return {layout, splitter, mobile, storage, key, chat, preview};
}
test('defaults to 28/72, restores a saved width and resets with Enter', () => {
  const app = setup();
  assert.equal(app.splitter.attrs['aria-valuenow'], '28');
  app.key('ArrowRight');
  assert.equal(setup(app.storage.value).splitter.attrs['aria-valuenow'], '30');
  app.key('Enter');
  assert.equal(app.storage.value, '28');
});
test('dragging clamps usable widths, cancellation restores the prior width', () => {
  const app = setup();
  app.splitter.handlers.pointerdown({button: 0, pointerId: 1, preventDefault() {}});
  app.splitter.handlers.pointermove({clientX: 5000});
  assert.equal(app.splitter.attrs['aria-valuenow'], '73');
  app.splitter.handlers.pointercancel();
  assert.equal(app.splitter.attrs['aria-valuenow'], '28');
  assert.equal(app.storage.value, null);
});
test('mobile layout preserves the saved desktop ratio', () => {
  const app = setup('55', true);
  assert.equal(app.splitter.attrs['aria-valuenow'], '55');
  assert.equal(app.storage.value, '55');
});

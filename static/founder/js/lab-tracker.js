/* Runs inside an opaque sandbox. Never reads input values, URLs or cookies. */
(() => {
  'use strict';
  const channel = document.currentScript.dataset.channel;
  const destination = document.currentScript.dataset.parentOrigin;
  let sent = 0;
  const initialLabels = new WeakMap();
  const formAttempts = new WeakMap();
  const emit = (kind, detail = {}) => {
    if (sent++ >= 200) return;
    window.parent.postMessage({source: 'bruno-lab', channel, kind, ...detail}, destination);
  };
  const path = element => {
    const parts = [];
    for (let node = element; node && node !== document.body && parts.length < 4; node = node.parentElement) {
      const tag = node.tagName.toLowerCase();
      const siblings = Array.from(node.parentElement?.children || []).filter(item => item.tagName === node.tagName);
      parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(node) + 1})`);
    }
    return parts.join(' > ').slice(0, 160);
  };
  const attemptForm = form => {
    if (!form) return;
    const now = performance.now();
    if (now - (formAttempts.get(form) ?? -1000) < 250) return;
    formAttempts.set(form, now);
    emit('form', {target: path(form)});
  };
  window.addEventListener('error', () => emit('error'));
  window.addEventListener('unhandledrejection', () => emit('error'));
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('button,a,summary,[role="button"]').forEach(element => {
      // Snapshot static labels before interaction; dynamic or entered text is excluded.
      if (!element.querySelector('input,textarea,[contenteditable]')) {
        initialLabels.set(element, (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80));
      }
    });
    emit('ready');
  }, {once: true});
  document.addEventListener('click', event => {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    const element = event.target.closest('button,a,summary,[role="button"],input[type="submit"]');
    if (!element || element.closest('[contenteditable]')) return;
    emit('click', {target: path(element), label: initialLabels.get(element) || ''});
    // The sandbox blocks actual submissions, sometimes before a submit event.
    // Record the user's attempt without enabling forms or reading their contents.
    if (element.type === 'submit') attemptForm(element.form);
  }, true);
  document.addEventListener('keydown', event => {
    if (event.isTrusted && !event.repeat && !event.isComposing && event.key === 'Enter'
        && event.target instanceof HTMLInputElement) attemptForm(event.target.form);
  }, true);
  document.addEventListener('submit', event => {
    if (event.isTrusted) attemptForm(event.target);
  }, true);
  let depth = 0, timer;
  document.addEventListener('scroll', () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const range = document.documentElement.scrollHeight - window.innerHeight;
      const current = range > 0 ? Math.min(100, Math.floor(window.scrollY / range * 100)) : 0;
      const milestone = Math.floor(current / 25) * 25;
      if (milestone > depth) { depth = milestone; emit('scroll', {depth}); }
    }, 250);
  }, {passive: true});
})();

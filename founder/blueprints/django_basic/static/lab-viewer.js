(() => {
  const bar = document.querySelector('.lab-return-bar');
  if (!bar) return;
  const back = bar.querySelector('[data-lab-return]');
  const embedded = window.parent !== window;
  const exit = () => {
    if (embedded) window.parent.postMessage({type: 'cofounder:lab-exit'}, bar.dataset.labOrigin);
    else window.location.assign(back.href);
  };
  // The parent viewer provides its own persistent toolbar when embedded.
  if (embedded) bar.hidden = true;
  back.addEventListener('click', event => { event.preventDefault(); exit(); });
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !event.isComposing) { event.preventDefault(); exit(); }
  }, true);
  window.addEventListener('message', event => {
    const prototype = document.querySelector('iframe.prototype');
    if (prototype && event.source === prototype.contentWindow && event.origin === 'null'
        && event.data?.type === 'cofounder:lab-exit') exit();
  });
})();

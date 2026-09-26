(() => {
  const app = window.Telegram?.WebApp;
  if (!app || app.platform === 'unknown') return;
  // Telegram identity is not used for login; existing site accounts stay intact.
  app.ready();
  app.expand();
  if (app.isVersionAtLeast('6.1')) {
    app.setHeaderColor('#ffffff');
    app.setBackgroundColor('#f5f4ef');
    const back = document.querySelector('.chat-heading a[href]');
    if (back) {
      app.BackButton.show();
      app.BackButton.onClick(() => window.location.assign(back.href));
    } else {
      app.BackButton.hide();
    }
  }
})();

/* Enhancements are optional: all account and module forms work without JS. */
(() => {
  document.querySelectorAll('[data-password-toggle]').forEach(button => {
    const input = document.getElementById(button.dataset.passwordToggle);
    if (!input) return;
    button.hidden = false;
    button.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(show));
      button.setAttribute('aria-label', show ? 'Скрыть пароль' : 'Показать пароль');
    });
  });
  document.querySelectorAll('[data-file-input]').forEach(input => {
    input.addEventListener('change', () => {
      const file = input.files[0], label = input.closest('.file-field')?.querySelector('[data-file-name]');
      if (label) label.textContent = file ? file.name : 'Выбрать файл';
      const avatar = input.name === 'avatar' && document.querySelector('[data-avatar-preview]');
      if (!avatar || !file || file.size > 5 * 1024 * 1024 || !['image/jpeg','image/png','image/webp'].includes(file.type)) return;
      const reader = new FileReader();
      reader.onload = () => { const image = new Image(); image.src = reader.result; image.alt = 'Предпросмотр выбранной аватарки'; avatar.replaceChildren(image); };
      reader.readAsDataURL(file);
    });
  });
  document.querySelectorAll('.account-menu').forEach(menu => {
    document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.open) { menu.open = false; menu.querySelector('summary').focus(); event.preventDefault(); event.stopPropagation(); }
    });
  });
})();

// «Клиент и ценность»: выбранный вариант Бруно подставляется в поле, где его можно поправить.
document.querySelectorAll('[data-value-choice]').forEach(radio => radio.addEventListener('change', () => {
  const field = document.querySelector('[data-value-final]');
  if (field && radio.checked) { field.value = radio.value; field.focus(); }
}));

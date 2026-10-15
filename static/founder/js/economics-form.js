// План расходов на странице рынка: строка «+ Ещё статья» и сумма в месяц. Без JS форма тоже работает:
// сервер всегда отдаёт одну пустую строку.
(() => {
  const form = document.querySelector('[data-economics-form]');
  if (!form) return;
  const rows = form.querySelector('[data-expense-rows]');
  const add = form.querySelector('[data-expense-add]');
  const total = form.querySelector('[data-expense-total]');
  const MAX_ROWS = 12;

  const number = (value) => {
    const parsed = parseFloat(String(value).replace(/\s/g, '').replace(',', '.'));
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  };
  const update = () => {
    const sum = [...rows.querySelectorAll('input[name="expense_amount"]')].reduce((acc, input) => acc + number(input.value), 0);
    total.textContent = sum ? `Итого ${Math.round(sum).toLocaleString('ru-RU')} ₽ в месяц` : '';
    add.hidden = rows.children.length >= MAX_ROWS;
  };

  add.addEventListener('click', () => {
    const row = rows.lastElementChild.cloneNode(true);
    row.querySelectorAll('input').forEach((input) => { input.value = ''; });
    row.querySelector('input[name="expense_name"]').placeholder = 'На что';
    rows.append(row);
    row.querySelector('input').focus();
    update();
  });
  rows.addEventListener('input', update);
  update();
})();

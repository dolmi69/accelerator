# Форматирование шаблонов

Исходники HTML/Django хранятся с переносами строк и отступами в 4 пробела.
Это относится и к интерфейсу платформы (`templates/`), и к заготовкам
скачиваемого сайта (`founder/blueprints/django_basic/templates/`).
HTML в исходниках не минифицируется. Длинные теги и вложенные условия должны
оставаться читаемыми. Если содержимое действительно чувствительно к пробелам
(например, inline-разметка, textarea, pre или строка JavaScript), его нужно сохранить.

Настройки находятся в `.djlintrc`. Форматтер установлен отдельно от рабочих
зависимостей приложения:

```sh
python -m pip install -r requirements-dev.txt
python -m djlint templates founder/blueprints/django_basic/templates --reformat
```

Проверить формат без изменения файлов:

```sh
python -m djlint templates founder/blueprints/django_basic/templates --check
```

После массового форматирования проверяйте платформу и экспортируемый сайт:

```sh
python manage.py test founder
python manage.py test_blueprint
```

Форматтер не переписывает встроенные JavaScript и CSS. Пометки `djlint:off` /
`djlint:on` защищают небольшие фрагменты, где переносы изменили бы содержимое.

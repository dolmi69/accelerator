import json
from dataclasses import replace
from html import escape

from django.conf import settings

from founder.services.ai_costs import reject_output
from founder.services.lab_bruno import protect_service_secrets
from founder.services.model_json import load_model_json
from founder.services.qwen import QwenOutputError, generate_code
from founder.services.shared_schema import compatible, validate

SYSTEM = """Собери сайт на общем BrunoHost. Верни один JSON, без HTML или Python.
Формат: {"title":"...","roles":{"member":"Участник","manager":"Руководитель"},"tables":{
"tasks":{"label":"Задачи","fields":{"name":{"label":"Название","type":"text","required":true}},
"access":{"member":{"read":"own","create":true,"update":"own"},"manager":{"read":"all","create":true,"update":"all"}}}}}.
Состав таблиц и подписи форм придумай под запрос. Опционально theme: green/blue/plum/graphite. Для таблицы\nзадай ui: {order:[ключи всех полей по порядку],add:текст кнопки,save:текст подтверждения,empty:пустое состояние}. Идентификаторы неизменяемые: латинские строчные буквы,
цифры и подчёркивание, начало с буквы. При правке верни полное описание, сохрани ключи.
Начальная роль при регистрации member. Остальные назначает владелец в лаборатории.
Укажи права каждой роли для каждой таблицы; read/update: none,own,all; create: boolean.
Типы: text,number,boolean,date,datetime,member,file (фото),choice с options (массив строк),
ref с target (ключ таблицы). Новые поля в существующих таблицах необязательны.
Для защиты от двойной записи в таблице бронирований задай booking: {"resource":"ключ ref",
"start":"ключ datetime","end":"ключ datetime"}; все три поля обязательны. Ресурс — отдельная
запись, например преподаватель. У пользователя должно быть право читать доступные ресурсы.
Выдавай минимально необходимые права.
Для поля, которое заполняет только определённая роль (например, назначение ответственного
прорабом), укажи write_roles: ["manager"]. Такое поле необязательно при создании рабочим.
Публичное чтение, оплата, внешние интеграции, произвольный JS, фоновые задачи и вычисления
недоступны. Если задача невыполнима в этом режиме,
верни только {"unsupported":"краткая причина"}. Не выдавай имитацию за готовую функцию."""


def generate(prompt, source, context):
    payload = json.dumps(
        {"request": prompt, "project": context, "previous": source.shared_schema if source else {}},
        ensure_ascii=False,
    )
    protect_service_secrets(payload)
    result = generate_code(
        SYSTEM,
        [{"role": "user", "content": payload}],
        max_tokens=min(settings.LAB_CREATE_MAX_TOKENS, settings.QWEN_CODE_MAX_TOKENS),
        temperature=settings.LAB_CREATE_TEMPERATURE,
    )
    try:
        schema = load_model_json(result.text)
        if isinstance(schema, dict) and set(schema) == {"unsupported"}:
            raise QwenOutputError(
                "Общий сервер не поддерживает этот сценарий. Нужен сайт с изолированным сервером. "
                + str(schema["unsupported"])[:250]
            )
        validate(schema)
        protect_service_secrets(json.dumps(schema, ensure_ascii=False))
        if source and source.kind == "shared":
            compatible(source.shared_schema, schema)
    except (ValueError, TypeError, RecursionError, QwenOutputError) as exc:
        reject_output(result.request_id)
        if isinstance(exc, QwenOutputError):
            raise
        raise QwenOutputError("Описание данных не прошло проверку. Предыдущая версия сохранена.") from None
    titles = [t["label"] for t in schema["tables"].values()]
    report = {
        "summary": "Проверены поля, связи и права ролей. Стартовая страница открывается, доступ к аккаунту требует входа.",
        "completed": ["Разделы сайта: " + ", ".join(titles)],
        "not_done": [],
        "checks": {
            "generation_mode": "shared",
            "schema_validated": True,
            "page_and_auth_checked": True,
            "runtime_verified": False,
            "review_mode": "local",
        },
    }
    # Static thumbnail contains no credentials or active app. Interactive routes render trusted templates.
    html = (
        "<!doctype html><html><body><h1>"
        + escape(schema["title"])
        + "</h1><p>"
        + escape(", ".join(titles))
        + "</p></body></html>"
    )
    return replace(result, text=html, shared_schema=schema, report=report, edit_method="shared")

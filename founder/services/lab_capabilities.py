"""The generated frontend may call only the trusted prototype bridge."""
import re

CAPABILITY_PROMPT = '''Для настоящих действий используй только готовый window.BrunoApp,
уже внедрённый платформой. Не объявляй/переопределяй BrunoApp, authModule,
chatModule или другие заглушки сервера. Не вызывай fetch, WebSocket, parent,
cookies, confirm/prompt для авторизации или отправки данных.
await BrunoApp.auth.getSession() возвращает {authenticated,user,capabilities}.
BrunoApp.auth.login() открывает настоящий вход на этом сайте.
await BrunoApp.results.save({title,content}) открывает подтверждение и сохраняет
текст в личном аккаунте; ответ {persisted:true,result:{id,...}} только после записи.
await BrunoApp.results.list() возвращает {results:[{id,title,content,created_at}]}.
await BrunoApp.chat.shareResult({title,content}) открывает настоящее окно выбора
получателя, сохраняет результат и отправляет личное сообщение; доступно только
при включённом chat. Можно передать {resultId:сохранённый_id}. Ответ содержит
persisted:true, message_id, recipient, chat_url. Не выбирай получателя в коде.
title до 120 символов, content до 1800; обычный текст, не HTML. Сохраняй переносы
строк. Успех показывай после await и подтверждения persisted. Ошибку и отмену
обрабатывай через try/catch и видимый текст status внутри страницы; при ошибке
нельзя показывать «отправлено». Эти методы работают в запущенном Django-сайте.
Не обещай отсутствующих методов. Сохранение и отправка не требуют AI-запросов.
В отчёте опиши подключённые возможности, а не утверждай, что сообщение уже
доставлено/прочитано или что ты выполнил браузерный тест.'''

METHODS = {'auth.getSession', 'auth.login', 'results.save', 'results.list', 'chat.shareResult'}


def capability_prompt(modules):
    """Describe only enabled capabilities to the code generator."""
    enabled = set(modules or [])
    if 'accounts' not in enabled:
        return 'Серверная основа отключена. Не используй BrunoApp и не имитируй сохранение или отправку данных.'
    instruction = CAPABILITY_PROMPT
    if 'chat' not in enabled:
        start = instruction.index('await BrunoApp.chat.shareResult')
        end = instruction.index('title до 120')
        instruction = instruction[:start] + instruction[end:]
        instruction += '\nМодуль chat ОТКЛЮЧЁН. Не создавай кнопки отправки в чат и не вызывай BrunoApp.chat. Не подключай функции сверх задания.'
    instruction += '\nСсылки на готовые страницы делай через <a href="/путь/" data-app-route="/путь/">. Платформа проверяет маршрут; не обращайся к parent самостоятельно.'
    if 'leads' in enabled:
        instruction += '\nЗаявки — отдельный готовый модуль leads: кнопка <a href="/request/" data-app-route="/request/">Оставить заявку</a> открывает настоящую форму. Заявка НЕ является сообщением в чат или личным результатом. Не заменяй её results.save или отправкой сообщения. Собственная форма внутри HTML пока не записывает заявку: используй готовую страницу и честно укажи ограничения нестандартных полей.'
    if 'catalog' in enabled:
        instruction += '\nНастоящий каталог открывается через /catalog/ с data-app-route. Демонстрационные карточки главной страницы не добавляют записи в базу каталога; не утверждай обратное.'
    return instruction


def check_contract(prompt, before, after, modules, change_purpose=False):
    """Conservative source checks, not a claim that arbitrary JS was executed."""
    scripts = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script\s*>', after, re.I | re.S))
    scripts = re.sub(r'/\*.*?\*/|(?m:^\s*//[^\n]*)', '', scripts, flags=re.S)
    calls = set(re.findall(r'\bBrunoApp\.(\w+\.\w+)\s*\(', scripts))
    errors = []
    if re.search(r'\b(?:authModule|chatModule)\b', scripts):
        errors.append('В коде осталась выдуманная авторизация или заглушка чата. Нужны настоящие инструменты сайта.')
    if calls - METHODS or re.search(r'(?:\b(?:const|let|var)\s+BrunoApp\b|\b(?:window\.)?BrunoApp\s*=)', scripts):
        errors.append('Код использует отсутствующий инструмент или переопределяет интерфейс настоящего сервера.')
    if calls and 'accounts' not in modules:
        errors.append('Для сохранения и отправки нужна подключённая основа Django.')
    if 'chat.shareResult' in calls and 'chat' not in modules:
        errors.append('Кнопка отправки ссылается на отключённый модуль чатов.')
    text = prompt.casefold()
    mock = bool(re.search(r'макет|имитаци|демонстрационн|без.{0,20}(сервер|подключ)', text))
    wants_share = bool(re.search(r'чат|chat', text) and re.search(r'результ|расч[её]т|ккал|calor', text)
                       and re.search(r'отправ|подел|пересла|send|shar', text))
    wants_save = bool(re.search(r'сохран|save', text) and re.search(r'результ|расч[её]т', text))
    if not mock and wants_share and 'chat.shareResult' not in calls:
        errors.append('Отправка результата не подключена к настоящему чату. Визуальная кнопка не выполняет запрос.')
    if not mock and wants_save and not calls & {'results.save', 'chat.shareResult'}:
        errors.append('Сохранение результата в аккаунте не подключено.')
    for method in calls & {'results.save', 'chat.shareResult'}:
        escaped = re.escape(method)
        if not re.search(r'\bawait\s+(?:window\.)?BrunoApp\.' + escaped + r'\s*\(', scripts) and not re.search(
                r'BrunoApp\.' + escaped + r'\s*\([^;]*?\)\s*\.then\s*\(', scripts, re.S):
            errors.append('Код не дожидается подтверждения записи результата сервером.')
    # Catch the concrete purpose regression independently of a model's verdict.
    if before and not change_purpose:
        from founder.services.lab_bruno import site_facts
        old, new = site_facts(before), site_facts(after)
        old_identity = old['title'] + ' ' + ' '.join(old['headings'])
        new_identity = new['title'] + ' ' + ' '.join(new['headings'])
        if re.search(r'калори|ккал', old_identity, re.I) and re.search(r'смет', new_identity, re.I) and not re.search(r'калори|ккал', new_identity, re.I):
            errors.append('Калькулятор калорий заменён калькулятором сметы без просьбы пользователя.')
    if errors:
        from founder.services.lab_bruno import LabRequestRejected
        raise LabRequestRejected('Правка не сохранена: ' + ' '.join(dict.fromkeys(errors)) + ' Предыдущая версия сохранена.')
    return {'bridge_methods': sorted(calls), 'source_contract': 'passed',
            'browser_execution': 'not_checked', 'message_delivery': 'not_sent_by_reviewer'}

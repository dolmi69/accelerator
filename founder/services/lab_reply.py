"""Persisted Bruno reports: reviewed AI feedback and deterministic local diffs."""
from collections import Counter
from difflib import SequenceMatcher
from html.parser import HTMLParser
import re

from founder.services.backend_modules import MODULES, normalize_modules


def saved_reply(version):
    """Keep a version's original report; derive one for older saved versions."""
    return version.bruno_report if version and version.bruno_report else version_reply(version)


def incomplete_reply(source):
    return {'title': 'Дизайн пока не завершён',
            'items': ['Рабочая основа сайта с выбранными модулями сохранена.',
                      'Предыдущий дизайн сохранён.' if source else 'Новый дизайн можно запросить ещё раз.'],
            'usage': 'Незавершённый AI-ответ мог быть оплачен.', 'mood': 'focused', 'failed': True}


def reviewed_reply(version, feedback):
    """A request-specific reviewed explanation, persisted with the version."""
    base = version_reply(version)
    items = list(dict.fromkeys([feedback['summary'], *feedback['completed'],
                              *['Не выполнено: ' + item for item in feedback['not_done']]]))
    return {**base, 'title': 'Результат твоего запроса', 'items': items[:4], 'more': items[4:],
            'reviewed': feedback.get('checks', {}).get('review_mode') != 'local',
            'checks': feedback.get('checks', {}), 'mood': 'focused' if feedback['not_done'] else 'confident'}


class PageFacts(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.text = []
        self.images = []
        self.script = []
        self.styles = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'head'}:
            self.hidden.append(tag)
        if tag == 'img':
            values = dict(attrs)
            self.images.append((values.get('src', ''), values.get('alt', '')[:80]))

    def handle_endtag(self, tag):
        if tag in self.hidden:
            self.hidden = self.hidden[:self.hidden.index(tag)]

    def handle_data(self, text):
        if 'script' in self.hidden:
            self.script.append(text)
        elif 'style' in self.hidden:
            self.styles.append(text)
        elif not self.hidden:
            text = re.sub(r'\s+', ' ', text).strip()
            if text:
                self.text.append(text[:160])


def version_reply(version):
    if version is None:
        return {'title': 'Давай соберём твой сайт', 'items': ['Опиши идею слева. Здесь я расскажу, что изменил в прототипе.'], 'usage': '', 'mood': 'focused'}
    source = version.source if version.source_id else None
    if source and source.startup_id != version.startup_id:
        source = None
    current, old = PageFacts(version.html), PageFacts(source.html if source else '')
    items = []
    modules = normalize_modules(version.backend_modules) if version.kind == 'django' else []
    previous_modules = normalize_modules(source.backend_modules) if source and source.kind == 'django' else []
    added = [MODULES[key][0] for key in modules if key not in previous_modules]
    removed = [MODULES[key][0] for key in previous_modules if key not in modules]
    if added:
        items.append('Подключил: ' + ', '.join(added) + '.')
    if removed:
        items.append('Отключил: ' + ', '.join(removed) + '.')
    presentation = version.presentation or {}
    previous = source.presentation or {} if source else {}
    for key, prefix, choices in [
        ('title', 'Название сайта: ', {}),
        ('palette', 'Цветовая тема: ', {'blue':'синяя', 'green':'зелёная', 'purple':'фиолетовая', 'dark':'тёмная'}),
        ('font', 'Шрифт: ', {'system':'современный', 'serif':'классический', 'mono':'моноширинный'}),
        ('radius', 'Кнопки: ', {'0':'прямые углы', '8':'слегка округлые', '24':'округлые'}),
    ]:
        value = presentation.get(key)
        if value != previous.get(key):
            items.append(prefix + (choices.get(value, str(value)[:100]) if value else 'исходный вариант') + '.')
    replacements = presentation.get('text_replacements', {})
    for label, replacement in replacements.items():
        if replacement != previous.get('text_replacements', {}).get(label):
            was = previous.get('text_replacements', {}).get(label, label)
            items.append(f'Надпись «{was}» → «{replacement}».')
    if not source and version.model != 'django-modules-v2':
        items.insert(0, 'Собрал первую версию главной страницы.')
    elif source and version.html != source.html:
        images = list((Counter(current.images) - Counter(old.images)).elements())
        if images:
            names = [alt or 'без подписи' for _, alt in images[:3]]
            verb = 'Добавил в макет изображения: ' if len(current.images) > len(old.images) else 'Обновил изображения в макете: '
            items.append(verb + ', '.join(names) + '.')
        # Compare actual visible text, not the request's intended outcome.
        if not replacements or replacements == previous.get('text_replacements', {}):
            for operation, a, b, c, d in SequenceMatcher(a=old.text[:250], b=current.text[:250], autojunk=False).get_opcodes():
                if operation == 'replace' and b-a == d-c == 1:
                    items.append(f'Текст «{old.text[a][:80]}» → «{current.text[c][:80]}».')
                elif operation == 'insert' and c < len(current.text):
                    items.append(f'Добавил текст: «{current.text[c][:100]}».')
                elif operation == 'delete' and a < len(old.text):
                    items.append(f'Убрал текст: «{old.text[a][:100]}».')
                if len(items) >= 4:
                    break
        if current.styles != old.styles and not any(item.startswith(('Цветовая тема:', 'Шрифт:', 'Кнопки:')) for item in items):
            items.append('Обновил оформление страницы.')
        if current.script != old.script:
            items.append('Изменил поведение элементов страницы.')
        if not items:
            items.append('Обновил разметку страницы.')
    items = list(dict.fromkeys(items))
    unchanged = not items
    if unchanged:
        items = ['Содержимое и настройки совпадают с предыдущей версией.']
    free = version.input_tokens == version.output_tokens == 0 and version.edit_method == 'local'
    if free:
        usage = 'Без AI · 0 токенов'
    elif version.input_tokens is not None and version.output_tokens is not None:
        usage = f'AI: {version.input_tokens + version.output_tokens:,} токенов'.replace(',', ' ')
    else:
        usage = 'Расход AI уточняется'
    if version.edit_method == 'template':
        usage = 'Распознавание запроса · ' + usage
    return {'title': 'Изменений пока нет' if unchanged else 'Готово, вот что я сделал',
            'items': items[:3], 'more': items[3:], 'usage': usage, 'mood': 'focused' if unchanged else 'confident'}

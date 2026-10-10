"""Local opt-in UI assets and compact examples for the single Qwen call."""
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'design_guides' / 'components'
MARKER = 'v1'


@lru_cache(maxsize=1)
def assets():
    return (f'<style data-forge-components="{MARKER}">' + (ROOT / 'forge.css').read_text() + '</style>',
            f'<script data-forge-components="{MARKER}">' + (ROOT / 'forge.js').read_text() + '</script>')


class ComponentParts(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.lines = [0, *[m.end() for m in re.finditer('\n', html)]]
        self.active = None
        self.managed = []
        self.used = False
        self.feed(html)

    def position_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if (any(name.startswith('data-forge-') and name != 'data-forge-components' for name in values)
                or any(item.startswith('forge-') for item in (values.get('class') or '').split())):
            self.used = True
        if tag in {'style', 'script'} and values.get('data-forge-components') == MARKER:
            self.active = (tag, self.position_offset())

    def handle_endtag(self, tag):
        if self.active and self.active[0] == tag:
            end = self.html.find('>', self.position_offset()) + 1
            if end > self.active[1]:
                self.managed.append((self.active[1], end))
            self.active = None


def model_source(html):
    """Do not rebill shared CSS/JS on every patch; preserve all custom code."""
    for start, end in reversed(ComponentParts(html).managed):
        html = html[:start] + html[end:]
    return html


def attach_components(html):
    """Embed locally so iframe, standalone HTML and Django ZIP look the same."""
    html = model_source(html)
    if not ComponentParts(html).used:
        return html
    css, js = assets()
    # Defaults live in a lower cascade layer and precede custom CSS. Overrides
    # remain possible; generated content never gets interpolated into JS.
    html = re.sub(r'(<head\b[^>]*>)', lambda m: m[0] + css, html, count=1, flags=re.I)
    return re.sub(r'</body\s*>', lambda _: js + '</body>', html, count=1, flags=re.I)


def component_prompt(prompt, *, creating):
    catalog = json.loads((ROOT / 'catalog.json').read_text())
    intro = '''Библиотека Forge: готовые адаптивные компоненты, CSS и обработчики
фильтров/вкладок подключит платформа локально. Не генерируй их базовый CSS/JS
и не используй data-forge-components. Подключение не требует второго AI-вызова.
Используй нужные классы из каталога; можно менять разметку, порядок, размеры,
цвета, типографику и добавлять своё CSS после библиотеки. Можно создавать
полностью собственные блоки. Компоненты необязательны, это не шаблон всей страницы.
Выбери собственное направление под продукт. Варианты hero: forge-hero,
forge-hero forge-hero--center, forge-hero forge-hero--editorial; grid:
forge-grid, forge-grid--two, forge-grid--bento + forge-span-two.
Для мобильного меню можно использовать forge-hamburger: по умолчанию виден
только до 768px; раскрытие меню и aria-expanded реализуй под свою разметку.
Общие переменные: --primary-color, --bg, --surface, --text-main,
--text-secondary; дополнительные --radius, --content-width, --display-font,
--forge-button-radius, --forge-on-accent (контрастный текст кнопок).
Подписи/содержимое примеров замени под задачу. Каждая ссылка ведёт в реально
созданный id либо разрешённый серверный маршрут. Не выводи текст «Собственный SVG»:
нарисуй осмысленную иллюстрацию или выбери композицию без неё.
Не имитируй подключение API. Формулы/обработчики калькуляторов пиши под задачу,
существующие сохраняй. Вкладки и локальные фильтры уже работают по data-атрибутам.
'''
    if creating:
        chosen = catalog
    else:
        patterns = {'hero':r'перв.{0,15}экран|шапк|hero|главн.{0,12}блок',
                    'bento':r'карточ|сетк|bento|преимуществ',
                    'products':r'каталог|товар|фильтр|поиск',
                    'steps':r'шаг|как.{0,8}работ', 'workspace':r'калькулятор|форм.{0,10}расч',
                    'tabs':r'вкладк|tabs', 'faq':r'faq|вопрос.{0,15}ответ', 'cta':r'призыв|cta'}
        chosen = [entry for entry in catalog if re.search(patterns[entry['id']], prompt, re.I)][:3]
        intro += '\nКаталог: ' + ', '.join(entry['id'] + ' — ' + entry['label'] for entry in catalog) + '.\nОбщий CSS/JS библиотеки скрыт из исходника для экономии токенов и будет возвращён при сохранении. Меняй свои стили; не пытайся заменить скрытую библиотеку.\n'
    return intro + json.dumps(chosen, ensure_ascii=False, separators=(',', ':'))

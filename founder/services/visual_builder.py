"""Versioned page/block documents and a single deterministic, script-free renderer.

No user HTML, JavaScript or CSS is executed by the editor. Every preview and
release is compiled from this schema; uploaded image IDs are checked by project.
"""
import copy
import json
import re
import uuid
from html import escape
from urllib.parse import urlsplit

from django.urls import reverse

MAX_DOCUMENT_BYTES = 200_000
MAX_PAGES = 20
MAX_BLOCKS = 100
ID = re.compile(r'[a-zA-Z0-9_-]{1,64}\Z')
COLOR = re.compile(r'#[0-9a-fA-F]{6}\Z')
THEME = {'accent': '#147d73', 'background': '#faf9f6', 'ink': '#182b2a', 'font': 'modern', 'radius': 'soft'}
# Fields are shared with the inspector. A block's structure is explicit rather
# than arbitrary HTML, so editing text cannot accidentally rewrite a server.
CATALOG = {
    'nav': {'label': 'Навигация', 'group': 'Основное', 'caption': 'Логотип и переходы по страницам', 'fields': ['brand', 'button', 'link'], 'items': True,
        'props': {'brand': 'Новая история', 'button': 'Связаться', 'link': 'mailto:hello@example.com', 'items': [{'title': 'О нас', 'link': '#about'}, {'title': 'Возможности', 'link': '#features'}]}},
    'hero': {'label': 'Первый экран', 'group': 'Основное', 'caption': 'Большая идея с первого взгляда', 'fields': ['eyebrow', 'title', 'text', 'button', 'link', 'image', 'alt'],
        'props': {'eyebrow': 'СОЗДАНО ДЛЯ ВАШИХ ИДЕЙ', 'title': 'Большие перемены\nначинаются с малого.', 'text': 'Расскажите, что делает ваш проект особенным. Покажите главное и пригласите сделать первый шаг.', 'button': 'Узнать больше', 'link': '#features', 'image': '', 'alt': ''}},
    'text': {'label': 'Текст', 'group': 'Контент', 'caption': 'История, о которой хочется узнать', 'fields': ['eyebrow', 'title', 'text'],
        'props': {'eyebrow': 'О ПРОЕКТЕ', 'title': 'У каждой идеи есть своя история.', 'text': 'Здесь можно рассказать о себе, команде или продукте. Объясните, какую задачу вы решаете и почему это важно вашим посетителям.'}},
    'features': {'label': 'Возможности', 'group': 'Контент', 'caption': 'Преимущества в аккуратных карточках', 'fields': ['eyebrow', 'title', 'text'], 'items': True,
        'props': {'eyebrow': 'ПРОДУМАНО ДО ДЕТАЛЕЙ', 'title': 'Всё, что действительно нужно.', 'text': 'Три причины познакомиться с вашим проектом поближе.', 'items': [{'title': 'Просто начать', 'text': 'Понятный первый шаг без лишних сложностей.'}, {'title': 'Приятно пользоваться', 'text': 'Забота о деталях в каждом действии.'}, {'title': 'Есть куда расти', 'text': 'Новые возможности вместе с вашим проектом.'}]}},
    'image': {'label': 'Изображение', 'group': 'Медиа', 'caption': 'Ваша фотография крупным планом', 'fields': ['image', 'alt', 'title', 'text'],
        'props': {'image': '', 'alt': '', 'title': 'Детали, которые говорят сами за себя.', 'text': 'Добавьте своё изображение и короткую подпись.'}},
    'gallery': {'label': 'Галерея', 'group': 'Медиа', 'caption': 'Работы, команда или продукт', 'fields': ['eyebrow', 'title', 'text'], 'items': True, 'images': True,
        'props': {'eyebrow': 'В ДЕТАЛЯХ', 'title': 'Лучше один раз увидеть.', 'text': 'Соберите фотографии, которые расскажут вашу историю.', 'items': [{'title': 'Первый кадр', 'image': '', 'alt': ''}, {'title': 'Второй кадр', 'image': '', 'alt': ''}, {'title': 'Третий кадр', 'image': '', 'alt': ''}]}},
    'quotes': {'label': 'Отзывы', 'group': 'Доверие', 'caption': 'Слова тех, кто уже попробовал', 'fields': ['eyebrow', 'title'], 'items': True,
        'props': {'eyebrow': 'ОБРАТНАЯ СВЯЗЬ', 'title': 'Пусть за вас говорят люди.', 'items': [{'title': 'Имя и роль', 'text': 'Добавьте настоящий отзыв клиента с его разрешения.'}]}},
    'pricing': {'label': 'Тарифы', 'group': 'Продажи', 'caption': 'Предложение с понятной ценой', 'fields': ['eyebrow', 'title', 'text'], 'items': True,
        'props': {'eyebrow': 'ВАШ ВЫБОР', 'title': 'Подходящий формат для каждого.', 'text': 'Расскажите, что входит в ваше предложение.', 'items': [{'title': 'Старт', 'price': 'Бесплатно', 'text': 'Первое знакомство с продуктом', 'button': 'Узнать больше', 'link': 'mailto:hello@example.com'}, {'title': 'Больше возможностей', 'price': 'По запросу', 'text': 'Индивидуальное предложение для ваших задач', 'button': 'Обсудить', 'link': 'mailto:hello@example.com'}]}},
    'faq': {'label': 'Вопросы и ответы', 'group': 'Доверие', 'caption': 'Развейте сомнения посетителя', 'fields': ['eyebrow', 'title'], 'items': True,
        'props': {'eyebrow': 'ВСЁ ПОНЯТНО', 'title': 'Возможно, вы хотели спросить.', 'items': [{'title': 'С чего начать?', 'text': 'Расскажите посетителю о первом шаге.'}, {'title': 'Как с вами связаться?', 'text': 'Укажите удобный способ связи в блоке контактов.'}]}},
    'cta': {'label': 'Призыв к действию', 'group': 'Продажи', 'caption': 'Один следующий шаг для посетителя', 'fields': ['eyebrow', 'title', 'text', 'button', 'link'],
        'props': {'eyebrow': 'ДАВАЙТЕ НАЧНЁМ', 'title': 'Ваша следующая глава — здесь.', 'text': 'Пригласите посетителя попробовать продукт или связаться с вами.', 'button': 'Написать нам', 'link': 'mailto:hello@example.com'}},
    'footer': {'label': 'Подвал', 'group': 'Основное', 'caption': 'Контакты и полезные ссылки', 'fields': ['brand', 'text'], 'items': True,
        'props': {'brand': 'Новая история', 'text': 'Создаём то, чем хочется пользоваться.', 'items': [{'title': 'Почта', 'link': 'mailto:hello@example.com'}, {'title': 'Telegram', 'link': 'https://t.me/'}]}},
}
LABELS = {'eyebrow': 'Надзаголовок', 'title': 'Заголовок', 'text': 'Описание', 'button': 'Текст кнопки', 'link': 'Ссылка', 'brand': 'Название', 'image': 'Изображение', 'alt': 'Описание изображения', 'price': 'Цена'}
STYLE = {'surface': 'base', 'align': 'left', 'spacing': 'normal', 'variant': 'split'}
STYLE_VALUES = {'surface': {'base', 'white', 'accent', 'dark'}, 'align': {'left', 'center'}, 'spacing': {'compact', 'normal', 'airy'}, 'variant': {'split', 'center'}}


class DocumentError(ValueError):
    pass


def new_block(kind):
    return {'id': uuid.uuid4().hex, 'type': kind, 'props': copy.deepcopy(CATALOG[kind]['props']), 'style': dict(STYLE)}


def default_document(startup):
    blocks = [new_block(t) for t in ('nav', 'hero', 'features', 'text', 'cta', 'footer')]
    for block in blocks:
        if 'brand' in block['props']:
            block['props']['brand'] = startup.name[:100]
    blocks[1]['props']['title'] = f'{startup.name[:80]}.\nВаша идея обретает форму.'
    blocks[2]['id'] = 'features'
    blocks[3]['id'] = 'about'
    return {'schema': 1, 'theme': dict(THEME), 'pages': [{'id': uuid.uuid4().hex, 'title': 'Главная', 'slug': '', 'description': '', 'blocks': blocks}]}


def safe_link(value):
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 for c in value) or '\\' in value:
        raise DocumentError('Некорректная ссылка.')
    if not value or value.startswith('#') or (value.startswith('/') and not value.startswith('//')):
        return value
    try:
        url = urlsplit(value)
        if url.scheme == 'https' and url.hostname and not url.username and not url.password:
            url.port
            return value
        if url.scheme in {'mailto', 'tel'} and url.path and not url.netloc:
            return value
    except ValueError:
        pass
    raise DocumentError('Используйте ссылку https://, mailto:, tel:, /страницу/ или #якорь.')


def _text(value, limit=5000):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise DocumentError(f'Текст должен содержать не больше {limit} символов.')
    return value


def _props(raw, keys, assets):
    if not isinstance(raw, dict) or set(raw) - set(keys):
        raise DocumentError('Неизвестное поле блока.')
    result = {}
    for key in keys:
        value = raw.get(key, '')
        if key == 'image':
            if not isinstance(value, str) or (value and value not in assets):
                raise DocumentError('Изображение недоступно в этом проекте.')
            result[key] = value
        elif key == 'link':
            result[key] = safe_link(value)
        else:
            result[key] = _text(value, 5000 if key == 'text' else 300)
    return result


def validate_document(raw, asset_ids=()):
    if not isinstance(raw, dict) or raw.get('schema') != 1 or set(raw) - {'schema', 'theme', 'pages'}:
        raise DocumentError('Неподдерживаемый формат сайта.')
    if len(json.dumps(raw, ensure_ascii=False).encode()) > MAX_DOCUMENT_BYTES:
        raise DocumentError('Сайт слишком большой. Разделите содержимое на несколько проектов.')
    theme = raw.get('theme')
    if not isinstance(theme, dict) or set(theme) != set(THEME):
        raise DocumentError('Некорректная тема сайта.')
    if any(not isinstance(theme[k], str) or not COLOR.fullmatch(theme[k]) for k in ('accent', 'background', 'ink')) or not isinstance(theme['font'], str) or not isinstance(theme['radius'], str) or theme['font'] not in {'modern', 'editorial', 'classic'} or theme['radius'] not in {'sharp', 'soft', 'round'}:
        raise DocumentError('Проверьте цвета и шрифты темы.')
    pages = raw.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise DocumentError('На сайте может быть от 1 до 20 страниц.')
    assets, ids, slugs, total, clean = set(map(str, asset_ids)), set(), set(), 0, []
    def identifier(value):
        if not isinstance(value, str) or not ID.fullmatch(value) or value in ids:
            raise DocumentError('У страниц и блоков должны быть уникальные идентификаторы.')
        ids.add(value)
        return value
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != {'id', 'title', 'slug', 'description', 'blocks'}:
            raise DocumentError('Некорректная страница.')
        slug = page['slug']
        if not isinstance(slug, str) or (index == 0 and slug != '') or (index > 0 and (not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,59}', slug) or slug in {'assets', 'admin', 'api', 'index', 'favicon', 'robots'})) or slug in slugs:
            raise DocumentError('Адрес страницы: латинские буквы, цифры и дефис; адреса не должны повторяться. Главная страница — /.')
        slugs.add(slug)
        blocks = page['blocks']
        if not isinstance(blocks, list):
            raise DocumentError('Некорректный список блоков.')
        total += len(blocks)
        if total > MAX_BLOCKS:
            raise DocumentError('На сайте может быть не больше 100 блоков.')
        out = {'id': identifier(page['id']), 'title': _text(page['title'], 100), 'slug': slug, 'description': _text(page['description'], 300), 'blocks': []}
        if not out['title'].strip():
            raise DocumentError('Введите название страницы.')
        for block in blocks:
            if not isinstance(block, dict) or set(block) != {'id', 'type', 'props', 'style'} or not isinstance(block.get('type'), str) or block.get('type') not in CATALOG:
                raise DocumentError('Неизвестный тип блока.')
            definition = CATALOG[block['type']]
            rawprops = block['props']
            if not isinstance(rawprops, dict):
                raise DocumentError('Некорректное содержимое блока.')
            allowed = definition['fields'] + (['items'] if definition.get('items') else [])
            if set(rawprops) - set(allowed):
                raise DocumentError('Неизвестное поле блока.')
            props = _props({k: v for k, v in rawprops.items() if k != 'items'}, definition['fields'], assets)
            if definition.get('items'):
                items = rawprops.get('items', [])
                if not isinstance(items, list) or len(items) > 12:
                    raise DocumentError('В блоке может быть до 12 элементов.')
                itemkeys = list(definition['props']['items'][0])
                props['items'] = [_props(item, itemkeys, assets) for item in items]
            style = block['style']
            if not isinstance(style, dict) or set(style) != set(STYLE) or any(not isinstance(style[k], str) or style[k] not in choices for k, choices in STYLE_VALUES.items()):
                raise DocumentError('Некорректное оформление блока.')
            out['blocks'].append({'id': identifier(block['id']), 'type': block['type'], 'props': props, 'style': dict(style)})
        clean.append(out)
    return {'schema': 1, 'theme': dict(theme), 'pages': clean}


def asset_ids(document):
    ids = set()
    for page in document['pages']:
        for block in page['blocks']:
            if block['props'].get('image'):
                ids.add(block['props']['image'])
            ids.update(item['image'] for item in block['props'].get('items', []) if item.get('image'))
    return ids


def page_path(page):
    return '/' + page['slug'] + '/' if page['slug'] else '/'


def render_document(document, page_id=None, *, image_url=None, page_url=None, editor=False):
    page = next((p for p in document['pages'] if p['id'] == page_id), None) if page_id else document['pages'][0]
    if not page:
        raise DocumentError('Страница не найдена.')
    image_url = image_url or (lambda key: f'/assets/uploads/{key}.webp')
    page_url = page_url or page_path
    paths = {alias: page_url(p) for p in document['pages'] for alias in {page_path(p), page_path(p).rstrip('/') or '/'}}
    def href(value):
        base, sep, anchor = value.partition('#')
        value = paths.get(base, base) + (sep + anchor if sep else '')
        return escape(value or '#', quote=True)
    def txt(props, key, tag='p', cls='', **unused):
        value = props.get(key, '')
        if not value:
            return ''
        marker = f' data-bb-text="{key}"' if editor else ''
        return f'<{tag} class="{cls}"{marker}>{escape(value).replace(chr(10), "<br>")}</{tag}>'
    def button(props, cls=''):
        if not props.get('button'):
            return ''
        return f'<a class="bb-button {cls}" href="{href(props.get("link", ""))}">{escape(props["button"])} <span aria-hidden="true">↗</span></a>'
    def image(props, cls=''):
        if props.get('image'):
            return f'<img class="bb-photo {cls}" src="{escape(image_url(props["image"]), quote=True)}" alt="{escape(props.get("alt", ""), quote=True)}" loading="lazy">'
        return '<div class="bb-art" aria-hidden="true"><div class="bb-art-orbit"></div><div class="bb-art-disc"></div><div class="bb-art-tile">✳</div><span class="bb-art-note">Место для вашей идеи</span></div>'
    parts = []
    for number, block in enumerate(page['blocks']):
        props, kind, style = block['props'], block['type'], block['style']
        heading = txt(props, 'eyebrow', 'p', 'bb-eyebrow') + txt(props, 'title', 'h1' if kind == 'hero' else 'h2') + txt(props, 'text', 'p', 'bb-description')
        items = props.get('items', [])
        if kind == 'nav':
            body = f'<nav class="bb-nav">{txt(props, "brand", "span", "bb-brand")}<div class="bb-nav-links">' + ''.join(f'<a href="{href(i.get("link", ""))}">{escape(i["title"])}</a>' for i in items) + '</div>' + button(props, 'bb-small') + '</nav>'
        elif kind == 'hero':
            body = f'<div class="bb-hero"><div class="bb-hero-copy">{heading}{button(props)}<div class="bb-hero-rule"><span></span> Ваша идея. Ваши правила.</div></div><div class="bb-hero-visual">{image(props)}</div></div>'
        elif kind == 'text':
            body = f'<div class="bb-story"><span class="bb-section-no">{number:02d} / ИСТОРИЯ</span><div>{heading}</div></div>'
        elif kind in {'features', 'quotes', 'pricing', 'gallery'}:
            cards = []
            for n, item in enumerate(items):
                content = image(item) if kind == 'gallery' else '<div class="bb-quote-mark">“</div>' if kind == 'quotes' else f'<span class="bb-card-index">{n+1:02d}</span>'
                content += f'<h3>{escape(item.get("title", ""))}</h3>'
                if item.get('price'):
                    content += f'<p class="bb-price">{escape(item["price"])}</p>'
                if item.get('text'):
                    content += f'<p>{escape(item["text"]).replace(chr(10), "<br>")}</p>'
                content += button(item)
                cards.append(f'<article class="bb-card">{content}</article>')
            body = f'<header class="bb-section-heading">{heading}</header><div class="bb-grid bb-{kind}">' + ''.join(cards) + '</div>'
        elif kind == 'image':
            body = f'<figure class="bb-figure">{image(props)}<figcaption>{heading}</figcaption></figure>'
        elif kind == 'faq':
            body = '<div class="bb-faq-layout"><header>' + heading + '</header><div>' + ''.join(f'<details class="bb-faq"><summary>{escape(i["title"])}<span>+</span></summary><p>{escape(i["text"]).replace(chr(10), "<br>")}</p></details>' for i in items) + '</div></div>'
        elif kind == 'cta':
            body = f'<div class="bb-cta"><span class="bb-spark" aria-hidden="true">✳</span>{heading}{button(props)}</div>'
        else:
            body = '<footer class="bb-footer"><div>' + txt(props, 'brand', 'span', 'bb-brand') + txt(props, 'text', 'p') + '</div><div class="bb-footer-links">' + ''.join(f'<a href="{href(i.get("link", ""))}">{escape(i["title"])}</a>' for i in items) + '</div></footer>'
        attr = f' data-block-id="{block["id"]}" data-block-type="{kind}"' if editor else ''
        parts.append(f'<section id="{block["id"]}"{attr} class="bb-section bb-kind-{kind} bb-surface-{style["surface"]} bb-align-{style["align"]} bb-space-{style["spacing"]} bb-variant-{style["variant"]}"><div class="bb-wrap">{body}</div></section>')
    theme = document['theme']
    fonts = {'modern': 'Inter,system-ui,-apple-system,sans-serif', 'editorial': 'Georgia,serif', 'classic': 'Arial,sans-serif'}
    radius = {'sharp': '0px', 'soft': '18px', 'round': '32px'}[theme['radius']]
    variables = f':root{{--bb-accent:{theme["accent"]};--bb-bg:{theme["background"]};--bb-ink:{theme["ink"]};--bb-font:{fonts[theme["font"]]};--bb-radius:{radius}}}'
    return '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(page['title']) + '</title><meta name="description" content="' + escape(page['description'], quote=True) + '"><style>' + variables + SITE_CSS + '</style></head><body>' + ''.join(parts) + ('<div class="bb-empty">Добавьте первый блок — с него начнётся ваша страница.</div>' if not parts else '') + '</body></html>'


SITE_CSS = '''
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bb-bg);color:var(--bb-ink);font:16px/1.65 var(--bb-font);-webkit-font-smoothing:antialiased}h1,h2,h3,p,figure{margin:0}a{color:inherit;text-decoration:none}a:hover{text-decoration:underline;text-underline-offset:4px}button,a,summary{-webkit-tap-highlight-color:transparent}a:focus-visible,summary:focus-visible{outline:3px solid var(--bb-accent);outline-offset:5px}h1,h2,h3{font-weight:600;line-height:1.12;letter-spacing:-.045em;overflow-wrap:anywhere}h1{font-size:clamp(38px,5vw,72px)}h2{font-size:clamp(28px,3.5vw,46px)}h3{font-size:22px;letter-spacing:-.025em}p{overflow-wrap:anywhere}.bb-wrap{width:min(1120px,calc(100% - 80px));margin:auto}.bb-section{padding:84px 0;position:relative;scroll-margin-top:24px}.bb-kind-nav,.bb-kind-footer{padding:24px 0}.bb-eyebrow{font:600 11px/1.5 system-ui,sans-serif;letter-spacing:.16em;margin-bottom:22px;opacity:.65}.bb-description{font-size:17px;line-height:1.75;opacity:.7;margin-top:24px;max-width:650px}.bb-button{display:inline-flex;gap:32px;align-items:center;justify-content:space-between;background:var(--bb-accent);color:#fff;border-radius:calc(var(--bb-radius)*.55);padding:15px 23px;font:600 14px/1.5 system-ui,sans-serif;margin-top:32px;transition:transform .2s}.bb-button:hover{transform:translateY(-2px);text-decoration:none}.bb-button span{font-size:20px}.bb-small{margin:0;padding:10px 18px}.bb-brand{font-size:22px;font-weight:700;letter-spacing:-.04em}.bb-nav{display:flex;align-items:center;gap:32px;justify-content:space-between}.bb-nav-links,.bb-footer-links{display:flex;gap:28px;font-size:13px}.bb-hero{display:grid;grid-template-columns:1.15fr 1fr;align-items:center;gap:64px;min-height:450px}.bb-hero-copy .bb-description{max-width:470px}.bb-hero-visual{min-width:0}.bb-hero-visual .bb-photo{aspect-ratio:1;object-fit:cover;border-radius:50% 50% var(--bb-radius) var(--bb-radius)}.bb-hero-rule{display:flex;align-items:center;gap:12px;opacity:.5;font-size:11px;margin-top:38px}.bb-hero-rule span{height:1px;width:36px;background:currentColor}.bb-art{position:relative;min-height:280px;aspect-ratio:1;overflow:hidden;border-radius:var(--bb-radius);background:color-mix(in srgb,var(--bb-accent) 7%,var(--bb-bg))}.bb-art-orbit{position:absolute;inset:12%;border:1px solid color-mix(in srgb,var(--bb-accent) 25%,transparent);border-radius:50%}.bb-art-orbit:after{content:'';position:absolute;inset:12%;border:inherit;border-radius:50%}.bb-art-disc{position:absolute;width:54%;height:54%;left:10%;top:24%;background:var(--bb-accent);border-radius:50%;box-shadow:24px 28px 0 color-mix(in srgb,var(--bb-accent) 20%,transparent)}.bb-art-tile{position:absolute;top:16%;right:11%;width:45%;height:45%;display:grid;place-items:center;background:#ead9be;color:#5c4a33;font:100px/1 Georgia,serif;transform:rotate(12deg);border-radius:var(--bb-radius);box-shadow:0 20px 40px #243b3014}.bb-art-note{position:absolute;bottom:8%;right:10%;font:11px/1.5 system-ui,sans-serif;opacity:.55}.bb-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:20px}.bb-section-heading{margin-bottom:44px;max-width:800px}.bb-card{padding:30px;border:1px solid color-mix(in srgb,currentColor 13%,transparent);border-radius:var(--bb-radius);min-width:0}.bb-card p{margin-top:16px;font-size:14px;opacity:.7}.bb-card-index{display:block;font-size:12px;opacity:.45;margin-bottom:35px}.bb-card h3{font-size:23px}.bb-story{display:grid;grid-template-columns:1fr 2fr;gap:48px;border-top:1px solid color-mix(in srgb,currentColor 18%,transparent);padding-top:40px}.bb-section-no{font-size:11px;letter-spacing:.1em;opacity:.5}.bb-photo{width:100%;display:block;object-fit:cover}.bb-figure>.bb-photo{max-height:650px;border-radius:var(--bb-radius);aspect-ratio:16/9}.bb-figure>.bb-art{aspect-ratio:2.1}.bb-figure figcaption{margin-top:30px;max-width:680px}.bb-gallery .bb-card{padding:0;border:0}.bb-gallery .bb-photo,.bb-gallery .bb-art{aspect-ratio:1;min-height:0;border-radius:var(--bb-radius);margin-bottom:20px}.bb-gallery h3{font-size:17px}.bb-quote-mark{font:80px/.7 Georgia,serif;opacity:.3;margin-bottom:24px}.bb-quotes .bb-card p{font-size:18px;opacity:.85}.bb-quotes .bb-card h3{font-size:13px}.bb-pricing .bb-price{font-size:34px;opacity:1;letter-spacing:-.05em}.bb-pricing .bb-button{width:100%}.bb-faq-layout{display:grid;grid-template-columns:1fr 1.2fr;gap:70px}.bb-faq{border-bottom:1px solid color-mix(in srgb,currentColor 18%,transparent);padding:22px 0}.bb-faq summary{display:flex;justify-content:space-between;gap:20px;cursor:pointer;list-style:none;font-weight:500}.bb-faq summary::-webkit-details-marker{display:none}.bb-faq summary span{font-size:24px;line-height:1}.bb-faq[open] summary span{transform:rotate(45deg)}.bb-faq p{padding-top:20px;font-size:14px;opacity:.7}.bb-cta{max-width:720px;margin:auto;text-align:center}.bb-cta .bb-description{margin:24px auto 0}.bb-spark{display:inline-block;font-size:64px;line-height:1;color:var(--bb-accent);margin-bottom:30px}.bb-footer{border-top:1px solid color-mix(in srgb,currentColor 15%,transparent);padding:35px 0 55px;display:flex;justify-content:space-between;gap:32px}.bb-footer p{font-size:12px;opacity:.55;margin-top:12px}.bb-surface-white{background:#fff}.bb-surface-accent{background:var(--bb-accent);color:white}.bb-surface-accent .bb-button{background:#fff;color:var(--bb-accent)}.bb-surface-accent .bb-spark{color:inherit}.bb-surface-dark{background:var(--bb-ink);color:var(--bb-bg)}.bb-space-compact{padding-top:36px;padding-bottom:36px}.bb-space-airy{padding-top:120px;padding-bottom:120px}.bb-align-center .bb-section-heading,.bb-align-center .bb-description{margin-left:auto;margin-right:auto;text-align:center}.bb-align-center h1,.bb-align-center h2,.bb-align-center .bb-eyebrow{text-align:center}.bb-variant-center .bb-hero{display:block;text-align:center;max-width:850px;margin:auto}.bb-variant-center .bb-hero .bb-description{margin-left:auto;margin-right:auto;max-width:600px}.bb-variant-center .bb-hero-rule{justify-content:center}.bb-variant-center .bb-hero-visual{margin-top:50px}.bb-variant-center .bb-hero-visual .bb-art{aspect-ratio:2.2;min-height:220px}.bb-variant-center .bb-hero-visual .bb-photo{aspect-ratio:2;max-height:450px;border-radius:var(--bb-radius)}.bb-empty{padding:140px 25px;text-align:center;opacity:.5}
@media(max-width:650px){.bb-wrap{width:calc(100% - 40px)}.bb-section{padding:48px 0}.bb-kind-nav,.bb-kind-footer{padding:20px 0}.bb-hero{grid-template-columns:1fr;gap:36px}.bb-hero-visual .bb-art{max-width:420px;margin:auto}.bb-nav{gap:16px;flex-wrap:wrap}.bb-nav-links{order:3;flex-basis:100%;gap:20px}.bb-brand{font-size:20px}.bb-nav .bb-button{font-size:12px;padding:8px 12px;gap:12px}.bb-story,.bb-faq-layout{grid-template-columns:1fr;gap:28px}.bb-section-heading{margin-bottom:30px}.bb-grid{gap:16px}.bb-footer{flex-direction:column}.bb-footer-links{flex-wrap:wrap}.bb-space-airy{padding:70px 0}.bb-art-tile{font-size:90px}.bb-description{font-size:16px}}@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}}
'''

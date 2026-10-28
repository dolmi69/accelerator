"""Literal display-name edits. Never rewrite application code or invent a name."""
from html import escape, unescape
from html.parser import HTMLParser
import re

TITLE = r'(?:«[^«»\n;<>]{1,80}»|"[^"\n;<>]{1,80}"|[\w.-]{1,60})'
SUBJECT = r'(?:название|имя|заголовок)(?: (?:сайта|проекта|страницы|вкладки|сервиса|приложения))?'
VERB = r'(?:поменяй|поменять|поменяем|измени|изменить|изменим|замени|заменить|смени|сменить)'


def name_request(prompt):
    text = re.sub(r'\s+', ' ', prompt.strip()).strip(' .!?')
    text = re.sub(r'^(?:(?:бро|братан|пожалуйста|давай|ну|надо|нужно|а|теперь)[, ]+)+', '', text, flags=re.I)
    text = re.sub(r'[, ]+пожалуйста$', '', text, flags=re.I)
    destination = rf'(?:с (?P<old>{TITLE}) )?на (?P<new>{TITLE})'
    patterns = [rf'{VERB} {SUBJECT} {destination}(?: назад)?',
                rf'{SUBJECT} {destination} {VERB}(?: назад)?',
                rf'(?:переименуй|переименовать) (?:сайт|проект|сервис|приложение) в (?P<new>{TITLE})',
                rf'обратно на (?P<new>{TITLE})']
    for index, pattern in enumerate(patterns):
        match = re.fullmatch(pattern, text, re.I)
        if match:
            values = {key: value.strip('«»"') if value else None for key, value in match.groupdict().items()}
            return {**values, 'implicit': index == 3}
    return None


def _previous_rename(source):
    for _ in range(8):
        if not source:
            break
        parsed = name_request(source.prompt)
        if not parsed:
            return False
        if not parsed['implicit']:
            return True
        source = source.source if source.source_id else None
    return False


class NameEditor(HTMLParser):
    def __init__(self, html, old, new):
        super().__init__(convert_charrefs=False)
        self.html, self.old, self.new = html, old, new
        self.lines = [0, *[m.end() for m in re.finditer('\n', html)]]
        self.stack, self.edits = [], []
        self.pattern = re.compile(r'(?<!\w)' + re.escape(old) + r'(?!\w)', re.I)
        self.feed(html)

    def text_position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get('class', '') or ''
        brand = bool(re.search(r'(?:^|[\s_-])(?:brand|logo|site-name)(?:$|[\s_-])', classes))
        if tag not in {'meta', 'link', 'input', 'img', 'br', 'hr', 'source', 'area', 'base', 'embed', 'param', 'track', 'wbr'}:
            self.stack.append((tag, brand))
        raw = self.get_starttag_text()
        safe_new = escape(self.new, quote=True)
        def attribute(match):
            name, quote, value = match.groups()
            original = value
            is_meta = tag == 'meta' and ((values.get('name') or '').lower() == 'description' or (values.get('property') or '').lower() in {'og:title', 'og:site_name'})
            if (is_meta and name.lower() == 'content') or (brand and name.lower() in {'aria-label', 'title'}):
                value = self.pattern.sub(lambda _: safe_new, value, count=1)
            return name + '=' + quote + value + quote if value != original else match[0]
        updated = re.sub(r'([\w:-]+)\s*=\s*([\'"])(.*?)\2', attribute, raw, flags=re.S)
        if updated != raw:
            self.edits.append((self.text_position(), self.text_position() + len(raw), updated))

    def handle_startendtag(self, tag, attrs):
        depth = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[depth:]

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        tags = {item[0] for item in self.stack}
        if tags & {'script', 'style', 'textarea'}:
            return
        editable = 'title' in tags or any(brand for _, brand in self.stack) or text.strip().casefold() == self.old.casefold()
        if not editable:
            return
        updated = self.pattern.sub(lambda _: escape(self.new), text)
        if updated != text:
            self.edits.append((self.text_position(), self.text_position() + len(text), updated))

    def result(self):
        output = self.html
        for start, end, value in sorted(self.edits, reverse=True):
            output = output[:start] + value + output[end:]
        return output


def rename_site(source, prompt):
    """Return a precise local edit only when the brand is unambiguous."""
    parsed = name_request(prompt)
    if not parsed or source.kind not in {'static', 'generated'}:
        return None
    if parsed['implicit'] and not _previous_rename(source):
        return None
    title = re.search(r'<title\b[^>]*>([^<]+)</title\s*>', source.html, re.I)
    if not title:
        return None
    current = re.split(r'\s+[—–|/-]\s+', unescape(title[1]).strip(), maxsplit=1)[0].strip()
    old, new = parsed.get('old') or current, parsed['new']
    if not old or len(old) > 80 or '&' in old or old.casefold() != current.casefold():
        return None
    editor = NameEditor(source.html, old, new)
    html = editor.result()
    if old != new and html == source.html:
        return None
    files = {**source.backend_files, 'index.html': html} if source.kind == 'generated' else {}
    if files:
        from founder.services.generated_backend import validate_files
        validate_files(files)
    return {'old':old, 'new':new, 'html':html, 'files':files}

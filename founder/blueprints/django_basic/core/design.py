"""Safe design tokens and a navigation slot inside the isolated generated page."""
from html.parser import HTMLParser
import re

COLOR = r'(?:#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\([\d.,%\s+-]+\)|white|black|ivory|beige|navy)'


def theme_from_html(html):
    styles = '\n'.join(re.findall(r'<style\b[^>]*>(.*?)</style\s*>', html, re.I | re.S))
    variables = dict(re.findall(r'(--[\w-]+)\s*:\s*([^;{}]+)', styles))

    def resolve(value, depth=0):
        if depth > 5:
            return ''
        return re.sub(r'var\((--[\w-]+)(?:\s*,\s*([^()]+))?\)',
            lambda m: resolve(variables.get(m[1], m[2] or ''), depth + 1), value).strip()

    def rule(selector, prop):
        values = []
        for selectors, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', styles):
            if any(s.strip() == selector for s in selectors.split(',')):
                values.extend(re.findall(r'(?:^|;)\s*' + re.escape(prop) + r'\s*:\s*([^;]+)', declarations))
        return resolve(values[-1]).replace('!important', '').strip() if values else ''

    def color(keys, fallback, direct=''):
        for value in [*[resolve(variables.get(key, '')) for key in keys], direct]:
            value = value.replace('!important', '').strip()
            if re.fullmatch(COLOR, value, re.I):
                return value
        return fallback

    background = rule('body', 'background') or resolve(variables.get('--bg-gradient', ''))
    if not (re.fullmatch(COLOR, background, re.I) or
            (len(background) < 220 and re.fullmatch(r'(?:linear|radial)-gradient\([#\w.,()%\s+-]+\)', background, re.I)
             and re.search(COLOR, background, re.I))):
        background = color(['--bg', '--background', '--background-color'], '#f5f6fa', rule('body', 'background-color'))
    font = rule('body', 'font-family')
    if not re.fullmatch(r'[\w\s,\'"-]{1,160}', font):
        font = 'system-ui,-apple-system,sans-serif'
    radius = rule('button', 'border-radius') or rule('.btn', 'border-radius')
    if not re.fullmatch(r'(?:[0-2]?\d|3[0-6])px', radius):
        radius = '12px'
    return {
        'accent': color(['--primary', '--primary-color', '--accent', '--brand', '--color-primary'], '#16867d', rule('button', 'background-color') or rule('button', 'background')),
        'background': background,
        'surface': color(['--surface', '--card-bg', '--paper', '--surface-color'], '#ffffff', rule('.container', 'background')),
        'ink': color(['--text-main', '--text', '--ink', '--text-color'], '#182d34', rule('body', 'color')),
        'muted': color(['--text-secondary', '--muted'], '#667b82'),
        'font': font, 'radius': radius,
    }


def theme_styles(theme):
    return ':root{' + ';'.join('--app-' + key + ':' + value for key, value in theme.items()) + '}\n'


class Slots(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.lines = [0, *[m.end() for m in re.finditer('\n', html)]]
        self.slot = self.header = self.container = self.body = self.head_end = self.head_start = None
        self.inside_body = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        line, column = self.getpos()
        offset = self.lines[line - 1] + column + len(self.get_starttag_text())
        attrs = dict(attrs)
        if tag == 'head':
            self.head_start = offset
        if tag == 'body':
            self.body = offset
            self.inside_body = True
        elif self.inside_body:
            if 'data-app-navigation' in attrs and self.slot is None:
                self.slot = offset
            if tag == 'header' and self.header is None:
                self.header = offset
            if self.container is None and (tag == 'main' or
                    tag == 'div' and set(attrs.get('class', '').split()) & {'container', 'app', 'page', 'wrapper'}):
                self.container = offset

    def handle_endtag(self, tag):
        if tag == 'head':
            line, column = self.getpos()
            self.head_end = self.lines[line - 1] + column
        if tag == 'body':
            self.inside_body = False


def integrate_navigation(html, navigation, css, script):
    slots = Slots(html)
    offset = slots.slot or slots.header or slots.container or slots.body
    if offset is None:
        return html
    html = html[:offset] + navigation + html[offset:]
    # Only server-produced fragments are inserted. The whole result remains sandboxed.
    head = '<style data-app-theme>' + css + '</style>'
    if slots.head_end is not None:
        html = html[:slots.head_end] + head + html[slots.head_end:]
    if slots.head_start is not None:
        bridge = '<script data-app-bridge>' + script + '</script>'
        html = html[:slots.head_start] + bridge + html[slots.head_start:]
    return html

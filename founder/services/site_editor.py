"""Free presentation controls from fixed CSS values, independent of any LLM."""
from html import escape
from html.parser import HTMLParser
import re

PALETTES = {
    "blue": ("#2563eb", "#eff6ff", "#172554"),
    "green": ("#15803d", "#f0fdf4", "#14532d"),
    "purple": ("#7c3aed", "#f5f3ff", "#2e1065"),
    "dark": ("#60a5fa", "#111827", "#f9fafb"),
}
FONTS = {"system": "system-ui,sans-serif", "serif": "Georgia,serif", "mono": "ui-monospace,monospace"}


def theme_css(presentation):
    # Revalidate persisted JSON too. Never interpolate arbitrary CSS.
    values = presentation if isinstance(presentation, dict) else {}
    css = []
    if values.get("palette") in PALETTES:
        accent, background, text = PALETTES[values["palette"]]
        css.extend([
            f":root{{--primary:{accent};--primary-color:{accent};--accent:{accent};--brand:{accent};--bg:{background};--text:{text};--text-main:{text};--surface:{'#1f2937' if values['palette'] == 'dark' else '#fff'};--text-secondary:{'#cbd5e1' if values['palette'] == 'dark' else '#475569'};--forge-on-accent:{'#111827' if values['palette'] == 'dark' else '#fff'}}}",
            f"body{{background:{background}!important;color:{text}!important}}",
            f"button,input[type=submit],.button,.btn{{background:{accent}!important;color:#fff!important}}",
            f"a{{color:{accent}}}",
        ])
    if values.get("font") in FONTS:
        css.append(f"body,button,input,textarea,select,h1,h2,h3,h4,h5,h6,p,a,span,label,li{{font-family:{FONTS[values['font']]}!important}}")
    if values.get("radius") in {"0", "8", "24"}:
        css.append(f"button,input[type=submit],.button,.btn,.forge-button{{border-radius:{values['radius']}px!important}}")
    return "\n".join(css)


def customize(html, presentation):
    # Only remove the exact marker created by this editor; all other code stays intact.
    html = re.sub(r'<style id="lab-local-theme">.*?</style>', "", html, flags=re.S)
    title = presentation.get("title", "")
    if title:
        safe_title = escape(title)
        html = re.sub(r"(<title\b[^>]*>).*?(</title\s*>)", lambda m: m[1]+safe_title+m[2], html, count=1, flags=re.I|re.S)
        # A heading can contain markup; text-only headings are the safe free control.
        html = re.sub(r"(<h1\b[^>]*>)[^<]*(</h1\s*>)", lambda m: m[1]+safe_title+m[2], html, count=1, flags=re.I)
    css = theme_css(presentation)
    if css:
        html = re.sub(r"</head\s*>", lambda _: '<style id="lab-local-theme">'+css+'</style></head>', html, count=1, flags=re.I)
    return replace_visible_text(html, presentation.get("text_replacements", {}))


def simple_command(prompt):
    # Strict complete commands only. Mixed instructions must not be silently dropped.
    font = re.fullmatch(r'(?:поменяй|измени|смени|сделай)\s+шрифт(?:\s+(?:сайта|на|на сайте))*\s+(современн\w+|классическ\w+|моноширинн\w+)[.!]?', prompt.strip().lower())
    if font:
        value = font[1]
        return {'font': 'system' if value.startswith('современн') else 'serif' if value.startswith('классическ') else 'mono'}
    match = re.fullmatch(r"(?:поменяй|измени|смени|сделай) (?:цветовую гамму|цветовую схему|тему)(?: сайта)? на (синюю|зел[её]ную|фиолетовую|т[её]мную)[.!]?", prompt.strip().lower())
    if not match:
        return None
    return {"palette": {"синюю": "blue", "зеленую": "green", "зелёную": "green", "фиолетовую": "purple", "темную": "dark", "тёмную": "dark"}[match[1]]}


# Inflected names in natural commands refer to the visible template label.
LABEL_ALIASES = {
    "регистрацию": "Регистрация", "регристрацию": "Регистрация",
    "регистрации": "Регистрация", "регистрация": "Регистрация",
}


def text_command(prompt):
    """Only complete literal rename requests; mixed tasks go to the AI editor."""
    match = re.fullmatch(
        r"(?:замени|заменить|поменяй|переименуй)\s+(?:надпись\s+|текст\s+|слово\s+)?(.+?)\s+на\s+(.+?)\s*[.!]?",
        prompt.strip(), re.I)
    if not match:
        return None
    values = []
    for value in match.groups():
        value = value.strip().strip('"\'«»“”')
        if (not value or len(value) > 120 or re.search(r'[\n\r;<>]|\b(?:и|затем)\s+(?:добавь|сделай|убери|замени|измени)', value, re.I)):
            return None
        values.append(value)
    old, new = values
    old = LABEL_ALIASES.get(old.casefold(), old)
    return (old, new) if old != new else None


class VisibleTextEditor(HTMLParser):
    """Edit literal text, leaving URLs, scripts and Django expressions intact."""
    def __init__(self, html, replacements):
        super().__init__(convert_charrefs=False)
        self.html, self.replacements = html, replacements
        self.lines = [0, *[m.end() for m in re.finditer("\n", html)]]
        self.raw_tag = None
        self.edits = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "textarea"}:
            self.raw_tag = tag

    def handle_endtag(self, tag):
        if tag == self.raw_tag:
            self.raw_tag = None

    def handle_data(self, text):
        if self.raw_tag:
            return
        # Template syntax is code, never a rename target.
        pieces = re.split(r"({{.*?}}|{%.*?%}|{#.*?#})", text, flags=re.S)
        for i in range(0, len(pieces), 2):
            for old, new in self.replacements.items():
                safe = escape(new).replace("{", "&#123;").replace("}", "&#125;")
                pieces[i] = re.sub(re.escape(old), lambda _: safe, pieces[i], flags=re.I)
        updated = ''.join(pieces)
        if updated != text:
            line, column = self.getpos()
            start = self.lines[line - 1] + column
            self.edits.append((start, start + len(text), updated))


def replace_visible_text(html, replacements):
    if not isinstance(replacements, dict):
        return html
    valid = {old: new for old, new in list(replacements.items())[:10]
             if isinstance(old, str) and isinstance(new, str) and 0 < len(old) <= 120 and 0 < len(new) <= 120}
    editor = VisibleTextEditor(html, valid)
    for start, end, replacement in reversed(editor.edits):
        html = html[:start] + replacement + html[end:]
    return html

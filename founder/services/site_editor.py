"""Free presentation controls from fixed CSS values, independent of any LLM."""
from html import escape
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
            f":root{{--primary:{accent};--accent:{accent};--brand:{accent};--bg:{background};--text:{text}}}",
            f"body{{background:{background}!important;color:{text}!important}}",
            f"button,input[type=submit],.button,.btn{{background:{accent}!important;color:#fff!important}}",
            f"a{{color:{accent}}}",
        ])
    if values.get("font") in FONTS:
        css.append(f"body,button,input,textarea,select{{font-family:{FONTS[values['font']]}!important}}")
    if values.get("radius") in {"0", "8", "24"}:
        css.append(f"button,input[type=submit],.button,.btn{{border-radius:{values['radius']}px!important}}")
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
    return html


def simple_command(prompt):
    # Strict complete commands only. Mixed instructions must not be silently dropped.
    match = re.fullmatch(r"(?:поменяй|измени|смени|сделай) (?:цветовую гамму|цветовую схему|тему)(?: сайта)? на (синюю|зел[её]ную|фиолетовую|т[её]мную)[.!]?", prompt.strip().lower())
    if not match:
        return None
    return {"palette": {"синюю": "blue", "зеленую": "green", "зелёную": "green", "фиолетовую": "purple", "темную": "dark", "тёмную": "dark"}[match[1]]}

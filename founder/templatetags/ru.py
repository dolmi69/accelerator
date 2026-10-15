"""Русское склонение в шаблонах: «1 версия», «3 версии», «5 версий» вместо «1 версий».

Подключён в settings как builtin, поэтому {% load %} в шаблонах не нужен.
"""

from django import template

register = template.Library()


@register.filter
def plural(number, forms):
    """{{ n|plural:"шаг,шага,шагов" }} → «1 шаг», «3 шага», «5 шагов»."""
    try:
        value = int(number)
    except (TypeError, ValueError):
        return number
    one, few, many = (forms.split(",") + ["", "", ""])[:3]
    if value % 10 == 1 and value % 100 != 11:
        word = one
    elif value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        word = few
    else:
        word = many
    return f"{value} {word}"

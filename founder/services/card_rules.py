"""Regular expressions that decide whether a published project card breaks community rules.

Each rule has a short code (stored with the block and the complaint) and a category shown to the author.
Patterns are matched against normalised text: lower case, 'ё' written as 'е'.
A match must not touch another letter or digit, so neutral words that merely contain a stem pass.
Draft list: it needs agreement with the team before it is treated as final.
"""
import re
from dataclasses import dataclass

MAT = 'Нецензурная лексика'
NAZI = 'Нацистский лозунг'

# Word boundary: no Unicode letter or digit directly before or after the match.
_BOUNDARY = r'(?<![^\W_])(?:{})(?![^\W_])'


@dataclass(frozen=True)
class Rule:
    code: str
    category: str
    regex: re.Pattern


def _rule(code, category, pattern):
    return Rule(code, category, re.compile(_BOUNDARY.format(pattern), re.IGNORECASE))


RULES = (
    _rule('MAT-01', MAT, r'хуй(?:ня|ни|ню|ней|ло|ла|ле|ли|е|я|ю|ем)?'),
    _rule('MAT-02', MAT, r'пизд(?:а|ы|е|у|ой|ец|ет|ят)'),
    _rule('MAT-03', MAT, r'еб(?:ать|ал|ала|ало|али|ет|ут|ан(?:ый|ая|ое|ые|ут|ого|ой))'),
    _rule('MAT-04', MAT, r'блядь|блять|бляди|блядский|блядство|бля'),
    _rule('MAT-05', MAT, r'сука|суки|сукин(?:а|ы|у|ой|сын)?'),
    _rule('MAT-06', MAT, r'мудак(?:и|а|ов|ам)?|мудил[ао]'),
    _rule('MAT-07', MAT, r'долбоеб(?:ы|ов|ам|ом)?'),
    _rule('NAZI-01', NAZI, r'(?:зиг\s+)?хайль(?:\s+гитлер\w*)?|sieg\s+heil|heil\s+hitler'),
    _rule('NAZI-02', NAZI, r'смерть\s+евреям'),
    _rule('NAZI-03', NAZI, r'кровь\s+и\s+почва|blut\s+und\s+boden'),
    _rule('NAZI-04', NAZI, r'white\s+power'),
)


def normalise(text):
    return text.replace('ё', 'е').replace('Ё', 'Е')


def first_violation(text):
    """Returns the first rule whose pattern matches the text, or None."""
    if not text:
        return None
    value = normalise(text)
    for rule in RULES:
        if rule.regex.search(value):
            return rule
    return None

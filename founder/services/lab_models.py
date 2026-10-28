"""Request-scoped Sol/Luna routing; no settings mutation and no paid fallback."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

from django.conf import settings

SOL = 'openai/gpt-6.1-sol'
LUNA = 'openai/gpt-6-luna'
LABELS = {SOL: 'GPT‑6.1 Sol', LUNA: 'GPT‑6 Luna'}
POLICY = 'sol-luna-v2'
_model = ContextVar('lab_code_model', default=None)


def enabled():
    return settings.LAB_CODE_PROVIDER == 'ofox' and settings.LAB_MODEL_ROUTING


def override_model():
    return _model.get()


def active_model():
    return override_model() or (settings.OFOX_CODE_MODEL if settings.LAB_CODE_PROVIDER == 'ofox'
                               else settings.QWEN_CODE_MODEL)


@contextmanager
def model_scope(model):
    if model is not None and model not in LABELS:
        raise ValueError('Unsupported lab routing model')
    token = _model.set(model if enabled() else None)
    try:
        yield
    finally:
        _model.reset(token)


def prices(model):
    prefix = 'LAB_SOL' if model == SOL else 'LAB_LUNA' if model == LUNA else None
    if prefix is None:
        raise ValueError('Unsupported lab routing model')
    return (getattr(settings, prefix + '_INPUT_USD_PER_MILLION'),
            getattr(settings, prefix + '_OUTPUT_USD_PER_MILLION'))


def _route(model, reason, phase='code'):
    return {'model': model, 'label': LABELS[model], 'reason': reason, 'phase': phase, 'policy': POLICY}


def planning_route(source):
    if not enabled():
        return None
    from founder.services.lab_design import design_pending
    first = source is None or design_pending(source)
    return _route(SOL if first else LUNA,
                  'Первое создание сайта' if first else 'Разбор запроса', 'analysis')


def is_display_rename(prompt):
    """Narrow, whole-message match: an obvious rename cannot be escalated by
    truncated source/history. Mixed requests still require intent analysis.
    """
    text = re.sub(r'\s+', ' ', prompt.strip().casefold()).strip(' .!?')
    text = re.sub(r'^(?:(?:бро|братан|пожалуйста|давай|ну|надо|нужно)[, ]+)+', '', text)
    text = re.sub(r'[, ]+пожалуйста$', '', text)
    title = r'(?:«[^«»\n;]{1,80}»|"[^"\n;]{1,80}"|[\w.-]{1,60})'
    subject = r'(?:название|имя|заголовок)(?: (?:сайта|проекта|страницы|вкладки|сервиса|приложения))?'
    verb = r'(?:поменяй|поменять|поменяем|измени|изменить|изменим|замени|заменить|смени|сменить)'
    destination = rf'(?:с {title} )?на {title}'
    return any(re.fullmatch(pattern, text) for pattern in (
        rf'{verb} {subject} {destination}(?: назад)?',
        rf'{subject} {destination} {verb}(?: назад)?',
        rf'(?:переименуй|переименовать) (?:сайт|проект|сервис|приложение) в {title}',
    ))


def generation_route(source, decision=None, *, kind=None, rebuild=False, prompt=''):
    if not enabled():
        return None
    from founder.services.lab_design import design_pending
    if source is None or design_pending(source):
        return _route(SOL, 'Первое создание сайта')
    if rebuild:
        return _route(SOL, 'Полная пересборка сайта')
    if is_display_rename(prompt):
        return _route(LUNA, 'Смена названия')
    if decision and decision.get('complexity') == 'small':
        return _route(LUNA, 'Небольшая правка')
    explanation = (decision or {}).get('complexity_reason', '')
    if (decision and decision.get('complexity') == 'complex' and
            isinstance(explanation, str) and explanation.strip()):
        return _route(SOL, 'Объёмная доработка: ' + explanation.strip()[:300])
    # Ambiguity, missing classifications, source length and the presence of a
    # backend are not grounds for spending on Sol.
    return _route(LUNA, 'Обычная доработка')

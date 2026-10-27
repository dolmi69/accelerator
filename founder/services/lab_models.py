"""Request-scoped Sol/Luna routing; no settings mutation and no paid fallback."""
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings

SOL = 'openai/gpt-6.1-sol'
LUNA = 'openai/gpt-6-luna'
LABELS = {SOL: 'GPT‑6.1 Sol', LUNA: 'GPT‑6 Luna'}
POLICY = 'sol-luna-v1'
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


def generation_route(source, decision=None, *, kind=None, rebuild=False):
    if not enabled():
        return None
    from founder.services.lab_design import design_pending
    if source is None or design_pending(source):
        return _route(SOL, 'Первое создание сайта')
    if rebuild or (kind is not None and source.kind != kind):
        return _route(SOL, 'Пересборка или изменение типа сайта')
    if decision and decision.get('complexity') == 'small':
        return _route(LUNA, 'Небольшая правка')
    return _route(SOL, 'Сложная или неоднозначная задача')

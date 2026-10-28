"""Bounded recovery of completed invalid model outputs, with honest usage totals."""
from dataclasses import replace
from contextlib import contextmanager
from contextvars import ContextVar
from django.conf import settings

_validator = ContextVar('lab_result_validator', default=None)


@contextmanager
def validation_scope(validator):
    token = _validator.set(validator)
    try:
        yield
    finally:
        _validator.reset(token)


def validate_candidate(result):
    validator = _validator.get()
    if validator:
        validator(result)


def attempt_limit():
    return max(1, min(3, int(getattr(settings, 'LAB_EDIT_REPAIR_ATTEMPTS', 3))))


def aggregate(result, trace, attempts):
    def total(field):
        values = [getattr(item,field) for item in trace]
        return sum(values) if all(value is not None for value in values) else None
    ids = tuple(dict.fromkeys(identifier for item in trace for identifier in
        (item.request_ids or ((item.request_id,) if item.request_id else ()))))
    report = dict(result.report)
    if attempts > 1 or len(trace) > 1:
        report['recovery'] = {'code_attempts':attempts,'model_calls':len(trace)}
    if (attempts > 1 or len(trace) > attempts + 1) and isinstance(report.get('completed'), list):
        report['completed'] = [*report['completed'][:19], 'Правка автоматически исправлена и прошла локальные проверки.']
    return replace(result, input_tokens=total('input_tokens'), output_tokens=total('output_tokens'),
                   request_ids=ids, report=report)


def exhausted(error, attempts):
    return (f'Не удалось безопасно применить изменение после {attempts} попыток исправления. '
            f'Причина: {str(error)[:700]} Рабочая версия сохранена. '
            'Уточните нужный элемент и ожидаемый результат — например, название кнопки или действие, которое должно работать.')

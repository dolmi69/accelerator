"""One AI response for a project-scoped batch; atomic, validated proposal storage."""
import json
from hashlib import sha256
from uuid import UUID

from founder.services.kano_availability import visible_tasks
from django.conf import settings
from django.db import transaction

from founder.models import FeatureCandidate, StartupProfile, TaskEstimate
from founder.services.ai import AIResponseFormatError, AIServiceError, complete_text, provider_label
from founder.services.model_json import load_model_json
from founder.services.priority_estimates import (ESTIMATE_PROMPT, ESTIMATE_SCHEMA, NUMERIC_FIELDS,
                                                parse_estimate, proposal, resolve_estimate, subject_digest)

MAX_TARGETS = 40
EXISTING_SCHEMA = {
    'type': 'array', 'maxItems': MAX_TARGETS, 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {'kind': {'type': 'string', 'enum': ['task', 'feature']},
                       'id': {'type': 'string'}, 'estimate': ESTIMATE_SCHEMA},
        'required': ['kind', 'id', 'estimate'],
    },
}


def _revision(subject, saved):
    data = {'subject': subject_digest(subject), 'manual': {key: getattr(saved, key, None) for key in
            (*NUMERIC_FIELDS, 'estimate_basis', 'priority')}, 'proposal': saved.bruno_estimate}
    return sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def collect_targets(startup, *, refresh=False):
    from founder.services.work_priorities import linked_features

    features = list(startup.feature_candidates.select_related('task'))
    links = linked_features(startup, features)
    candidates = [('feature', f, f) for f in features if not f.task_id or f.task.status == 'todo']
    for task in visible_tasks(startup.bruno_tasks).filter(status='todo').select_related('estimate'):
        if str(task.pk) not in links:
            candidates.append(('task', task, getattr(task, 'estimate', None) or TaskEstimate(task=task)))
    targets = []
    for kind, subject, saved in candidates:
        if all(getattr(saved, key) is not None for key in NUMERIC_FIELDS):
            continue
        preview = resolve_estimate(saved)
        if not refresh and not preview.needs_bruno:
            continue
        targets.append({'kind': kind, 'id': str(subject.pk), 'subject': subject, 'saved': saved,
                        'revision': _revision(subject, saved), 'needs_bruno': preview.needs_bruno})
    targets.sort(key=lambda t: (not t['needs_bruno'], t['kind'], t['id']))
    return targets[:MAX_TARGETS]


def target_context(targets):
    return [{'kind': t['kind'], 'id': t['id'], 'title': t['subject'].title,
             'description': t['subject'].description if t['kind'] == 'feature' else t['subject'].instructions,
             'done_when': '' if t['kind'] == 'feature' else t['subject'].success_criterion,
             'axis': 'product' if t['kind'] == 'feature' else t['subject'].axis,
             'origin': 'mvp' if t['kind'] == 'feature' else t['subject'].origin,
             'founder_inputs': {key: getattr(t['saved'], key) for key in
                               (*NUMERIC_FIELDS, 'estimate_basis', 'priority')}} for t in targets]


def batch_request(targets):
    """Required request-local slots avoid omissions and model-echoed UUID errors."""
    slots = {f'item_{index}': target for index, target in enumerate(targets, 1)}
    context = {}
    for ref, item in zip(slots, target_context(targets)):
        context[ref] = {key: value for key, value in item.items() if key not in ('kind', 'id')}
    schema = {
        'type': 'object', 'additionalProperties': False, 'required': ['estimates'],
        'properties': {'estimates': {
            'type': 'object', 'additionalProperties': False, 'required': list(slots),
            'properties': {ref: ESTIMATE_SCHEMA for ref in slots},
        }},
    }
    return slots, context, schema


def parse_batch(data, slots):
    try:
        if not isinstance(data, dict) or set(data) != {'estimates'}:
            raise ValueError
        estimates = data['estimates']
        if not isinstance(estimates, dict) or set(estimates) != set(slots):
            raise ValueError
    except (ValueError, TypeError, KeyError) as exc:
        raise AIResponseFormatError(
            'Бруно вернул неполный список оценок. Предыдущие оценки сохранены; повторите оценку.'
        ) from exc
    return {(target['kind'], target['id']): parse_estimate(estimates[ref])
            for ref, target in slots.items()}


def parse_existing(items, targets):
    expected = {(t['kind'], t['id']) for t in targets}
    try:
        if not isinstance(items, list) or len(items) != len(expected):
            raise ValueError
        parsed = {}
        for item in items:
            if not isinstance(item, dict) or set(item) != {'kind', 'id', 'estimate'}:
                raise ValueError
            key = (item['kind'], str(UUID(item['id'])))
            if key not in expected or key in parsed:
                raise ValueError
            parsed[key] = parse_estimate(item['estimate'])
        if set(parsed) != expected:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise AIResponseFormatError('Бруно не оценил весь запрошенный список. Предыдущие оценки сохранены.') from exc


def save_subject_estimate(subject, values, model):
    if isinstance(subject, FeatureCandidate):
        saved = subject
    else:
        saved, _ = TaskEstimate.objects.get_or_create(task=subject)
    saved.bruno_estimate = proposal(values, model, subject)
    saved.save(update_fields=['bruno_estimate', 'updated_at'])


@transaction.atomic
def persist_targets(startup, targets, parsed, model):
    StartupProfile.objects.select_for_update().get(pk=startup.pk)
    fresh = []
    for target in targets:
        if target['kind'] == 'feature':
            subject = startup.feature_candidates.select_for_update().filter(pk=target['id']).first()
            if subject is None or subject.task_id and subject.task.status != 'todo':
                raise AIServiceError('Список задач изменился во время оценки. Запустите оценку ещё раз.')
            saved = subject
        else:
            subject = visible_tasks(startup.bruno_tasks).select_for_update().filter(pk=target['id'], status='todo').first()
            if subject is None:
                raise AIServiceError('Задание уже завершено или удалено. Запустите оценку ещё раз.')
            saved = TaskEstimate.objects.select_for_update().filter(task=subject).first() or TaskEstimate(task=subject)
        if _revision(subject, saved) != target['revision']:
            raise AIServiceError('Оценки или описание изменились в другой вкладке. Ваши правки сохранены; повторите оценку.')
        fresh.append((subject, parsed[(target['kind'], target['id'])]))
    for subject, values in fresh:
        save_subject_estimate(subject, values, model)
    return len(fresh)


def generate_estimates(startup, *, refresh=False):
    if settings.AI_PROVIDER == 'demo':
        raise AIServiceError('Для оценок Бруно подключите AI-провайдера. Свои оценки можно ввести вручную.')
    targets = collect_targets(startup, refresh=refresh)
    if not targets:
        return 0
    from founder.services.review import _review_context

    slots, context, schema = batch_request(targets)
    content = _review_context(startup, extract_economics=False) + (
        '\n\nОцени только пункты следующего объекта. Ключи item_N — точные ссылки на пункты '
        'этого запроса; задания из остального контекста не добавляй.\n'
        + json.dumps(context, ensure_ascii=False, default=str))
    raw = complete_text(
        'Ты Бруно. Оцени существующие задания без создания новых. ' + ESTIMATE_PROMPT
        + 'Верни JSON-объект estimates: для каждого переданного ключа item_N верни объект '
          'из шести исходных чисел и estimate_basis. Сохрани все ключи item_N без переименования. '
          'Не возвращай массив, kind, id или вложенный estimate. '
          'Оцени каждый пункт, даже если данных мало: обозначь предположения в estimate_basis. '
          'Обязательные ключи estimates: ' + ', '.join(slots) + '.', content,
        json_schema=schema,
        max_tokens=400 + len(targets) * 500,
    )
    try:
        data = load_model_json(raw)
        parsed = parse_batch(data, slots)
    except (ValueError, TypeError, KeyError) as exc:
        raise AIResponseFormatError('Бруно не завершил оценки. Предыдущие значения сохранены.') from exc
    return persist_targets(startup, targets, parsed, provider_label()[1])

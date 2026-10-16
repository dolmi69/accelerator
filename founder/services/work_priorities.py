"""One queue for tasks and unstarted features, with shared deterministic formulas."""
from urllib.parse import urlencode

from founder.services.kano_availability import enabled as kano_enabled
from django.urls import reverse

from founder.models import BrunoTask, TaskEstimate
from founder.services import mvp
from founder.services.priority_estimates import resolve_estimate

ESTIMATE_FIELDS = ('reach', 'rice_impact', 'confidence', 'effort', 'ice_impact', 'ease', 'estimate_basis', 'priority')


def linked_features(startup, features):
    links = {str(f.task_id): f for f in features if f.task_id}
    by_id = {str(f.pk): f for f in features}
    for review in startup.reviews.all()[:20]:
        steps = review.data.get('steps', [])
        for index, task_id in review.data.get('_accepted_steps', {}).items():
            if str(index).isdigit() and int(index) < len(steps):
                feature = by_id.get(steps[int(index)].get('feature_id'))
                if feature:
                    links.setdefault(str(task_id), feature)
    return links


def can_use_lab(task, feature=None):
    return task.status == 'todo' and task.origin != 'kano' and (
        task.origin == 'mvp' or (feature is not None and task.axis == 'product'))


def task_rows(startup, tasks, features, method='rice'):
    links = linked_features(startup, features)
    result = []
    for task in tasks:
        if not kano_enabled() and task.origin == 'kano':
            continue
        feature = links.get(str(task.pk))
        estimate = feature or getattr(task, 'estimate', None) or TaskEstimate(task=task)
        task.ranked_feature = feature
        task.estimate_preview = resolve_estimate(estimate)
        task.rice = task.estimate_preview.rice
        task.ice = task.estimate_preview.ice
        task.score = task.ice if method == 'ice' else task.rice
        task.estimate_source = task.estimate_preview.estimate
        task.score_preliminary = (task.estimate_preview.ice_preliminary if method == 'ice'
                                  else task.estimate_preview.rice_preliminary)
        task.lab_ready = can_use_lab(task, feature)
        task.lab_url = reverse('lab', args=[startup.pk]) + '?' + urlencode({'task': task.pk, 'method': method}) + '#lab-task-context' if task.lab_ready else ''
        task.estimate_url = (reverse('feature_edit', args=[startup.pk, feature.pk]) if feature
                             else reverse('task_estimate', args=[startup.pk, task.pk])) + '?' + urlencode({'method': method})
        task.priority_note = feature.priority_label if feature else mvp.PRIORITY_SHORT[estimate.priority]
        result.append(task)
    result.sort(key=lambda t: (t.score is None, -(t.score or 0)))
    for rank, task in enumerate(result, 1):
        task.rank = rank
    return result


def board(startup, tasks, features, method='rice', *, readiness=None):
    """A linked MVP task appears once, using its feature's existing estimates."""
    rows = task_rows(startup, tasks, features, method)
    represented = {str(t.ranked_feature.pk) for t in rows if t.ranked_feature}
    entries = []
    for task in rows:
        feature = task.ranked_feature
        entries.append({'key': 'task-' + str(task.pk), 'title': task.title,
            'description': task.instructions, 'task': task, 'feature': feature,
            'estimate': task.estimate_source, 'rice': task.rice, 'ice': task.ice, 'score': task.score,
            'preview': task.estimate_preview, 'score_preliminary': task.score_preliminary,
            'edit_url': task.estimate_url, 'lab_url': task.lab_url,
            'kind_label': ('MVP · ' if feature or task.origin == 'mvp' else '') + task.get_axis_display(),
            'priority': feature.effective_priority if feature else task.estimate_source.priority,
            'manual_priority': task.estimate_source.priority,
            'in_mvp': bool(feature and feature.in_mvp),
            'kano_url': reverse('feature_kano', args=[startup.pk, feature.pk]) + f'?method={method}' if feature and kano_enabled() else '',
            'work_url': '#task-' + str(task.pk)})
    for feature in features:
        if feature.task_id or str(feature.pk) in represented:
            continue
        preview = resolve_estimate(feature)
        entries.append({'key': 'feature-' + str(feature.pk), 'title': feature.title,
            'description': feature.description, 'task': None, 'feature': feature, 'estimate': preview.estimate,
            'rice': preview.rice, 'ice': preview.ice, 'score': preview.ice if method == 'ice' else preview.rice,
            'preview': preview, 'score_preliminary': preview.ice_preliminary if method == 'ice' else preview.rice_preliminary,
            'edit_url': reverse('feature_edit', args=[startup.pk, feature.pk]) + f'?method={method}',
            'lab_url': reverse('lab', args=[startup.pk]) + '?' + urlencode({'feature': feature.pk, 'method': method}) + '#lab-task-context',
            'kind_label': 'Функция MVP', 'priority': feature.effective_priority,
            'manual_priority': feature.priority, 'in_mvp': feature.in_mvp,
            'kano_url': reverse('feature_kano', args=[startup.pk, feature.pk]) + f'?method={method}' if kano_enabled() else '',
            'work_url': '#execute-feature-' + str(feature.pk)})
    entries.sort(key=lambda e: (e['score'] is None, -(e['score'] or 0)))
    rated = [e for e in entries if e['score'] is not None]
    for rank, entry in enumerate(entries, 1):
        entry['rank'] = rank
        suggestion = ''
        if entry['score'] is not None:
            better = sum(e['score'] > entry['score'] for e in rated)
            position = better / max(len(rated) - 1, 1)
            suggestion = ('wont' if entry['score'] == 0 else 'must' if position < .25 else
                          'should' if position < .5 else 'could' if position < .75 else 'wont')
        if entry['feature'] is None:
            entry['priority'] = entry['manual_priority'] or suggestion
        entry['priority_label'] = mvp.PRIORITY_SHORT[entry['priority']]
        entry['automatic'] = not bool(entry['manual_priority'])
        if entry['task']:
            entry['task'].priority_note = entry['priority_label']
    preliminary = sum(e['score_preliminary'] for e in entries)
    needing_bruno = sum(e['preview'].needs_bruno for e in entries)
    return {'priority_entries': entries, 'priority_count': len(entries),
            'priority_rated_count': len(rated),
            'priority_saved_count': len(rated) - preliminary, 'priority_preliminary_count': preliminary,
            'priority_needs_bruno': needing_bruno,
            'priority_first': next((e for e in entries if e['priority'] != 'wont'), None)}, rows


def _excerpt(text, limit):
    value = ' '.join((text or '').split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'


def lab_prompt(startup, *, task=None, feature=None, source=None):
    """Prepare a reviewable prompt locally; opening the lab never calls AI."""
    parts = [
        'Доработай текущий сайт.' if source else 'Создай первую рабочую версию сайта.',
        'Проект: ' + _excerpt(startup.name, 80),
    ]
    if startup.one_line_pitch:
        parts.append('Идея: ' + _excerpt(startup.one_line_pitch, 120))
    parts.append('Задача: ' + _excerpt(task.title if task else feature.title, 160))
    if task and task.success_criterion:
        parts.append('Готово, когда: ' + _excerpt(task.success_criterion, 400))
    if task and task.instructions and not (task.origin == 'mvp' and feature):
        parts.append('Что сделать: ' + _excerpt(task.instructions, 500))
    if feature and feature.description:
        parts.append('Сценарий пользователя: ' + _excerpt(feature.description, 300))
    if source:
        parts.append('Сохрани работающие функции, оформление и существующие данные. Меняй только то, что нужно для этой задачи.')
    parts.append('Сделай понятный адаптивный интерфейс, обработку ошибок и пустых состояний. '
                 'Проверь основной сценарий. В ответе опиши результат и ограничения. '
                 'Не заявляй о выполненных проверках, если не проводил их.')
    return '\n\n'.join(parts)[:2000].rstrip()

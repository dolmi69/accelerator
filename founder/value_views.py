"""«Клиент и ценность»: ABCDX → брейншторм по сегменту A → ценностное предложение.

Запросы к модели (value_segments, value_brainstorm, value_generate) ограничивает
security_middleware так же, как остальные AI-действия проекта.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from founder.services import activity
from founder.services import value_canvas as service
from founder.services.access import get_startup
from founder.services.ai import AIServiceError
from founder.services.qwen import QwenError


def _back(startup, anchor):
    return redirect(reverse('value', args=[startup.pk]) + f'#{anchor}')


def _clean(request, name, limit):
    return ' '.join(request.POST.get(name, '').split())[:limit]


@login_required
@require_GET
def value_page(request, startup_id):
    startup = get_startup(request, startup_id)
    canvas = service.canvas_for(startup)
    order = list(service.GRADES)
    segments = sorted(enumerate(canvas.segments), key=lambda pair: order.index(pair[1].get('grade', 'X')))
    options = canvas.options or {}
    return render(request, 'founder/value.html', {
        'startup': startup, 'workspace_tab': 'value', 'canvas': canvas,
        'segments': [{**item, 'index': index} for index, item in segments],
        'grades': service.GRADES.items(), 'has_a': bool(service.segments_of(canvas, 'A')),
        'situations': list(enumerate(canvas.situations)),
        'jobs': [(index, {**item, 'kind_label': service.JOB_KINDS.get(item.get('kind'), '')})
                 for index, item in enumerate(canvas.jobs)],
        'options': [('Формула', text) for text in options.get('formula', [])]
                   + [('Мы это… для…, которые хотят…', text) for text in options.get('statements', [])]
                   + [('Сравнение', text) for text in options.get('analogies', [])],
        'can_edit': startup.user_role != 'viewer',
    })


def _run(request, startup, action, success, anchor):
    try:
        action(startup)
        messages.success(request, success)
    except (AIServiceError, QwenError, ValueError) as exc:
        messages.error(request, str(exc))
    return _back(startup, anchor)


@login_required
@require_POST
def value_segments(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    return _run(request, startup, service.suggest_segments,
                'Бруно предложил сегменты. Проверьте буквы: решаете вы.', 'abcdx')


@login_required
@require_POST
def value_segments_save(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    canvas = service.canvas_for(startup)
    segments = []
    for index, item in enumerate(canvas.segments):
        if request.POST.get(f'delete_{index}'):
            continue
        grade = request.POST.get(f'grade_{index}', item.get('grade', 'X'))
        segments.append({**item, 'grade': grade if grade in service.GRADES else 'X'})
    title = _clean(request, 'new_title', 80)
    if title and len(segments) < service.MAX_SEGMENTS:
        grade = request.POST.get('new_grade', 'X')
        segments.append({'title': title, 'description': _clean(request, 'new_description', 300),
                         'context': '', 'holder': _clean(request, 'new_holder', 200), 'reason': '',
                         'grade': grade if grade in service.GRADES else 'X', 'source': 'founder'})
    canvas.segments = segments
    canvas.save(update_fields=['segments', 'updated_at'])
    ideal = len(service.segments_of(canvas, 'A'))
    activity.log(startup, request.user, activity.Kind.CARD, f'Сегменты ABCDX обновлены, сегментов A: {ideal}')
    messages.success(request, 'Сегменты сохранены.' + ('' if ideal else ' Отметьте буквой A хотя бы один, чтобы перейти к брейншторму.'))
    return _back(startup, 'abcdx')


@login_required
@require_POST
def value_brainstorm(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    return _run(request, startup, service.brainstorm,
                'Брейншторм готов. Отметьте ситуации и задачи, которые узнаёте у своих клиентов.', 'brainstorm')


@login_required
@require_POST
def value_brainstorm_save(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    canvas = service.canvas_for(startup)
    chosen_situations = set(request.POST.getlist('situations'))
    chosen_jobs = set(request.POST.getlist('jobs'))
    canvas.situations = [{**item, 'chosen': str(index) in chosen_situations} for index, item in enumerate(canvas.situations)]
    canvas.jobs = [{**item, 'chosen': str(index) in chosen_jobs} for index, item in enumerate(canvas.jobs)]
    canvas.save(update_fields=['situations', 'jobs', 'updated_at'])
    messages.success(request, 'Выбор сохранён. Бруно учтёт его, когда будет формулировать ценность.')
    return _back(startup, 'value-step')


@login_required
@require_POST
def value_generate(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    return _run(request, startup, service.formulate_value,
                'Бруно предложил формулировки. Выберите одну и при желании поправьте.', 'value-step')


@login_required
@require_POST
def value_save(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    canvas = service.canvas_for(startup)
    text = _clean(request, 'value', service.VALUE_LIMIT)
    if not text:
        messages.error(request, 'Выберите формулировку или напишите свою.')
        return _back(startup, 'value-step')
    offered = {item.casefold() for group in (canvas.options or {}).values() for item in group}
    canvas.value = text
    canvas.value_source = 'bruno' if text.casefold() in offered else 'founder'
    canvas.save(update_fields=['value', 'value_source', 'updated_at'])
    activity.log(startup, request.user, activity.Kind.CARD, f'Ценностное предложение: {activity.quoted(text)}')
    messages.success(request, 'Ценностное предложение сохранено. Бруно и акулы будут на него опираться.')
    return _back(startup, 'value-step')

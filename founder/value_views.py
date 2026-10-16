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
from founder.services.mentor import AREAS as AREA_LABELS


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
        'options': [('Формула', text, options.get('formula_complete', True)) for text in options.get('formula', [])]
                   + [('Сравнение «X для Y»', text, True) for text in options.get('analogies', [])]
                   + [('Мы это… для…, которые хотят…', text, True) for text in options.get('statements', [])],
        'problem': options.get('problem') or {},
        'formula_complete': options.get('formula_complete', True),
        'question': options.get('question', ''),
        'unknown_labels': [label for key, label in AREA_LABELS if key in options.get('unknown', [])],
        'everyone': options.get('everyone', False),
        'problem_harms': [service.HARMS[key] for key in (options.get('problem') or {}).get('harm', [])],
        'offered_jobs': [(kind, service.JOB_KINDS[kind], text) for kind, text in (options.get('jobs') or {}).items()
                         if kind in service.JOB_KINDS],
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
    if title and (service.is_everyone(title) or service.is_everyone(_clean(request, 'new_description', 300))):
        messages.error(request, '«Все» — не сегмент. Назовите конкретных людей или компании: кто они и в какой ситуации.')
        return _back(startup, 'abcdx')
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
    text = _clean(request, 'value', 1000)  # apply_value trims it to the pitch length at a word boundary.
    if not text:
        messages.error(request, 'Выберите формулировку или напишите свою.')
        return _back(startup, 'value-step')
    if service.UNKNOWN in text.casefold():
        messages.error(request, 'В формулировке осталось «неизвестно». Сначала ответьте на вопрос Бруно.')
        return _back(startup, 'value-step')
    offered = {item.casefold() for key in ('formula', 'statements', 'analogies')
               for item in (canvas.options or {}).get(key, [])}
    canvas.value_source = 'bruno' if text.casefold() in offered else 'founder'
    updated = service.apply_value(startup, canvas, text)
    activity.log(startup, request.user, activity.Kind.CARD, f'Ценностное предложение: {activity.quoted(text)}')
    parts = ['питч проекта'] + (['проблему проекта'] if updated['problem'] else []) + (
        ['черновик карточки'] if updated['card'] else [])
    messages.success(request, 'Ценностное предложение сохранено и записано в ' + ' и '.join(
        [', '.join(parts[:-1]), parts[-1]] if len(parts) > 1 else parts) + '. Оттуда его берут карточка и питч.')
    return _back(startup, 'value-step')


@login_required
@require_POST
def value_problem_adopt(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    try:
        service.adopt_problem(startup, service.canvas_for(startup))
        messages.success(request, 'Проблема записана в картину проекта как догадка. Подтвердите её разговорами с клиентами.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return _back(startup, 'value-step')


@login_required
@require_POST
def value_jobs_adopt(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    try:
        added = service.adopt_jobs(service.canvas_for(startup))
        messages.success(request, f'Задачи клиента добавлены в шаг 2: {added}.' if added else 'Эти задачи уже есть в шаге 2.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return _back(startup, 'value-step')


@login_required
@require_POST
def value_answer(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    try:
        service.answer_question(startup, service.canvas_for(startup), request.POST.get('answer', ''))
        messages.success(request, 'Ответ записан в картину проекта. Нажмите «Сформулировать ценность» ещё раз.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return _back(startup, 'value-step')

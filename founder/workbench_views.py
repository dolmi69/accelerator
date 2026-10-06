"""Owner-scoped views for tasks, evidence and investor practice."""
from uuid import UUID

from django.conf import settings
from django.http import Http404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from founder.forms import EvidenceForm
from founder.models import BrunoTask, BusinessAxis, ChatMessage, ChatSession, EvidenceEntry, StartupProfile
from founder.services.ai import AIServiceError
from founder.services.panel import ORDER as SHARK_ORDER, shark_info
from founder.services.review import create_review, guess_axis, step_to_task
from founder.services.workbench import generate_tasks


def owned_startup(request, startup_id):
    return get_object_or_404(StartupProfile, pk=startup_id, owner=request.user)


@login_required
def tasks(request, startup_id):
    startup = owned_startup(request, startup_id)
    return render(request, 'founder/tasks.html', {
        'startup': startup, 'workspace_tab': 'tasks',
        'open_tasks': startup.bruno_tasks.filter(status=BrunoTask.Status.TODO),
        'past_tasks': startup.bruno_tasks.exclude(status=BrunoTask.Status.TODO)[:15],
        'is_demo': settings.AI_PROVIDER == 'demo',
    })


@login_required
@require_POST
def tasks_generate(request, startup_id):
    startup = owned_startup(request, startup_id)
    try:
        items = generate_tasks(startup)
        messages.success(request, f'Бруно подготовил заданий: {len(items)}.' if items else 'У вас уже есть задания в работе. Сначала сохраните результат или отложите одно из них.')
    except AIServiceError as exc:
        messages.error(request, str(exc))
    return redirect('tasks', startup_id=startup.pk)


@login_required
@require_POST
def task_skip(request, startup_id, task_id):
    startup = owned_startup(request, startup_id)
    task = get_object_or_404(startup.bruno_tasks, pk=task_id)
    startup.bruno_tasks.filter(pk=task.pk, status=BrunoTask.Status.TODO).update(status=BrunoTask.Status.SKIPPED)
    return redirect('tasks', startup_id=startup.pk)


@login_required
def evidence_list(request, startup_id):
    startup = owned_startup(request, startup_id)
    entries = startup.evidence_entries.select_related('task')
    axis = request.GET.get('axis', '')
    if axis in BusinessAxis.values:
        entries = entries.filter(axis=axis)
    else:
        axis = ''
    return render(request, 'founder/evidence.html', {
        'startup': startup, 'workspace_tab': 'evidence', 'axes': BusinessAxis.choices,
        'selected_axis': axis, 'entries': Paginator(entries, 15).get_page(request.GET.get('page')),
    })


@login_required
def evidence_edit(request, startup_id, entry_id=None):
    startup = owned_startup(request, startup_id)
    entry = get_object_or_404(startup.evidence_entries, pk=entry_id) if entry_id else None
    initial = {}
    if not entry and request.GET.get('task'):
        try:
            task_id = UUID(request.GET['task'])
        except (ValueError, TypeError) as exc:
            raise Http404('Задание не найдено') from exc
        task = get_object_or_404(startup.bruno_tasks, pk=task_id)
        initial = {'task': task, 'axis': task.axis, 'claim': task.title}
    elif not entry and request.GET.get('message'):
        # Черновик записи из сообщения в чате: основатель проверит и дополнит поля.
        try:
            message_id = UUID(request.GET['message'])
        except (ValueError, TypeError) as exc:
            raise Http404('Сообщение не найдено') from exc
        message = get_object_or_404(ChatMessage, pk=message_id, role=ChatMessage.Role.USER,
                                    session__startup=startup, session__mode=ChatSession.Mode.COFOUNDER)
        initial = {'axis': guess_axis(message.content), 'observation': message.content[:4000],
                   'observed_on': timezone.localtime(message.created_at).date()}
    form = EvidenceForm(request.POST if request.method == 'POST' else None,
                        instance=entry, initial=initial, startup=startup)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            if saved.task_id:
                startup.bruno_tasks.filter(pk=saved.task_id, status=BrunoTask.Status.TODO).update(
                    status=BrunoTask.Status.DONE, completed_at=timezone.now(),
                )
        messages.success(request, 'Результат сохранён. Следующее обновление радара учтёт эту запись; сама запись не добавляет баллы.')
        return redirect('evidence_list', startup_id=startup.pk)
    return render(request, 'founder/evidence_form.html', {
        'startup': startup, 'workspace_tab': 'evidence', 'form': form, 'entry': entry,
    }, status=400 if request.method == 'POST' else 200)


@login_required
def investor(request, startup_id):
    startup = owned_startup(request, startup_id)
    sessions = list(startup.chat_sessions.filter(mode__in=[ChatSession.Mode.PITCH, ChatSession.Mode.PANEL])
                    .select_related('pitch_report', 'panel_verdict')[:20])
    return render(request, 'founder/investor.html', {
        'startup': startup, 'workspace_tab': 'investor', 'sessions': sessions,
        'sharks': [shark_info(key) for key in SHARK_ORDER],
    })


@login_required
def review(request, startup_id):
    startup = owned_startup(request, startup_id)
    reviews = startup.reviews.all()
    current = reviews.first()
    if request.GET.get('id'):
        try:
            current = get_object_or_404(reviews, pk=UUID(request.GET['id']))
        except (ValueError, TypeError) as exc:
            raise Http404('Разбор не найден') from exc
    open_axes = set(startup.bruno_tasks.filter(status=BrunoTask.Status.TODO).values_list('axis', flat=True))
    axis_labels = dict(BusinessAxis.choices)
    data = current.data if current else {}
    return render(request, 'founder/review.html', {
        'startup': startup, 'workspace_tab': 'review', 'review': current, 'data': data,
        'stage_label': dict(StartupProfile.Stage.choices).get(data.get('stage'), ''),
        'risks': [{**risk, 'axis_label': axis_labels.get(risk['axis'], '')} for risk in data.get('risks', [])],
        'steps': [{**step, 'index': index, 'axis_label': axis_labels.get(step['axis'], ''),
                   'axis_busy': step['axis'] in open_axes} for index, step in enumerate(data.get('steps', []))],
        'history': reviews[:8], 'is_demo': settings.AI_PROVIDER == 'demo',
        'has_story': (any((startup.one_line_pitch, startup.problem, startup.solution, startup.target_customer))
                      or startup.chat_sessions.filter(messages__role='user').exists()
                      or startup.evidence_entries.exists()),
    })


@login_required
@require_POST
def review_generate(request, startup_id):
    startup = owned_startup(request, startup_id)
    try:
        create_review(startup)
        messages.success(request, 'Бруно разобрал проект и составил план на месяц.')
    except AIServiceError as exc:
        messages.error(request, str(exc))
    return redirect('review', startup_id=startup.pk)


@login_required
@require_POST
def review_step_task(request, startup_id, review_id, index):
    startup = owned_startup(request, startup_id)
    current = get_object_or_404(startup.reviews, pk=review_id)
    try:
        task = step_to_task(current, index)
    except AIServiceError as exc:
        raise Http404(str(exc)) from exc
    if task is None:
        messages.error(request, 'По этому направлению уже есть задание в работе. Сначала завершите или отложите его.')
        return redirect(f"{reverse('review', args=[startup.pk])}?id={current.pk}")
    messages.success(request, 'Шаг добавлен в задания. Результат запишите в дневник, когда сделаете.')
    return redirect('tasks', startup_id=startup.pk)

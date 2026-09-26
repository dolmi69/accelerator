"""Owner-scoped views for tasks, evidence and investor practice."""
from uuid import UUID

from django.conf import settings
from django.http import Http404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from founder.forms import EvidenceForm
from founder.models import BrunoTask, BusinessAxis, ChatSession, EvidenceEntry, StartupProfile
from founder.services.ai import AIServiceError
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
    return render(request, 'founder/investor.html', {
        'startup': startup, 'workspace_tab': 'investor',
        'sessions': startup.chat_sessions.filter(mode=ChatSession.Mode.PITCH).select_related('pitch_report')[:20],
    })

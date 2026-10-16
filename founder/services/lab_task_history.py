"""Server-side binding of a lab request to one eligible task in this project."""
from uuid import UUID
from django.http import Http404
from django.shortcuts import get_object_or_404
from founder.models import BrunoTask, StartupProfile
from founder.services.work_priorities import can_use_lab, linked_features
from founder.services.mvp import board
from founder.services.project_history import task_digest


def identifier(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise Http404('Задание не найдено') from exc


def bind_context(request, startup, payload, mode):
    task_id, feature_id = request.POST.get('task_context'), request.POST.get('feature_context')
    if not task_id and not feature_id:
        return
    if mode != 'edit':
        payload.pop('task_context', None)
        payload.pop('feature_context', None)
        return
    if bool(task_id) == bool(feature_id):
        raise Http404('Выберите одно задание или функцию')
    StartupProfile.objects.select_for_update().get(pk=startup.pk)
    if task_id:
        task = get_object_or_404(startup.bruno_tasks, pk=identifier(task_id), status='todo')
        feature = linked_features(startup, board(startup)).get(str(task.pk))
        if not can_use_lab(task, feature):
            raise Http404('Задание выполняется вне лаборатории')
    else:
        feature = get_object_or_404(startup.feature_candidates.select_related('task'), pk=identifier(feature_id))
        task = feature.task
        if task and not can_use_lab(task, feature):
            raise Http404('Задание уже завершено')
        if not task:
            task = BrunoTask.objects.create(startup=startup, origin='mvp', axis='product', title=feature.title,
                instructions=feature.description, success_criterion='Функция работает в основном пользовательском сценарии; результат проверки сохранён.')
            feature.task = task
            feature.save(update_fields=['task', 'updated_at'])
    payload['task_context'] = [str(task.pk)]
    payload.pop('feature_context', None)
    payload['_task_digest'] = [task_digest(task)]


def validate_context(request, startup):
    if request.POST.get('feature_context'):
        from django.db import transaction
        with transaction.atomic():
            payload = {}
            bind_context(request, startup, payload, 'edit')
        post = request.POST.copy()
        post.pop('feature_context', None)
        for key, values in payload.items():
            post.setlist(key, values)
        request.POST = post
    if request.POST.get('task_context'):
        task = get_object_or_404(startup.bruno_tasks, pk=identifier(request.POST['task_context']), status='todo')
        feature = linked_features(startup, board(startup)).get(str(task.pk))
        if not can_use_lab(task, feature):
            raise Http404('Задание выполняется вне лаборатории')
        if not getattr(request, 'lab_background_job', False) or not request.POST.get('_task_digest'):
            post = request.POST.copy()
            post['_task_digest'] = task_digest(task)
            request.POST = post

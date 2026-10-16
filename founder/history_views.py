"""Project history and the private «Расскажи Бруно» result conversation."""
from uuid import UUID, uuid4
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction, OperationalError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from founder.models import EvidenceConversation, EvidenceEntry, BrunoTask, StartupProfile
from founder.services.access import get_startup
from founder.services.kano_availability import visible_tasks
from founder.services.project_history import snapshot, finish_task, task_digest, task_snapshot
from founder.services.result_conversation import draft_reply
from founder.services.ai import AIServiceError
from founder.services.lab_bruno import LabRequestRejected
from founder.services.request_limits import acquire_ai_lease, release_ai_lease, consume_limit, RequestLimitExceeded


def _uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise Http404('Разговор не найден') from exc


def conversation_url(startup, conversation):
    return reverse('result_tell', args=[startup.pk]) + '?draft=' + str(conversation.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def tell(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    draft_id = request.POST.get('draft') or request.GET.get('draft')
    task_id = request.POST.get('task') or request.GET.get('task')
    conversation = None
    if draft_id:
        conversation = get_object_or_404(EvidenceConversation.objects.select_related('task', 'startup'),
            pk=_uuid(draft_id), startup=startup, author=request.user)
    task = conversation.task if conversation else (get_object_or_404(visible_tasks(startup.bruno_tasks), pk=_uuid(task_id)) if task_id else None)
    if task and task.status != 'todo' and not (conversation and conversation.entry_id):
        messages.info(request, 'Этот шаг уже завершён. Новый результат можно рассказать отдельно.')
        return redirect('result_tell', startup_id=startup.pk)
    if not conversation and task:
        conversation = startup.result_conversations.filter(author=request.user, task=task, entry__isnull=True).first()
    if conversation and conversation.entry_id:
        return redirect(reverse('evidence_list', args=[startup.pk]) + '#entry-' + str(conversation.entry_id))
    if conversation and conversation.processing and conversation.updated_at < timezone.now() - timedelta(minutes=12):
        EvidenceConversation.objects.filter(pk=conversation.pk, processing=True).update(processing=False)
        conversation.refresh_from_db()
        messages.info(request, 'Ответ прервался. Рассказ сохранён; можно продолжить разговор.')
    if request.method == 'POST':
        if request.POST.get('action') == 'confirm':
            if not conversation:
                raise Http404('Черновик не найден')
            with transaction.atomic():
                conversation = EvidenceConversation.objects.select_for_update().select_related('task').get(pk=conversation.pk)
                if conversation.entry_id:
                    return redirect('evidence_list', startup_id=startup.pk)
                proposal = conversation.proposal
                if (conversation.processing or not proposal.get('ready') or
                    request.POST.get('revision') != str(conversation.revision) or
                    (conversation.task_id and proposal.get('_task_digest') != task_digest(conversation.task))):
                    messages.error(request, 'Черновик изменился или ещё не готов. Проверьте последнюю версию.')
                    return redirect(conversation_url(startup, conversation))
                entry = EvidenceEntry.objects.create(startup=startup, author=request.user, task=conversation.task,
                    claim=proposal['claim'], observation=proposal['observation'], source=proposal['source'] or 'Рассказ участника проекта',
                    axis=proposal['axis'], outcome=proposal['outcome'], next_question=proposal['next_step'],
                    progress={'verification':'reported', 'learned':proposal['learned'], 'next_step':proposal['next_step'],
                        'report_model':conversation.model, 'next_estimate':proposal.get('next_estimate', {}), 'completed':True, 'task_snapshot':task_snapshot(conversation.task) if conversation.task_id else {}})
                snapshot(entry, request.user)
                finish_task(entry, request.user)
                from founder.services.coins import award, Kind
                award(request.user, Kind.EVIDENCE, key=entry.pk, startup=startup, note=entry.claim)
                conversation.entry = entry
                conversation.save(update_fields=['entry', 'updated_at'])
            messages.success(request, 'Шаг сохранён в истории проекта.' + (' Задание завершено.' if entry.task_id else ''))
            return redirect(reverse('evidence_list', args=[startup.pk]) + '#entry-' + str(entry.pk))
        text = request.POST.get('message', '').strip()
        if not text or len(text) > 4000:
            messages.error(request, 'Расскажите о результате: от 1 до 4000 символов.')
        else:
            key = _uuid(request.POST.get('request_key'))
            with transaction.atomic():
                StartupProfile.objects.select_for_update().get(pk=startup.pk)
                if not conversation:
                    conversation, _ = EvidenceConversation.objects.get_or_create(id=key, defaults={'startup':startup, 'author':request.user, 'task':task})
                    if conversation.startup_id != startup.pk or conversation.author_id != request.user.pk:
                        raise Http404('Разговор не найден')
                conversation = EvidenceConversation.objects.select_for_update().select_related('task','startup').get(pk=conversation.pk)
                if conversation.processing or conversation.request_key == key:
                    return redirect(conversation_url(startup, conversation))
                if len(conversation.messages) >= 24:
                    messages.error(request, 'Разговор достиг лимита. Сохраните результат или начните новую запись.')
                    return redirect(conversation_url(startup, conversation))
                conversation.messages = [*conversation.messages, {'role':'user','text':text}]
                conversation.proposal = {}
                conversation.processing = True
                conversation.request_key = key
                conversation.revision += 1
                conversation.save()
            token = None
            try:
                token = acquire_ai_lease(request.user.pk)
                consume_limit(f'ai-minute:{request.user.pk}', settings.AI_REQUESTS_PER_MINUTE, 60)
                consume_limit(f'ai-day:{request.user.pk}', settings.AI_REQUESTS_PER_DAY, 86400)
                reply, model = draft_reply(conversation)
                reply['_task_digest'] = task_digest(task) if task else ''
                with transaction.atomic():
                    saved = EvidenceConversation.objects.select_for_update().get(pk=conversation.pk)
                    if saved.revision == conversation.revision:
                        saved.proposal = reply
                        saved.model = model
                        saved.messages = [*saved.messages, {'role':'assistant','text':reply['reply'], 'question':reply['question']}]
                        saved.processing = False
                        saved.save()
            except (AIServiceError, LabRequestRejected, RequestLimitExceeded, ValueError, OperationalError):
                EvidenceConversation.objects.filter(pk=conversation.pk).update(processing=False)
                messages.error(request, 'Бруно не завершил ответ. Ваш рассказ сохранён; можно продолжить или записать результат вручную.')
            finally:
                if token:
                    release_ai_lease(request.user.pk, token)
            return redirect(conversation_url(startup, conversation))
    return render(request, 'founder/result_tell.html', {'startup':startup, 'workspace_tab':'evidence',
        'conversation':conversation, 'task':task, 'request_key':uuid4()})


@login_required
@require_POST
def verify_result(request, startup_id, entry_id):
    startup = get_startup(request, startup_id, edit=True)
    with transaction.atomic():
        entry = get_object_or_404(startup.evidence_entries.select_for_update().select_related('task'), pk=entry_id, origin='lab')
        if entry.progress.get('verification') != 'needs_check':
            return redirect('evidence_list', startup_id=startup.pk)
        if not entry.task_id or entry.task.status != 'todo':
            messages.error(request, 'Задание уже закрыто или недоступно.')
        elif request.POST.get('confirmed') != '1':
            messages.error(request, 'Сначала проверьте критерий готовности в работающем сайте.')
        elif not entry.progress.get('task_matches_request', True) or entry.progress.get('task_snapshot') != task_snapshot(entry.task):
            messages.error(request, 'Условия задания изменились. Расскажите Бруно о результате новой проверки.')
        else:
            entry.progress = {**entry.progress, 'verification':'confirmed', 'completed':True,
                'confirmed_by':request.user.public_name, 'confirmed_at':timezone.now().isoformat()}
            entry.save(update_fields=['progress','updated_at'])
            snapshot(entry, request.user)
            finish_task(entry, request.user)
            messages.success(request, 'Ваша проверка сохранена. Задание завершено.')
    return redirect(reverse('evidence_list', args=[startup.pk]) + '#entry-' + str(entry.pk))


@login_required
@require_POST
def add_followup(request, startup_id, entry_id):
    startup = get_startup(request, startup_id, edit=True)
    with transaction.atomic():
        StartupProfile.objects.select_for_update().get(pk=startup.pk)
        entry = get_object_or_404(startup.evidence_entries.select_for_update(), pk=entry_id)
        action = entry.progress.get('next_step', '')
        if not action:
            raise Http404('Следующий шаг не предложен')
        if not entry.followup_task_id:
            entry.followup_task = BrunoTask.objects.create(startup=startup, axis=entry.axis, origin='progress',
                title=action[:160], instructions=action[:1200],
                success_criterion='Проверка проведена; наблюдения и ограничения сохранены в истории проекта.',
                ai_model=entry.progress.get('report_model', ''))
            if entry.progress.get('next_estimate'):
                from founder.services.bruno_estimates import save_subject_estimate
                save_subject_estimate(entry.followup_task, entry.progress['next_estimate'], entry.progress.get('report_model', ''))
            entry.save(update_fields=['followup_task','updated_at'])
    return redirect(reverse('tasks', args=[startup.pk]) + '#task-' + str(entry.followup_task_id))

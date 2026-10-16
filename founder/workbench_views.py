"""Team-scoped views for tasks, evidence and investor practice."""
from uuid import UUID

from founder.services.kano_availability import visible_tasks, step_visible
from django.conf import settings
from django.http import Http404, HttpResponseBadRequest
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from founder.forms import EvidenceForm
from founder.models import BrunoTask, BusinessAxis, ChatMessage, ChatSession, CoinTransaction, EvidenceEntry, StartupProfile
from founder.services import market as market_service
from founder.services.ai import AIServiceError
from founder.services.panel import ORDER as SHARK_ORDER, shark_info
from founder.services.access import assignable_users, get_startup
from founder.services import activity
from founder.services.coins import award, coins_note
from founder.services.review import create_review, context_digest, guess_axis, step_to_task, populate_tasks, task_priorities
from founder.services.readiness import readiness_context


def owned_startup(request, startup_id, *, edit=True):
    return get_startup(request, startup_id, edit=edit)


def _workspace_return(request, startup, page, section):
    # Only this fixed destination is accepted, never an arbitrary redirect URL.
    if request.POST.get('return_to') == 'radar':
        return redirect(reverse('dashboard', args=[startup.pk]) + '#' + section)
    return redirect(page, startup_id=startup.pk)


@login_required
def tasks(request, startup_id):
    startup = owned_startup(request, startup_id, edit=False)
    open_tasks = visible_tasks(startup.bruno_tasks).filter(status=BrunoTask.Status.TODO).select_related('assignee','kano_feature','estimate')
    from founder.services.work_assignments import selected_filter, filter_board, filter_query
    people = assignable_users(startup)
    assignee_filter = selected_filter(request, people)
    mine = assignee_filter == 'mine'
    from founder.feature_views import board_context
    reviews = startup.reviews.all()
    current = reviews.first()
    review_id = request.GET.get('review') or request.GET.get('id')
    if review_id:
        try:
            current = get_object_or_404(reviews, pk=UUID(review_id))
        except (ValueError, TypeError) as exc:
            raise Http404('Разбор не найден') from exc
    data = current.data if current else {}
    open_axes = set(visible_tasks(startup.bruno_tasks).filter(origin='bruno', status=BrunoTask.Status.TODO)
                    .values_list('axis', flat=True))
    labels = dict(BusinessAxis.choices)
    accepted = data.get('_accepted_steps', {})
    feature_titles = {title.casefold() for title in startup.feature_candidates.values_list('title', flat=True)}
    proposals = [item for item in data.get('features', []) if item['title'].casefold() not in feature_titles]
    board = board_context(startup, request)
    from founder.services.work_priorities import board as priority_board
    readiness = readiness_context(startup)
    priorities, open_tasks = priority_board(startup, open_tasks, board['features'], board['mvp_method'], readiness=readiness)
    from collections import Counter
    counts = Counter(task.assignee_id for task in open_tasks)
    for person in people:
        person.open_task_count = counts[person.pk]
    entries = priorities['priority_entries']
    filter_counts = {
        'all': len(entries),
        'mine': sum(item['task'] is not None and item['task'].assignee_id == request.user.pk for item in entries),
        'unassigned': sum(item['task'] is None or item['task'].assignee_id is None for item in entries),
    }
    priorities, open_tasks = filter_board(priorities, open_tasks, assignee_filter, request.user)
    for item in priorities['priority_entries']:
        item['assignment_url'] = (reverse('task_assign', args=[startup.pk, item['task'].pk]) if item['task'] else
                                  reverse('feature_assign', args=[startup.pk, item['feature'].pk]))
    return render(request, 'founder/tasks.html', {
        **board, **priorities,
        'startup': startup, 'workspace_tab': 'tasks', 'open_tasks': open_tasks, 'mine': mine,
        'readiness': readiness, 'focus_choices': StartupProfile.WorkFocus.choices,
        'review': current, 'data': data, 'review_history': reviews[:8],
        'review_stale': bool(current and data.get('_context_digest') != context_digest(startup)),
        'risks': [{**risk, 'axis_label': labels.get(risk['axis'], '')} for risk in data.get('risks', [])],
        'steps': [{**step, 'index': index, 'axis_label': labels.get(step['axis'], ''),
                   'axis_busy': step['axis'] in open_axes, 'accepted': str(index) in accepted}
                  for index, step in enumerate(data.get('steps', [])) if step_visible(step)],
        'feature_proposals': proposals,
        'has_story': (any((startup.one_line_pitch, startup.problem, startup.solution, startup.target_customer))
                      or startup.chat_sessions.filter(messages__role='user').exists()
                      or startup.evidence_entries.exists()),
        'past_tasks': visible_tasks(startup.bruno_tasks).exclude(status=BrunoTask.Status.TODO).select_related('assignee')[:15],
        'is_demo': settings.AI_PROVIDER == 'demo',
        'team': people, 'assignee_filter':assignee_filter,
        'rice_filter_query':filter_query(assignee_filter, 'rice'), 'ice_filter_query':filter_query(assignee_filter, 'ice'),
        'assignee_options':[('all','Все задания'), ('mine','Мои задания'), ('unassigned','Без исполнителя'),
                            *((str(person.pk), person.public_name) for person in people)],
        'task_filter_options': [{'value': value, 'label': label, 'count': filter_counts[value]}
                                for value, label in (('all', 'Все задания'), ('mine', 'Мои задания'),
                                                     ('unassigned', 'Без исполнителя'))],
    })


@login_required
@require_POST
def task_assign(request, startup_id, task_id):
    return _assign_work(request, startup_id, task_id=task_id)


@login_required
@require_POST
def feature_assign(request, startup_id, feature_id):
    return _assign_work(request, startup_id, feature_id=feature_id)


@login_required
@require_POST
def feature_result(request, startup_id, feature_id):
    from founder.services.work_assignments import ensure_feature_task
    with transaction.atomic():
        StartupProfile.objects.select_for_update().filter(pk=startup_id).exists()
        startup = owned_startup(request, startup_id)
        feature = get_object_or_404(startup.feature_candidates.select_for_update(), pk=feature_id)
        task = ensure_feature_task(startup, feature)
    return redirect(reverse('result_tell', args=[startup.pk]) + f'?task={task.pk}')


def _assign_work(request, startup_id, *, task_id=None, feature_id=None):
    from founder.services.work_assignments import assignment_person, ensure_feature_task, filter_query
    with transaction.atomic():
        StartupProfile.objects.select_for_update().filter(pk=startup_id).exists()
        startup = owned_startup(request, startup_id)
        assignee = assignment_person(startup, request.POST.get('assignee', ''))
        if feature_id:
            feature = get_object_or_404(startup.feature_candidates.select_for_update(), pk=feature_id)
            # Clearing an unstarted feature should never create a task.
            task = ensure_feature_task(startup, feature) if assignee or feature.task_id else None
        else:
            task = get_object_or_404(visible_tasks(startup.bruno_tasks).select_for_update(), pk=task_id, status='todo')
        if task and task.assignee_id != (assignee.pk if assignee else None):
            task.assignee = assignee
            task.save(update_fields=['assignee'])
            activity.log(startup, request.user, activity.Kind.TASK,
                f"Задание {activity.quoted(task.title, 60)} — " + (f"исполнитель @{assignee.handle}" if assignee else "без исполнителя"))
    method = 'ice' if request.POST.get('method') == 'ice' else 'rice'
    selected = request.POST.get('work_filter', 'all')
    allowed = {'all', 'mine', 'unassigned', *(str(person.pk) for person in assignable_users(startup))}
    if selected not in allowed:
        selected = 'all'
    messages.success(request, 'Исполнитель: ' + assignee.public_name if assignee else 'Задание теперь без исполнителя.')
    anchor = f'priority-task-{task.pk}' if task else f'priority-feature-{feature_id}'
    return redirect(reverse('tasks', args=[startup.pk]) + '?' + filter_query(selected, method) + '#' + anchor)


@login_required
@require_POST
def tasks_generate(request, startup_id):
    # Compatibility endpoint: all project planning now uses the same analysis.
    return review_generate(request, startup_id)


@login_required
@require_POST
def task_skip(request, startup_id, task_id):
    startup = owned_startup(request, startup_id)
    task = get_object_or_404(visible_tasks(startup.bruno_tasks), pk=task_id)
    if visible_tasks(startup.bruno_tasks).filter(pk=task.pk, status=BrunoTask.Status.TODO).update(status=BrunoTask.Status.SKIPPED):
        activity.log(startup, request.user, activity.Kind.TASK, f"Задание отложено: {activity.quoted(task.title)}")
    return redirect('tasks', startup_id=startup.pk)


@login_required
def evidence_list(request, startup_id):
    startup = owned_startup(request, startup_id, edit=False)
    from founder.services.project_history import decorate, history_context
    entries = startup.evidence_entries.select_related('task', 'author', 'lab_version', 'followup_task', 'conversation').prefetch_related('revisions').order_by('-created_at', '-pk')
    axis = request.GET.get('axis', '')
    if axis in BusinessAxis.values:
        entries = entries.filter(axis=axis)
    else:
        axis = ''
    page = Paginator(entries, 15).get_page(request.GET.get('page'))
    page.object_list = [decorate(entry) for entry in page.object_list]
    return render(request, 'founder/evidence.html', {
        **history_context(startup),
        'result_drafts': startup.result_conversations.filter(author=request.user, entry__isnull=True)[:5],
        'startup': startup, 'workspace_tab': 'evidence', 'axes': BusinessAxis.choices,
        'selected_axis': axis, 'entries': page,
    })


@login_required
def evidence_edit(request, startup_id, entry_id=None):
    startup = owned_startup(request, startup_id)
    entry = get_object_or_404(startup.evidence_entries, pk=entry_id) if entry_id else None
    if entry and entry.origin == 'lab':
        return redirect(reverse('evidence_list', args=[startup.pk]) + '#entry-' + str(entry.pk))
    initial = {}
    if not entry and request.GET.get('milestone') in EvidenceEntry.Milestone.values:
        milestone = request.GET['milestone']
        initial = {'milestone': milestone,
                   'axis': 'finance' if milestone in ('payment', 'repeat_payments') else 'product' if milestone in ('pilot', 'retention') else 'market'}
    if not entry and request.GET.get('task'):
        try:
            task_id = UUID(request.GET['task'])
        except (ValueError, TypeError) as exc:
            raise Http404('Задание не найдено') from exc
        task = get_object_or_404(visible_tasks(startup.bruno_tasks), pk=task_id)
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
        earned = 0
        with transaction.atomic():
            from founder.services.project_history import snapshot
            if entry:
                snapshot(EvidenceEntry.objects.get(pk=entry.pk), request.user)
            saved = form.save(commit=False)
            if not saved.author_id:
                saved.author = request.user
            saved.save()
            snapshot(saved, request.user)
            if entry is None:
                earned += award(request.user, CoinTransaction.Kind.EVIDENCE, key=saved.pk,
                                startup=startup, note=saved.claim)
                activity.log(startup, request.user, activity.Kind.EVIDENCE,
                             f"Запись в дневнике: {activity.quoted(saved.claim)} — {saved.get_outcome_display().lower()}")
            if saved.task_id and visible_tasks(startup.bruno_tasks).filter(pk=saved.task_id, status=BrunoTask.Status.TODO).update(
                status=BrunoTask.Status.DONE, completed_at=timezone.now(),
            ):
                earned += award(request.user, CoinTransaction.Kind.TASK, key=saved.task_id,
                                startup=startup, note=saved.claim)
                activity.log(startup, request.user, activity.Kind.TASK,
                             f"Задание выполнено: {activity.quoted(saved.task.title)}")
        messages.success(request, 'Результат сохранён. Следующее обновление радара учтёт эту запись; сама запись не добавляет баллы.'
                         + coins_note(earned))
        return _workspace_return(request, startup, 'evidence_list', 'project-tasks')
    return render(request, 'founder/evidence_form.html', {
        'startup': startup, 'workspace_tab': 'evidence', 'form': form, 'entry': entry,
        'return_to_radar': request.POST.get('return_to') == 'radar',
    }, status=400 if request.method == 'POST' else 200)


@login_required
def investor(request, startup_id):
    startup = owned_startup(request, startup_id, edit=False)
    sessions = list(startup.chat_sessions.filter(mode__in=[ChatSession.Mode.PITCH, ChatSession.Mode.PANEL])
                    .select_related('pitch_report', 'panel_verdict')[:20])
    return render(request, 'founder/investor.html', {
        'startup': startup, 'workspace_tab': 'investor', 'sessions': sessions,
        'sharks': [shark_info(key) for key in SHARK_ORDER],
    })


@login_required
def review(request, startup_id):
    # Keep bookmarked historical review URLs, including ?id=, readable.
    return tasks(request, startup_id)


@login_required
@require_POST
def review_generate(request, startup_id):
    startup = owned_startup(request, startup_id)
    try:
        method = 'ice' if request.POST.get('method') == 'ice' else 'rice'
        with transaction.atomic():
            current = create_review(startup, method=method)
            created = populate_tasks(current)
        activity.log(startup, request.user, activity.Kind.REVIEW, "Обновлён единый разбор проекта")
        messages.success(request, f'План обновлён. Новых задач: {created}. Текущие задания сохранены.')
    except AIServiceError as exc:
        messages.error(request, str(exc))
    if request.POST.get('method') == 'ice' and request.POST.get('return_to') != 'radar':
        return redirect(reverse('tasks', args=[startup.pk]) + '?method=ice')
    return _workspace_return(request, startup, 'tasks', 'project-plan')


@login_required
@require_POST
def work_focus(request, startup_id):
    startup = owned_startup(request, startup_id)
    value = request.POST.get('focus')
    if value not in StartupProfile.WorkFocus.values:
        return HttpResponseBadRequest('Неизвестный фокус.')
    StartupProfile.objects.filter(pk=startup.pk).update(work_focus=value)
    messages.success(request, 'Фокус сохранён. Обновите разбор, когда захотите пересмотреть план.')
    return redirect(reverse('tasks', args=[startup.pk]) + '#work-focus')


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
    activity.log(startup, request.user, activity.Kind.TASK, f"Шаг плана стал заданием: {activity.quoted(task.title)}")
    messages.success(request, 'Шаг добавлен в задания. Результат запишите в дневник, когда сделаете.')
    return redirect('tasks', startup_id=startup.pk)


@login_required
def market(request, startup_id):
    startup = owned_startup(request, startup_id, edit=False)
    reports = startup.market_reports.all()
    current = reports.first()
    if request.GET.get('id'):
        try:
            current = get_object_or_404(reports, pk=UUID(request.GET['id']))
        except (ValueError, TypeError) as exc:
            raise Http404('Анализ не найден') from exc
    data = current.data if current else {}
    sources = current.sources if current else []
    open_axes = set(visible_tasks(startup.bruno_tasks).filter(origin='bruno',status=BrunoTask.Status.TODO).values_list('axis', flat=True))
    axis_labels = dict(BusinessAxis.choices)

    def source(index):
        return sources[index - 1] if isinstance(index, int) and 0 < index <= len(sources) else None

    return render(request, 'founder/market.html', {
        'startup': startup, 'workspace_tab': 'market', 'report': current, 'data': data,
        'relevance_label': market_service.RELEVANCE.get(data.get('relevance'), ''),
        'signals': [{**item, 'source_info': source(item.get('source'))} for item in data.get('demand_signals', [])],
        'competitors': [{**item, 'source_info': source(item.get('source'))} for item in data.get('competitors', [])],
        'checks': [{**check, 'index': index, 'axis_label': axis_labels.get(check['axis'], ''),
                    'axis_busy': check['axis'] in open_axes} for index, check in enumerate(data.get('checks', []))],
        'sources': [{**item, 'number': number} for number, item in enumerate(sources, 1)],
        'history': reports[:8], 'is_demo': settings.AI_PROVIDER == 'demo',
        'search_enabled': market_service.search_enabled() or settings.AI_PROVIDER == 'demo',
        'has_story': (any((startup.one_line_pitch, startup.problem, startup.solution, startup.target_customer))
                      or startup.chat_sessions.filter(mode=ChatSession.Mode.COFOUNDER, messages__role='user').exists()),
    })


@login_required
@require_POST
def market_generate(request, startup_id):
    startup = owned_startup(request, startup_id)
    try:
        market_service.create_market_report(startup)
        messages.success(request, 'Бруно изучил рынок по открытым источникам.')
    except AIServiceError as exc:
        messages.error(request, str(exc))
    return _workspace_return(request, startup, 'market', 'project-market')


@login_required
@require_POST
def market_check_task(request, startup_id, report_id, index):
    startup = owned_startup(request, startup_id)
    current = get_object_or_404(startup.market_reports, pk=report_id)
    try:
        task = market_service.check_to_task(current, index)
    except ValueError as exc:
        raise Http404(str(exc)) from exc
    if task is None:
        messages.error(request, 'По этому направлению уже есть задание в работе. Сначала завершите или отложите его.')
        return redirect(f"{reverse('market', args=[startup.pk])}?id={current.pk}")
    messages.success(request, 'Проверка добавлена в задания. Результат запишите в дневник, когда сделаете.')
    return redirect('tasks', startup_id=startup.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def task_estimate(request, startup_id, task_id):
    from founder.feature_forms import TaskEstimateForm
    from founder.models import TaskEstimate
    from founder.services.mvp import board
    from founder.services.work_priorities import linked_features
    from founder.services.priority_estimates import form_initial, resolve_estimate

    startup = owned_startup(request, startup_id)
    task = get_object_or_404(visible_tasks(startup.bruno_tasks).select_related('estimate'), pk=task_id)
    method = 'ice' if (request.POST.get('method') or request.GET.get('method')) == 'ice' else 'rice'
    feature = linked_features(startup, board(startup, method)).get(str(task.pk))
    if feature:
        return redirect(reverse('feature_edit', args=[startup.pk, feature.pk]) + f'?method={method}')
    estimate = getattr(task, 'estimate', None) or TaskEstimate(task=task)
    preview = resolve_estimate(estimate)
    form = TaskEstimateForm(request.POST if request.method == 'POST' else None, instance=estimate,
                            initial=form_initial(preview) if request.method == 'GET' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            BrunoTask.objects.select_for_update().get(pk=task.pk)
            TaskEstimate.objects.update_or_create(task=task, defaults=form.cleaned_data)
            activity.log(startup, request.user, activity.Kind.TASK,
                         'Оценки задания: ' + activity.quoted(task.title))
        messages.success(request, 'Оценки сохранены. RICE и ICE пересчитаны.')
        return redirect(reverse('tasks', args=[startup.pk]) + f'?method={method}#priority-task-{task.pk}')
    return render(request, 'founder/task_estimate.html', {
        'startup': startup, 'workspace_tab': 'tasks', 'task': task, 'form': form, 'mvp_method': method,
        'estimate_preview': preview,
    }, status=400 if request.method == 'POST' else 200)


@login_required
@require_POST
def estimates_generate(request, startup_id):
    from django.db import OperationalError
    from founder.services.bruno_estimates import generate_estimates
    from founder.services.request_limits import (RequestLimitExceeded, acquire_ai_lease, consume_limit,
                                                 release_ai_lease)

    startup = owned_startup(request, startup_id)
    method = 'ice' if request.POST.get('method') == 'ice' else 'rice'
    token = None
    try:
        token = acquire_ai_lease(request.user.pk, ttl_seconds=600)
        consume_limit(f'ai-minute:{request.user.pk}', settings.AI_REQUESTS_PER_MINUTE, 60)
        consume_limit(f'ai-day:{request.user.pk}', settings.AI_REQUESTS_PER_DAY, 86400)
        count = generate_estimates(startup, refresh=request.POST.get('refresh') == '1')
        if count:
            activity.log(startup, request.user, activity.Kind.TASK, f'Бруно предложил оценки: {count}')
            messages.success(request, f'Бруно оценил {count} задач и функций. Баллы рассчитаны; ваши числа сохранены.')
        else:
            messages.info(request, 'Для всех пунктов уже есть оценки. Их можно уточнить вручную.')
    except (AIServiceError, RequestLimitExceeded) as exc:
        messages.error(request, str(exc))
    except OperationalError:
        messages.error(request, 'Проект занят другим запросом. Предыдущие оценки сохранены; попробуйте позже.')
    finally:
        if token is not None:
            release_ai_lease(request.user.pk, token)
    return redirect(reverse('tasks', args=[startup.pk]) + f'?method={method}#task-priorities')

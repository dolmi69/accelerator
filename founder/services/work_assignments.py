"""One responsible teammate per task; shared by the plan and team overview."""
from urllib.parse import urlencode
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from founder.models import BrunoTask, ProjectMember
from founder.services.access import assignable_users
from founder.services.kano_availability import visible_tasks

STATUS_OPTIONS = (
    ('all', 'Все статусы'),
    ('todo', 'В работе'),
    ('done', 'Завершены'),
    ('skipped', 'Отложены'),
)


def selected_status(request):
    value = request.GET.get('status', 'all')
    if value not in dict(STATUS_OPTIONS):
        raise Http404('Статус заданий не найден')
    return value


def selected_filter(request, people):
    value = request.GET.get('assignee', 'mine' if request.GET.get('mine') == '1' else 'all')
    allowed = {'all', 'mine', 'unassigned', *(str(user.pk) for user in people)}
    if value not in allowed:
        raise Http404('Исполнитель не найден в команде')
    return value


def filter_query(value, method='rice', status='all'):
    return urlencode({'method':method, **({'mine':1} if value == 'mine' else {'assignee':value} if value != 'all' else {}),
                      **({'status': status} if status != 'all' else {})})


def filter_board(priorities, tasks, value, user, status='all'):
    def match(task):
        if value == 'all':
            return True
        if value == 'unassigned':
            return task is None or task.assignee_id is None
        return task is not None and str(task.assignee_id) == str(user.pk if value == 'mine' else value)
    entries = [entry for entry in priorities['priority_entries']
               if status in ('all', 'todo') and match(entry['task'])]
    for rank, entry in enumerate(entries, 1):
        entry['rank'] = rank
    rated = [entry for entry in entries if entry['score'] is not None]
    preliminary = sum(entry['score_preliminary'] for entry in entries)
    return {**priorities, 'priority_entries':entries, 'priority_count':len(entries),
        'priority_rated_count':len(rated), 'priority_preliminary_count':preliminary,
        'priority_saved_count':len(rated) - preliminary,
        'priority_needs_bruno':sum(entry['preview'].needs_bruno for entry in entries),
        'priority_first':next((entry for entry in entries if entry['priority'] != 'wont'), None)}, [
            task for task in tasks if status in ('all', 'todo') and match(task)]


def filter_history(tasks, value, user, status='all'):
    if status == 'todo':
        return tasks.none()
    if status in ('done', 'skipped'):
        tasks = tasks.filter(status=status)
    if value == 'mine':
        return tasks.filter(assignee=user)
    if value == 'unassigned':
        return tasks.filter(assignee__isnull=True)
    if value != 'all':
        return tasks.filter(assignee_id=value)
    return tasks


def ensure_feature_task(startup, feature):
    """Caller locks the project. Assignment is an explicit decision to take the feature on."""
    if feature.task_id:
        return get_object_or_404(visible_tasks(startup.bruno_tasks).select_for_update(), pk=feature.task_id, status='todo')
    task = BrunoTask.objects.create(startup=startup, origin='mvp', axis='product', title=feature.title,
        instructions=feature.description,
        success_criterion='Функция работает в основном пользовательском сценарии; результат проверки сохранён.')
    feature.task = task
    feature.save(update_fields=['task', 'updated_at'])
    return task


def assignment_person(startup, value):
    if not value:
        return None
    person = next((person for person in assignable_users(startup) if str(person.pk) == str(value)), None)
    if person is None:
        raise Http404('Назначать можно владельца и активных соавторов проекта')
    return person


def team_workload(startup):
    members = list(startup.members.filter(status=ProjectMember.Status.ACTIVE).select_related('user'))
    people = [startup.owner, *(member.user for member in members)]
    tasks = visible_tasks(startup.bruno_tasks)
    counts = {row['assignee_id']:row for row in tasks.values('assignee_id').annotate(
        opened=Count('id', filter=Q(status='todo')), done=Count('id', filter=Q(status='done')))}
    editors = {startup.owner_id, *(member.user_id for member in members if member.role == 'editor')}
    rows = {}
    for person in people:
        count = counts.get(person.pk, {})
        rows[person.pk] = {'opened':count.get('opened', 0), 'done':count.get('done', 0),
            'url':reverse('tasks', args=[startup.pk]) + '?' + filter_query(str(person.pk)) + '#task-priorities' if person.pk in editors else ''}
    # Unstarted features are also visible in the comparison queue.
    unassigned = counts.get(None, {}).get('opened', 0) + startup.feature_candidates.filter(task__isnull=True).count()
    return {'workload_by_user':rows, 'unassigned_count':unassigned,
        'unassigned_url':reverse('tasks', args=[startup.pk]) + '?' + filter_query('unassigned') + '#task-priorities',
        'assigned_count':sum(row['opened'] for key,row in counts.items() if key is not None)}

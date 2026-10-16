"""Saved project steps with provenance, immutable revisions and conservative checks."""
from datetime import timedelta
import hashlib
import json
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from founder.models import BrunoTask, EvidenceEntry, EvidenceRevision, StartupProfile
from founder.services.lab_reply import saved_reply, PageFacts

SNAPSHOT_FIELDS = ('claim', 'observation', 'axis', 'source', 'source_url', 'outcome', 'milestone', 'next_question', 'progress')


def task_snapshot(task):
    return {key: str(getattr(task, key)) for key in ('pk', 'title', 'instructions', 'success_criterion', 'axis')}


def task_digest(task):
    return hashlib.sha256(json.dumps(task_snapshot(task), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def snapshot(entry, author=None):
    EvidenceEntry.objects.select_for_update().get(pk=entry.pk)
    data = {key: getattr(entry, key) for key in SNAPSHOT_FIELDS}
    data.update(observed_on=entry.observed_on.isoformat(), origin=entry.origin,
                task=str(entry.task_id or ''), lab_version=str(entry.lab_version_id or ''))
    last = entry.revisions.first()
    if last and last.snapshot == data:
        return last
    return EvidenceRevision.objects.create(entry=entry, author=author,
        number=last.number + 1 if last else 1, snapshot=data)


def finish_task(entry, user):
    """Only called after explicit human confirmation or an independently checked change."""
    from founder.services.coins import award, Kind
    earned = 0
    if entry.task_id and BrunoTask.objects.filter(pk=entry.task_id, startup=entry.startup, status='todo').update(
            status='done', completed_at=timezone.now()):
        earned += award(user, Kind.TASK, key=entry.task_id, startup=entry.startup, note=entry.claim)
    return earned


def check_task(task, version):
    # Reports written by the generating model are never execution receipts.
    # A narrowly defined local text replacement can be proven from the actual sources.
    from founder.services.site_editor import text_command
    if task and version.source_id and version.edit_method == 'local':
        requested = text_command(task.instructions)
        applied = text_command(version.prompt)
        if requested and applied == requested:
            old, new = requested
            criterion = ' '.join(task.success_criterion.translate(str.maketrans('', '', '«»\"\'')).casefold().strip(' .!').split())
            expected = {f'надпись {new} вместо {old}'.casefold(), f'текст {new} вместо {old}'.casefold(), f'{new} вместо {old}'.casefold()}
            text = ' '.join(PageFacts(version.html).text)
            before = ' '.join(PageFacts(version.source.html).text)
            if criterion in expected and old in before and old not in text and new in text:
                return {'status': 'verified', 'checks': [f'В исходнике подтверждена замена «{old}» на «{new}».'],
                        'limitations': ['Внешний вид и поведение в браузере отдельно не проверялись.']}
    return {'status': 'needs_check', 'checks': ['Новая версия сайта сохранена.'],
            'limitations': ['Пользовательский сценарий и критерий готовности ещё нужно проверить в работающем сайте.']}


@transaction.atomic
def record_lab_result(request, startup, source, version):
    """Called by the generation pipeline, also when no browser remains connected."""
    if source and source.pk == version.pk:
        return None
    StartupProfile.objects.select_for_update().get(pk=startup.pk)
    existing = EvidenceEntry.objects.filter(lab_version=version).first()
    if existing:
        return existing
    task_id = request.POST.get('task_context')
    task = startup.bruno_tasks.select_for_update().filter(pk=task_id).first() if task_id else None
    expected = request.POST.get('_task_digest')
    unchanged = bool(task and expected and expected == task_digest(task))
    report = saved_reply(version)
    checks = check_task(task if unchanged else None, version)
    if task and not unchanged:
        checks['limitations'].append('Условия задания изменились после отправки запроса; результат требует сверки.')
    if report.get('failed'):
        checks['status'] = 'needs_check'
        checks['limitations'].append('Генерация завершена частично.')
    reported = [str(item)[:700] for item in [*report.get('items', []), *report.get('more', [])]][:20]
    actual_changes = []
    if source:
        if source.html != version.html:
            actual_changes.append('Обновлён интерфейс сайта')
        if source.backend_files != version.backend_files:
            actual_changes.append('Изменены файлы сервера')
        if source.presentation != version.presentation:
            actual_changes.append('Обновлены настройки оформления')
    else:
        actual_changes.append('Создана первая версия сайта')
    if not actual_changes:
        actual_changes.append('Сохранена новая версия; содержимое совпадает с предыдущей')
    title = task.title if task else 'Новая версия сайта'
    entry = EvidenceEntry.objects.create(startup=startup, task=task, author=request.user, origin='lab',
        lab_version=version, axis=task.axis if task else 'product', claim=title[:300],
        observation=('Отчёт Бруно по версии:\n' + '\n'.join(reported or ['Версия сохранена.']))[:4000],
        source='Сохранённая версия лаборатории', outcome='unclear',
        next_question='' if checks['status'] == 'verified' else (task.success_criterion if task else 'Проверить основной сценарий сайта')[:500],
        progress={'verification': checks['status'], 'checks': checks['checks'], 'limitations': checks['limitations'],
            'changes': actual_changes, 'report_model': version.model, 'report_label': 'Отчёт модели по исходнику',
            'task_snapshot': task_snapshot(task) if task else {}, 'task_matches_request': unchanged, 'source_version': str(source.pk) if source else '',
            'learned': '', 'next_step': '', 'completed': checks['status'] == 'verified'})
    if checks['status'] == 'verified' and unchanged:
        finish_task(entry, request.user)
    snapshot(entry, request.user)
    return entry


def decorate(entry):
    data = entry.progress
    entry.short_id = str(entry.pk).split('-')[0]
    entry.verification = data.get('verification', 'reported')
    entry.verification_label = {'verified': 'Проверено в исходнике', 'needs_check': 'Нужна проверка',
        'reported': 'Со слов участника', 'confirmed': 'Проверено участником'}.get(entry.verification, 'Со слов участника')
    entry.author_label = entry.author.public_name if entry.author_id else 'Участник проекта'
    entry.version_url = reverse('lab', args=[entry.startup_id]) + '?version=' + str(entry.lab_version_id) if entry.lab_version_id else ''
    entry.can_verify = bool(entry.origin == 'lab' and entry.verification == 'needs_check' and
        entry.task_id and entry.task.status == 'todo' and data.get('task_matches_request', True))
    return entry


def history_context(startup):
    entries = startup.evidence_entries.all()
    week = entries.filter(created_at__gte=timezone.now() - timedelta(days=7))
    return {'history_total': entries.count(), 'history_week': week.count(),
        'history_lab': week.filter(origin='lab').count(), 'history_people': week.filter(origin='manual').count(),
        'history_pending': entries.filter(origin='lab', progress__verification='needs_check').count(),
        'history_latest': next((decorate(e) for e in entries.select_related('author', 'task', 'lab_version').order_by('-created_at')[:1]), None)}

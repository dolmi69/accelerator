"""Independent evidence states. No TRL score and no AI on page views."""
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from founder.models import EvidenceEntry, LabTestSession, ProjectMember, ProjectPicture


FOCUS_LABELS = {
    'demand': 'Проверить потребность',
    'product': 'Проверить продукт',
    'revenue': 'Проверить оплаты и возврат',
}


def readiness_context(startup):
    diary_url = reverse('evidence_list', args=[startup.pk])
    lab_url = reverse('lab', args=[startup.pk])
    facts = ProjectPicture.objects.filter(startup=startup).values_list('facts', flat=True).first()
    facts = facts if isinstance(facts, dict) else {}

    def described(field, topic):
        item = facts.get(topic)
        return bool(getattr(startup, field).strip() or (
            isinstance(item, dict) and item.get('status') in ('fact', 'guess')
            and str(item.get('text') or '').strip()
        ))

    concept = all(described(field, topic) for field, topic in (
        ('problem', 'problem'), ('solution', 'solution'), ('target_customer', 'customer'),
    ))
    versions = startup.lab_versions.exclude(html__regex=r'^\s*$')
    prototype = versions.values('id').first()
    team = startup.members.filter(Q(status=ProjectMember.Status.ACTIVE) | Q(joined_at__isnull=False))
    tests = (LabTestSession.objects.filter(
        version__in=versions, finished_at__isnull=False, duration_seconds__gt=0,
    ).exclude(tester_id=startup.owner_id).exclude(tester_id__in=team.values('user_id'))
        .exclude(feedback__regex=r'^\s*$'))
    testers = tests.values('tester_id').distinct().count()
    # Project-scoped, dated, sourced observations. Retain negative/unclear
    # results rather than letting a historical success hide contradictions.
    entries = startup.evidence_entries.filter(observed_on__lte=timezone.localdate()).exclude(
        milestone='',
    ).exclude(observation__regex=r'^\s*$').filter(
        ~Q(source__regex=r'^\s*$') | ~Q(source_url__regex=r'^\s*$'),
    )
    groups = {
        'demand': [EvidenceEntry.Milestone.DEMAND],
        'product': [EvidenceEntry.Milestone.PILOT],
        'revenue': [EvidenceEntry.Milestone.PAYMENT, EvidenceEntry.Milestone.REPEAT_PAYMENTS,
                    EvidenceEntry.Milestone.RETENTION],
    }
    saved = list(entries.values('id', 'milestone', 'outcome', 'claim', 'observed_on'))
    states = []
    for key, milestones in groups.items():
        records = [entry for entry in saved if entry['milestone'] in milestones]
        supported = [entry for entry in records if entry['outcome'] == 'supported']
        refuted = [entry for entry in records if entry['outcome'] == 'refuted']
        status, label = 'unknown', 'Нет данных'
        detail = 'Сохраните результат проверки и укажите, откуда сведения.'
        if records:
            status, label = 'unclear', 'Недостаточно данных'
        if supported:
            status, label = 'signal', 'Есть положительный результат'
        if refuted:
            status, label = 'refuted', 'Проверка не подтвердила гипотезу'
        if refuted and supported:
            status, label = 'mixed', 'Есть противоречия'
        if key == 'product' and not records:
            if prototype:
                status, label = 'prototype', 'Прототип сохранён'
                detail = 'Сохранённый сайт ещё не доказывает, что основной сценарий работает.'
            if testers:
                status, label = 'feedback', 'Есть отзывы о прототипе'
                detail = 'Отзыв показывает, что тест состоялся. Успешность сценария нужно проверить отдельно.'
        if key == 'revenue' and supported and not refuted:
            repeat = any(entry['milestone'] == 'repeat_payments' for entry in supported)
            payment = any(entry['milestone'] in ('payment', 'repeat_payments') for entry in supported)
            retention = any(entry['milestone'] == 'retention' for entry in supported)
            label = 'Записаны повторные оплаты' if repeat else 'Записана первая оплата' if payment else 'Записаны возвраты пользователей'
            if payment and retention:
                label += ' и возвраты'
        if records:
            detail = 'По записям основателя с указанным источником. Платформа не проверяет оплату или интервью автоматически.'
        links = []
        # One link for each observed outcome, bounded independently of history size.
        for outcome in ('supported', 'refuted', 'unclear'):
            entry = next((entry for entry in records if entry['outcome'] == outcome), None)
            if entry:
                links.append({**entry, 'url': f'{diary_url}#entry-{entry["id"]}',
                              'outcome_label': dict(EvidenceEntry.Outcome.choices)[outcome]})
        states.append({'key': key, 'title': {'demand': 'Потребность', 'product': 'Продукт',
                       'revenue': 'Оплаты и возврат'}[key], 'status': status, 'label': label,
                       'description': detail, 'sources': links, 'count': len(records),
                       'supported': bool(supported), 'refuted': bool(refuted)})

    by_key = {item['key']: item for item in states}
    if not by_key['demand']['supported'] or by_key['demand']['refuted']:
        suggested = 'demand'
    elif not by_key['product']['supported'] or by_key['product']['refuted']:
        suggested = 'product'
    else:
        suggested = 'revenue'
    selected = startup.work_focus if startup.work_focus in FOCUS_LABELS else suggested
    next_description = {
        'demand': 'Уточните, кому нужна ваша идея. Проведите проверку с потенциальными клиентами и запишите также отказы и сомнения.',
        'product': 'Дайте человеку вне команды пройти главный сценарий. Запишите, что получилось, где он остановился и почему.',
        'revenue': 'Проверьте готовность платить и возвращаться. Запишите реальные суммы, период и источник; намерение купить храните как гипотезу.',
    }[selected]
    return {
        'states': states, 'concept': concept, 'prototype': bool(prototype), 'testers': testers,
        'prototype_url': f'{lab_url}?version={prototype["id"]}' if prototype else lab_url,
        'test_url': lab_url + '?section=globalization', 'focus': selected,
        'focus_label': FOCUS_LABELS[selected], 'manual_focus': startup.work_focus != 'auto',
        'next_description': next_description,
        'action_url': reverse('evidence_create', args=[startup.pk]) + '?milestone=' + {
            'demand': 'demand', 'product': 'pilot', 'revenue': 'payment',
        }[selected], 'action_label': 'Записать проверку',
        'title': 'Есть прототип' if prototype else 'Проверяем идею',
    }

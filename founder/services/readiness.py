"""Platform readiness milestones, computed from saved records without AI.

This is a product/business scale, not certification under an engineering TRL
standard. Diary claims remain founder-reported, even when a source is supplied.
"""
from django.db.models import OuterRef, Q, Subquery
from django.urls import reverse
from django.utils import timezone

from founder.models import EvidenceEntry, LabTestSession, ProjectMember, ProjectPicture


STAGES = (
    ('Идея', 'Создан проект с названием.'),
    ('Идея сформулирована', 'Описаны клиент, его проблема и ваше решение.'),
    ('Есть спрос', 'Сохранён положительный результат проверки спроса с источником.'),
    ('Есть прототип', 'В лаборатории сохранена версия сайта.'),
    ('Первый внешний тест', 'Один человек вне команды завершил тест и оставил отзыв.'),
    ('Проверен пользователями', 'Три разных человека вне команды завершили тесты и оставили отзывы.'),
    ('Пилот с клиентом', 'Есть прототип и запись об успешном пилоте с источником.'),
    ('Первая оплата', 'Есть прототип и запись о полученной оплате с источником.'),
    ('Повторные оплаты', 'Есть прототип и запись о повторных оплатах: числа, период и источник.'),
)

NEXT_ACTIONS = {
    2: 'Опишите, кто ваш клиент, какую проблему вы решаете и как работает решение.',
    3: 'Проверьте спрос у потенциальных клиентов. Сохраните результат и его источник в дневнике.',
    4: 'Создайте и сохраните первую версию сайта в лаборатории.',
    5: 'Опубликуйте прототип в карточке проекта и получите завершённый тест с отзывом от человека вне команды.',
    6: 'Соберите завершённые тесты с отзывами от трёх разных людей вне команды.',
    7: 'Проведите пилот с реальным клиентом и запишите результат вместе с источником.',
    8: 'Запишите полученную оплату: сумму, дату и источник. Планы продаж пока не учитываются.',
    9: 'Запишите повторные оплаты: количество, период и источник результата.',
}


def readiness_context(startup):
    """Highest recorded milestone; missing earlier evidence stays visible.

    A working prototype may exist before demand was checked. Do not pretend that
    reaching level 4 confirms demand, or award points for a high radar score.
    Only small projections leave the database; prototype HTML is never loaded.
    """
    diary_url = reverse('evidence_list', args=[startup.pk])
    lab_url = reverse('lab', args=[startup.pk])
    today = timezone.localdate()
    picture = ProjectPicture.objects.filter(startup=startup).values_list('facts', flat=True).first()
    facts = picture if isinstance(picture, dict) else {}

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
    # An interrupted first generation's working Django base is still a prototype.
    prototype = versions.values('id').first()
    # Include invitations and past membership dates to avoid counting teammates
    # as outside testers just because their current role changed.
    team = startup.members.filter(Q(status=ProjectMember.Status.ACTIVE) | Q(joined_at__isnull=False))
    tests = (LabTestSession.objects.filter(
        version__in=versions, finished_at__isnull=False, duration_seconds__gt=0,
    ).exclude(tester_id=startup.owner_id).exclude(tester_id__in=team.values('user_id'))
        .exclude(feedback__regex=r'^\s*$'))
    testers = tests.values('tester_id').distinct().count()
    entries = startup.evidence_entries.filter(
        outcome=EvidenceEntry.Outcome.SUPPORTED, observed_on__lte=today,
    ).exclude(milestone='').exclude(observation__regex=r'^\s*$').filter(
        ~Q(source__regex=r'^\s*$') | ~Q(source_url__regex=r'^\s*$'),
    )
    proofs = {}
    linked_entry = entries.filter(milestone=OuterRef('milestone')).values('id')[:1]
    for entry in entries.order_by().values('milestone').annotate(entry_id=Subquery(linked_entry)).distinct():
        # Four linked examples at most, regardless of diary size.
        proofs[entry['milestone']] = entry['entry_id']
    milestone = EvidenceEntry.Milestone
    payment = proofs.get(milestone.PAYMENT) or proofs.get(milestone.REPEAT_PAYMENTS)
    pilot = proofs.get(milestone.PILOT)
    demand = proofs.get(milestone.DEMAND) or pilot or payment
    passed = (bool(startup.name.strip()), concept, bool(demand), bool(prototype),
              testers >= 1, testers >= 3, bool(prototype and pilot),
              bool(prototype and payment), bool(prototype and proofs.get(milestone.REPEAT_PAYMENTS)))
    level = max((index for index, ready in enumerate(passed, 1) if ready), default=1)
    sources = (None, None, demand, None, None, None, pilot, payment, proofs.get(milestone.REPEAT_PAYMENTS))
    steps = []
    for number, ((title, criterion), ready, source) in enumerate(zip(STAGES, passed, sources), 1):
        url = diary_url + f'#entry-{source}' if source else (
            lab_url + f'?version={prototype["id"]}' if number == 4 and prototype
            else lab_url + '?section=globalization' if number in (5, 6) else ''
        )
        if number in (1, 2):
            url = reverse('dashboard', args=[startup.pk]) + '#bruno-conversations'
        steps.append({'number': number, 'title': title, 'criterion': criterion,
                      'ready': ready, 'current': number == level, 'url': url,
                      'source_label': 'Запись основателя' if source else 'Открыть прототип' if number == 4 else 'Посмотреть тесты' if number in (5, 6) else 'Обсудить с Бруно'})
    next_level = min(level + 1, 9)
    next_step = steps[next_level - 1]
    action_url = reverse('evidence_create', args=[startup.pk]) + '?milestone=' + {
        3: milestone.DEMAND, 7: milestone.PILOT, 8: milestone.PAYMENT, 9: milestone.REPEAT_PAYMENTS,
    }.get(next_level, '')
    action_label = 'Записать результат'
    if next_level == 2:
        action_url, action_label = reverse('startup_edit', args=[startup.pk]), 'Описать идею'
    elif next_level in (4, 5, 6):
        action_url = lab_url + ('?section=globalization' if next_level in (5, 6) else '')
        action_label = 'Открыть лабораторию' if next_level == 4 else 'К тестированию'
    if level == 9:
        action_url, action_label = diary_url, 'Открыть результаты'
    return {'level': level, 'title': STAGES[level - 1][0], 'steps': steps,
            'testers': testers, 'next_level': next_level, 'next_title': next_step['title'],
            'next_description': NEXT_ACTIONS[next_level] if level < 9 else 'Продолжайте сохранять результаты работы с клиентами.',
            'action_url': action_url, 'action_label': action_label,
            'missing_earlier': any(not ready for ready in passed[:level - 1])}

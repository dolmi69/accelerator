"""Read-only usage report, including failed attempts that have no saved version."""
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.utils import timezone
from founder.models import LabAIUsage


class Command(BaseCommand):
    help = 'Расход лаборатории за месяц: фактические токены, резерв и расчёт цены подписки.'

    def add_arguments(self, parser):
        parser.add_argument('--user', type=int, help='ID пользователя; без него весь сервис.')
        parser.add_argument('--ai-budget', type=Decimal, help='Максимальный месячный AI-расход на пользователя, руб.')
        parser.add_argument('--hosting', type=Decimal, default=Decimal('0'), help='Другие месячные затраты на пользователя, руб.')
        parser.add_argument('--fee-percent', type=Decimal, default=Decimal('0'), help='Комиссия платежей, процент.')
        parser.add_argument('--margin-percent', type=Decimal, default=Decimal('30'), help='Целевая доля выручки после указанных затрат.')

    def handle(self, *args, **options):
        date = timezone.localdate()
        rows = LabAIUsage.objects.filter(created_at__year=date.year, created_at__month=date.month)
        if options['user'] is not None:
            rows = rows.filter(user_id=options['user'])
        total = rows.aggregate(cost=Sum('accounted_micro_rub'), input=Sum('input_tokens'), output=Sum('output_tokens'))
        self.stdout.write(f"Месяц: {date:%Y-%m}; запросов: {rows.count()}; учтено: {Decimal(total['cost'] or 0)/1_000_000:.2f} ₽")
        self.stdout.write(f"Подтверждённые токены: вход {total['input'] or 0}, выход {total['output'] or 0}")
        self.stdout.write(f"Без подтверждённого расхода: {rows.filter(status__in=['unknown','reserved']).count()}; их резерв включён в сумму.")
        if options['ai_budget'] is not None:
            budget, hosting, fee, margin = (options[key] for key in ['ai_budget','hosting','fee_percent','margin_percent'])
            if any(not value.is_finite() or value < 0 for value in [budget,hosting,fee,margin]) or fee + margin >= 100:
                raise CommandError('Затраты должны быть неотрицательными; комиссии плюс маржа — меньше 100%.')
            price = (budget + hosting) / (1 - (fee + margin)/100)
            self.stdout.write(f'Расчётная цена подписки: от {price:.2f} ₽/мес. Только по введённым затратам; налоги и поддержку добавьте в расчёт.')

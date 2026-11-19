"""Pro billing periods and website laboratory credits."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from founder.models import LabPlanPeriod, User
from founder.services import lab_models

# One credit represents one Luna input token at the plan's base rate.
PRO_LAB_UNITS = 10_000_000
MICRO_RUB_PER_UNIT = 10
_plan_request = ContextVar('lab_plan_request', default=None)


class PlanBalanceError(Exception):
    pass


@dataclass
class LabPlanRequest:
    period: LabPlanPeriod
    introductory: bool
    using_intro: bool = False


def is_pro(user):
    until = getattr(user, 'pro_until', None)
    return bool(getattr(user, 'is_authenticated', False) and until and until > timezone.now())


def quota(user, name):
    if name != 'lab_requests_per_day':
        raise ValueError('Unknown plan quota')
    return 0 if is_pro(user) else settings.LAB_REQUESTS_PER_DAY


def _current_period(user, now):
    return LabPlanPeriod.objects.filter(user=user, starts_at__lte=now, ends_at__gt=now).first()


def lab_period(user):
    if not is_pro(user):
        return None
    now = timezone.now()
    current = _current_period(user, now)
    if current:
        return current
    # Existing Pro users and access granted by an administrator had no ledger.
    with transaction.atomic():
        account = User.objects.select_for_update().get(pk=user.pk)
        now = timezone.now()
        if not account.pro_until or account.pro_until <= now:
            return None
        current = _current_period(account, now)
        if current:
            return current
        return LabPlanPeriod.objects.create(user=account, starts_at=now, ends_at=account.pro_until,
            included_units=PRO_LAB_UNITS)


@transaction.atomic
def activate_pro(user, days):
    account = User.objects.select_for_update().get(pk=user.pk)
    now = timezone.now()
    current_end = account.pro_until
    if current_end and current_end > now:
        # Keep the current allowance when a legacy subscriber renews early.
        if not _current_period(account, now):
            LabPlanPeriod.objects.create(user=account, starts_at=now, ends_at=current_end,
                included_units=PRO_LAB_UNITS)
        start = current_end
    else:
        start = now
    end = start + timedelta(days=days)
    LabPlanPeriod.objects.create(user=account, starts_at=start, ends_at=end, included_units=PRO_LAB_UNITS)
    account.pro_until = end
    account.save(update_fields=['pro_until'])
    user.pro_until = end
    return end


@contextmanager
def lab_plan_request(user, *, introductory=False):
    period = lab_period(user)
    request = LabPlanRequest(period, introductory) if period else None
    token = _plan_request.set(request)
    try:
        yield
    finally:
        _plan_request.reset(token)


def current_lab_plan_request():
    return _plan_request.get()


def lab_units(model, input_tokens, output_tokens, amount_micro_rub):
    if model == lab_models.LUNA:
        return input_tokens + 5 * output_tokens
    if model == lab_models.SOL:
        return 20 * (input_tokens + 5 * output_tokens)
    return (amount_micro_rub + MICRO_RUB_PER_UNIT - 1) // MICRO_RUB_PER_UNIT


def reserve_lab_units(request, units):
    """Run within the same transaction as the provider's ledger reservation."""
    period = LabPlanPeriod.objects.filter(pk=request.period.pk)
    if request.using_intro:
        return 0, True
    if request.introductory and period.filter(intro_used=False).update(intro_used=True):
        return 0, True
    if not period.filter(used_units__lte=F('included_units') - units).update(used_units=F('used_units') + units):
        raise PlanBalanceError('В Pro не хватает токенов на этот запрос. Попробуйте более короткую правку или откройте конструктор.')
    return units, False


def settle_lab_units(period_id, delta):
    if delta:
        LabPlanPeriod.objects.filter(pk=period_id).update(used_units=F('used_units') + delta)

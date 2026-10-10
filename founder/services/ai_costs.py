"""Conservative cost reservations, not customer payments or a provider invoice.

Prices are snapshotted per attempt. Unknown usage retains the entire reservation;
timeouts and process crashes cannot silently turn into free requests in our ledger.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import logging

from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from founder.models import LabAIUsage, LabSpendBucket

logger = logging.getLogger(__name__)
_scope = ContextVar("lab_billing_scope", default=(None, None, "code"))


class CostLimitError(Exception):
    pass


@contextmanager
def billing_scope(user, startup, operation):
    token = _scope.set((user.pk, startup.pk, operation))
    try:
        yield
    finally:
        _scope.reset(token)


def _decimal(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 1_000_000:
            raise InvalidOperation
        return amount
    except (InvalidOperation, ValueError, TypeError):
        raise CostLimitError("Проверьте настройки цен и бюджета генератора.") from None


def micro_rub(value):
    return int((_decimal(value) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def cost(input_tokens, output_tokens, input_price, output_price):
    # Token count × RUB per million = micro-RUB, rounded upwards.
    return int((input_tokens * input_price + output_tokens * output_price).to_integral_value(rounding=ROUND_CEILING))


def reserve(model, normalized, output_limit, *, pricing=None):
    if pricing is None:
        if model != settings.QWEN_PRICE_MODEL:
            raise CostLimitError("Для выбранной модели нужно настроить её собственный тариф QWEN_PRICE_MODEL.")
        pricing = (settings.QWEN_INPUT_RUB_PER_MILLION, settings.QWEN_OUTPUT_RUB_PER_MILLION)
    input_price, output_price = map(_decimal, pricing)
    if input_price == 0 or output_price == 0:
        raise CostLimitError("Тариф модели должен быть положительным.")
    # UTF-8 bytes + framing margin are deliberately conservative without a tokenizer.
    # This is not a measured token count or an absolute provider billing guarantee.
    input_bound = sum(len(m["content"].encode("utf-8")) + 128 for m in normalized) + 512
    if input_bound > settings.QWEN_INPUT_BYTE_LIMIT:
        raise CostLimitError("Слишком большой контекст. Выберите отдельный блок для правки или сократите описание.")
    amount = cost(input_bound, output_limit, input_price, output_price)
    if amount > micro_rub(settings.LAB_MAX_REQUEST_RUB):
        raise CostLimitError("Запрос превышает установленный бюджет одной генерации. Упростите задачу.")
    user_id, startup_id, operation = _scope.get()
    date = timezone.localdate()
    periods = [(f"global:day:{date}", settings.LAB_GLOBAL_DAILY_RUB),
               (f"global:month:{date:%Y-%m}", settings.LAB_GLOBAL_MONTHLY_RUB)]
    if user_id:
        periods.extend([(f"user:{user_id}:day:{date}", settings.LAB_USER_DAILY_RUB),
                        (f"user:{user_id}:month:{date:%Y-%m}", settings.LAB_USER_MONTHLY_RUB)])
    try:
        with transaction.atomic():
            for key, rub_limit in periods:
                LabSpendBucket.objects.get_or_create(key=key)
                limit = micro_rub(rub_limit)
                if amount > limit or not LabSpendBucket.objects.filter(pk=key, micro_rub__lte=limit-amount).update(micro_rub=F("micro_rub")+amount):
                    raise CostLimitError("Бюджет AI-генерации на этот период исчерпан. Правки без AI остаются доступны.")
            return LabAIUsage.objects.create(user_id=user_id, startup_id=startup_id, model=model,
                operation=operation, input_bound=input_bound, output_limit=output_limit,
                input_price=input_price, output_price=output_price, accounted_micro_rub=amount,
                bucket_keys=[key for key, _ in periods])
    except OperationalError:
        raise CostLimitError("Не удалось проверить бюджет. Повторите запрос позже.") from None


def settle(usage, *, input_tokens=None, output_tokens=None, status="unknown"):
    known = input_tokens is not None and output_tokens is not None
    amount = cost(input_tokens, output_tokens, usage.input_price, usage.output_price) if known else usage.accounted_micro_rub
    try:
        with transaction.atomic():
            # Only one settlement may refund a reservation.
            changed = LabAIUsage.objects.filter(pk=usage.pk, status="reserved").update(
                status=status if known else "unknown", input_tokens=input_tokens, output_tokens=output_tokens,
                accounted_micro_rub=amount, finished_at=timezone.now())
            if changed:
                delta = amount - usage.accounted_micro_rub
                for key in usage.bucket_keys:
                    LabSpendBucket.objects.filter(pk=key).update(micro_rub=F("micro_rub")+delta)
    except OperationalError:
        # The reservation stays charged. Never start a paid retry to recover this.
        logger.warning("Could not settle lab usage; reservation retained: %s", usage.pk)


def reject_output(request_id):
    if request_id:
        LabAIUsage.objects.filter(pk=request_id, status="success").update(status="output_error")


def usage_summary(user):
    date = timezone.localdate()
    rows = LabAIUsage.objects.filter(user=user, created_at__year=date.year, created_at__month=date.month)
    totals = rows.aggregate(input=Sum("input_tokens"), output=Sum("output_tokens"), cost=Sum("accounted_micro_rub"))
    return {"input_tokens": totals["input"] or 0, "output_tokens": totals["output"] or 0,
            "accounted_rub": Decimal(totals["cost"] or 0)/1_000_000,
            "uncertain": rows.filter(status__in=["unknown", "reserved"]).count(),
            "attempts": rows.count(), "monthly_limit": _decimal(settings.LAB_USER_MONTHLY_RUB)}

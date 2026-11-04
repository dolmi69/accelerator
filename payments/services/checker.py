"""Сверка пополнений с блокчейном: находим оплату на основной адрес и зачисляем.

Все пополнения приходят на ОДИН адрес, поэтому платёж узнаётся только по сумме.
Каждому счёту при создании добавляется уникальный «хвост» в миллионных долях
(10 USDT превращаются, например, в 10.004213), и перевод зачисляется счёту лишь
при точном совпадении суммы, если он отправлен в срок действия этого счёта.
Совпадение с несколькими счетами считается неоднозначным и вручную разбирается
по журналу: автоматически деньги не зачисляются никому.

Подтверждения считаем по времени перевода (блок Tron ~3 с), т.к. POST-эндпоинты
TronGrid для номера блока с этой машины недоступны.

Идемпотентно: счёт помечается оплаченным условным UPDATE, и баланс растёт только
у того прогона, который этот UPDATE выполнил; txid зачисленных переводов
повторно не учитывается.
"""

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from payments.models import Balance, Invoice
from payments.services import hd, tron

logger = logging.getLogger(__name__)

QUANT = Decimal("0.000001")
# Пользователь вводит сумму с точностью до копейки, хвост занимает 3–6 знаки после запятой.
AMOUNT_STEP = Decimal("0.01")
TAIL_MAX = 9999
TAIL_ATTEMPTS = 30
# Часы TronGrid и сервера могут расходиться: небольшой запас до создания счёта.
CLOCK_SKEW = timedelta(minutes=2)
# Перевод, отправленный в срок, может набрать подтверждения уже после истечения счёта.
LATE_CONFIRMATION_WINDOW = timedelta(days=1)


def _to_usdt(raw_value) -> Decimal:
    return (Decimal(str(raw_value)) / Decimal(10**tron.USDT_DECIMALS)).quantize(QUANT)


def _confirmations(block_timestamp_ms) -> int:
    """Оценка числа подтверждений по возрасту перевода (блок Tron ~3 секунды)."""
    if not block_timestamp_ms:
        return 0
    elapsed_ms = int(time.time() * 1000) - int(block_timestamp_ms)
    return max(elapsed_ms // (tron.TRON_BLOCK_SECONDS * 1000), 0)


def _sent_at(block_timestamp_ms):
    if not block_timestamp_ms:
        return None
    return datetime.fromtimestamp(int(block_timestamp_ms) / 1000, tz=dt_timezone.utc)


def unique_amount(amount: Decimal):
    """Сумма к оплате с уникальным хвостом среди открытых счетов; None, если свободных хвостов не нашлось."""
    base = amount.quantize(AMOUNT_STEP)
    taken = set(
        Invoice.objects.filter(
            status=Invoice.Status.PENDING, amount_expected__gt=base, amount_expected__lt=base + AMOUNT_STEP,
        ).values_list("amount_expected", flat=True)
    )
    for _ in range(TAIL_ATTEMPTS):
        candidate = (base + QUANT * (secrets.randbelow(TAIL_MAX) + 1)).quantize(QUANT)
        if candidate not in taken:
            return candidate
    return None


def credit_balance(invoice: Invoice) -> None:
    """Увеличивает баланс пользователя на зачисленную сумму."""
    if invoice.user_id is None:
        return
    balance = Balance.for_user(invoice.user)
    Balance.objects.filter(pk=balance.pk).update(amount=F("amount") + invoice.amount_received)


def _mark_paid(invoice: Invoice, txid: str, value: Decimal, confirmations: int, now) -> bool:
    """Помечает счёт оплаченным и пополняет баланс ровно один раз, даже если поллеров два."""
    with transaction.atomic():
        updated = Invoice.objects.filter(
            pk=invoice.pk, txid="", status__in=[Invoice.Status.PENDING, Invoice.Status.EXPIRED],
        ).update(
            txid=txid, amount_received=value, confirmations=confirmations,
            status=Invoice.Status.PAID, paid_at=now,
        )
        if updated != 1:
            return False
        invoice.txid, invoice.amount_received, invoice.confirmations = txid, value, confirmations
        invoice.status, invoice.paid_at = Invoice.Status.PAID, now
        credit_balance(invoice)
    return True


def check_all() -> dict:
    """Проверяет входящие пополнения. Возвращает статистику прогона."""
    stats = {"checked": 0, "paid": 0, "errors": 0, "ambiguous": 0}
    now = timezone.now()

    Invoice.objects.filter(status=Invoice.Status.PENDING, expires_at__lt=now).update(
        status=Invoice.Status.EXPIRED
    )

    open_invoices = list(
        Invoice.objects.filter(
            status__in=[Invoice.Status.PENDING, Invoice.Status.EXPIRED],
            txid="",
            expires_at__gte=now - LATE_CONFIRMATION_WINDOW,
        ).order_by("created_at")
    )
    stats["checked"] = len(open_invoices)
    if not open_invoices:
        return stats

    try:
        address = hd.receiving_address()
        transfers = tron.fetch_incoming_usdt(address)
    except (ImproperlyConfigured, tron.TronGridError):
        stats["errors"] = 1
        return stats

    credited = set(Invoice.objects.exclude(txid="").values_list("txid", flat=True))

    for tx in reversed(transfers):  # от старых к новым
        txid = tx.get("transaction_id")
        if not txid or txid in credited or tx.get("to") != address:
            continue
        try:
            value = _to_usdt(tx["value"])
            sent_at = _sent_at(tx.get("block_timestamp"))
        except (KeyError, InvalidOperation, TypeError, ValueError, OverflowError, OSError):
            continue
        if sent_at is None:
            continue

        matches = [
            inv for inv in open_invoices
            if inv.amount_expected == value and inv.created_at - CLOCK_SKEW <= sent_at <= inv.expires_at
        ]
        if len(matches) != 1:
            if matches:
                stats["ambiguous"] += 1
                logger.warning("USDT transfer %s matches %s invoices, left for manual review", txid, len(matches))
            continue
        invoice = matches[0]

        confirmations = _confirmations(tx.get("block_timestamp"))
        if confirmations < settings.PAYMENTS_REQUIRED_CONFIRMATIONS:
            continue

        if _mark_paid(invoice, txid, value, confirmations, now):
            credited.add(txid)
            open_invoices.remove(invoice)
            stats["paid"] += 1

    return stats

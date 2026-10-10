"""Сверка пополнений с блокчейном: находим оплату на основной адрес и зачисляем.

Все пополнения приходят на ОДИН адрес. Поллер берёт входящие переводы USDT,
сопоставляет их с ожидаемыми суммами (от старых к новым) и увеличивает баланс
пользователя. Подтверждения считаем по времени перевода (блок Tron ~3 с), т.к.
POST-эндпоинты TronGrid для номера блока с этой машины недоступны.

Идемпотентно: оплаченное пополнение выходит из выборки, а txid уже зачисленных
переводов повторно не учитывается.
"""

import time
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import F
from django.utils import timezone

from payments.models import Balance, Invoice
from payments.services import hd, tron

QUANT = Decimal("0.000001")


def _to_usdt(raw_value) -> Decimal:
    return (Decimal(str(raw_value)) / Decimal(10**tron.USDT_DECIMALS)).quantize(QUANT)


def _confirmations(block_timestamp_ms) -> int:
    """Оценка числа подтверждений по возрасту перевода (блок Tron ~3 секунды)."""
    if not block_timestamp_ms:
        return 0
    elapsed_ms = int(time.time() * 1000) - int(block_timestamp_ms)
    return max(elapsed_ms // (tron.TRON_BLOCK_SECONDS * 1000), 0)


def credit_balance(invoice: Invoice) -> None:
    """Увеличивает баланс пользователя на зачисленную сумму."""
    if invoice.user_id is None:
        return
    balance = Balance.for_user(invoice.user)
    Balance.objects.filter(pk=balance.pk).update(amount=F("amount") + invoice.amount_received)


def check_all() -> dict:
    """Проверяет входящие пополнения. Возвращает статистику прогона."""
    stats = {"checked": 0, "paid": 0, "errors": 0}
    now = timezone.now()

    Invoice.objects.filter(status=Invoice.Status.PENDING, expires_at__lt=now).update(
        status=Invoice.Status.EXPIRED
    )

    pending = list(Invoice.objects.filter(status=Invoice.Status.PENDING).order_by("created_at"))
    stats["checked"] = len(pending)
    if not pending:
        return stats

    try:
        address = hd.receiving_address()
        transfers = tron.fetch_incoming_usdt(address)
    except (ImproperlyConfigured, tron.TronGridError):
        stats["errors"] = 1
        return stats

    credited = set(Invoice.objects.exclude(txid="").values_list("txid", flat=True))
    remaining = {inv.id: inv for inv in pending}

    for tx in reversed(transfers):  # от старых к новым
        txid = tx.get("transaction_id")
        if not txid or txid in credited or tx.get("to") != address:
            continue

        value = _to_usdt(tx["value"])
        invoice = next((inv for inv in remaining.values() if inv.amount_expected <= value), None)
        if invoice is None:
            continue

        confirmations = _confirmations(tx.get("block_timestamp"))
        if confirmations < settings.PAYMENTS_REQUIRED_CONFIRMATIONS:
            continue

        invoice.txid = txid
        invoice.amount_received = value
        invoice.confirmations = confirmations
        invoice.status = Invoice.Status.PAID
        invoice.paid_at = now
        invoice.save(update_fields=["txid", "amount_received", "confirmations", "status", "paid_at"])
        credit_balance(invoice)

        credited.add(txid)
        remaining.pop(invoice.id, None)
        stats["paid"] += 1

    return stats

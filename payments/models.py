"""Пополнения на ОДИН основной адрес и баланс пользователя.

Все пополнения приходят на один адрес кошелька. Чтобы понять, кому зачислить,
каждому пополнению назначается ожидаемая сумма; поллер сверяет входящие
переводы с ожидаемыми суммами и увеличивает баланс пользователя.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


def default_order_id():
    return f"top_{uuid.uuid4().hex[:12]}"


class Balance(models.Model):
    """Баланс пользователя в USDT."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="balance"
    )
    amount = models.DecimalField("Баланс, USDT", max_digits=18, decimal_places=6, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Баланс"
        verbose_name_plural = "Балансы"

    def __str__(self):
        return f"{self.user}: {self.amount} USDT"

    @classmethod
    def for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    @property
    def amount_text(self):
        return format(Decimal(self.amount).normalize(), "f")


class Invoice(models.Model):
    """Одно пополнение баланса. Все идут на один основной адрес."""

    class Status(models.TextChoices):
        NEW = "new", "Создан"
        PENDING = "pending", "Ожидает оплату"
        PAID = "paid", "Оплачен"
        EXPIRED = "expired", "Истёк"
        CANCELED = "canceled", "Отменён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    order_id = models.CharField("Номер пополнения", max_length=64, unique=True, default=default_order_id)
    description = models.CharField("Назначение", max_length=255, blank=True, default="Пополнение баланса")

    network = models.CharField("Сеть", max_length=16, default="tron")
    asset = models.CharField("Актив", max_length=16, default="USDT")
    address = models.CharField("Адрес для оплаты", max_length=64)

    amount_expected = models.DecimalField("Сумма к оплате", max_digits=18, decimal_places=6)
    amount_received = models.DecimalField("Получено", max_digits=18, decimal_places=6, default=0)

    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.NEW)
    txid = models.CharField("Транзакция", max_length=128, blank=True)
    confirmations = models.PositiveIntegerField("Подтверждений", default=0)

    created_at = models.DateTimeField("Создан", default=timezone.now)
    expires_at = models.DateTimeField("Действует до")
    paid_at = models.DateTimeField("Оплачен", null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Пополнение"
        verbose_name_plural = "Пополнения"

    def __str__(self):
        return f"{self.order_id} · {self.amount_expected} {self.asset} · {self.get_status_display()}"

    @property
    def is_open(self):
        return self.status in {self.Status.NEW, self.Status.PENDING}

    @property
    def is_paid(self):
        return self.status == self.Status.PAID

    @property
    def amount_expected_text(self):
        """Сумма строкой с точкой и без хвостовых нулей (0.5, 10)."""
        return format(Decimal(self.amount_expected).normalize(), "f")

    @property
    def amount_received_text(self):
        return format(Decimal(self.amount_received).normalize(), "f")

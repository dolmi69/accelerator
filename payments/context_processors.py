"""Показывает баланс пользователя в шапке сайта."""

from django.conf import settings

from payments.models import Balance


def wallet(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    if settings.PAYMENTS_ENABLED:
        return {"wallet_balance": Balance.for_user(user)}
    # Пополнение выключено (например, в облаке): «USDT: 0» в шапке только мешает,
    # а строку баланса незачем создавать на каждый запрос. Остаток, если он есть, по-прежнему виден.
    balance = Balance.objects.filter(user=user, amount__gt=0).first()
    return {"wallet_balance": balance} if balance else {}

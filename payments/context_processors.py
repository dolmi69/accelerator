"""Показывает баланс пользователя в шапке сайта."""

from payments.models import Balance


def wallet(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"wallet_balance": Balance.for_user(user)}

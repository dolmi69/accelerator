"""Тарифы Free и Pro.

Pro — временная подписка до даты (`User.pro_until`). «Безлимит» не продаём:
у Pro конечная дневная квота AI-запросов. Продление — вручную в админке.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

FREE = "free"
PRO = "pro"


def is_pro(user) -> bool:
    """Активна ли подписка Pro прямо сейчас."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    until = getattr(user, "pro_until", None)
    return bool(until and until > timezone.now())


def quota_for(plan: str) -> int:
    """Дневная квота AI-запросов для тарифа."""
    return settings.PRO_AI_REQUESTS_PER_DAY if plan == PRO else settings.AI_REQUESTS_PER_DAY


def quota(user) -> dict:
    """Тариф и дневная квота для пользователя."""
    plan = PRO if is_pro(user) else FREE
    return {
        "plan": plan,
        "label": "Pro" if plan == PRO else "Free",
        "requests_per_day": quota_for(plan),
    }


def activate_pro(user, days=None):
    """Включает или продлевает Pro. Возвращает новую дату окончания."""
    days = int(days or settings.PRO_DAYS)
    now = timezone.now()
    base = user.pro_until if (user.pro_until and user.pro_until > now) else now
    user.pro_until = base + timedelta(days=days)
    user.save(update_fields=["pro_until"])
    return user.pro_until

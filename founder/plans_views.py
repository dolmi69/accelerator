"""Страница тарифов и тестовая оплата Pro.

Ядро тарифа (поле `User.pro_until` и `is_pro`) уже есть в `founder.services.plans`;
здесь — UI: страница тарифов, тестовая оплата и активация Pro на 30 дней.
Квота показывается из настроек лаборатории (LAB_REQUESTS_PER_DAY / LAB_PRO_...).
"""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from founder.services import plans


def activate_pro(user, days):
    """Включает или продлевает Pro. Возвращает новую дату окончания."""
    now = timezone.now()
    base = user.pro_until if (user.pro_until and user.pro_until > now) else now
    user.pro_until = base + timedelta(days=days)
    user.save(update_fields=["pro_until"])
    return user.pro_until


@login_required
@require_GET
def tariffs(request):
    return render(
        request,
        "founder/tariffs.html",
        {
            "price_rub": settings.PRO_PRICE_RUB,
            "pro_days": settings.PRO_DAYS,
            "is_pro": plans.is_pro(request.user),
            "pro_until": request.user.pro_until,
            "free_quota": settings.LAB_REQUESTS_PER_DAY,
            "pro_quota": settings.LAB_PRO_REQUESTS_PER_DAY,
        },
    )


@login_required
@require_GET
def pro_test_pay(request):
    return render(
        request,
        "founder/pro_test_pay.html",
        {
            "price_rub": settings.PRO_PRICE_RUB,
            "pro_days": settings.PRO_DAYS,
            "is_pro": plans.is_pro(request.user),
        },
    )


@login_required
@require_POST
def pro_test_pay_confirm(request):
    until = activate_pro(request.user, settings.PRO_DAYS)
    messages.success(
        request,
        f"Pro включён до {until:%d.%m.%Y}. Тестовый режим — деньги не списывались.",
    )
    return redirect("tariffs")

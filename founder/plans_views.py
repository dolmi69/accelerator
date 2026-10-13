"""Страница тарифов и тестовая оплата Pro."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from founder.services import plans


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
            "free_quota": plans.quota_for(plans.FREE),
            "pro_quota": plans.quota_for(plans.PRO),
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
    until = plans.activate_pro(request.user)
    messages.success(
        request,
        f"Pro включён до {until:%d.%m.%Y}. Тестовый режим — деньги не списывались.",
    )
    return redirect("tariffs")

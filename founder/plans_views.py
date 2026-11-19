"""Tariffs and the local Pro checkout preview."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from founder.services import plans


def _test_checkout_available():
    return settings.DEBUG and settings.PRO_TEST_CHECKOUT_ENABLED


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
            "plan_units": plans.PRO_LAB_UNITS,
            "lab_plan": plans.lab_period(request.user),
            "test_checkout": _test_checkout_available(),
        },
    )


@login_required
@require_GET
def pro_test_pay(request):
    if not _test_checkout_available():
        raise Http404
    return render(
        request,
        "founder/pro_test_pay.html",
        {
            "price_rub": settings.PRO_PRICE_RUB,
            "pro_days": settings.PRO_DAYS,
            "is_pro": plans.is_pro(request.user),
            "plan_units": plans.PRO_LAB_UNITS,
        },
    )


@login_required
@require_POST
def pro_test_pay_confirm(request):
    if not _test_checkout_available():
        raise Http404
    until = plans.activate_pro(request.user, settings.PRO_DAYS)
    messages.success(
        request,
        f"Pro включён до {until:%d.%m.%Y}. Тестовый режим — деньги не списывались.",
    )
    return redirect("tariffs")

"""Кошелёк монет и продвижение проекта за монеты."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from founder.services.access import get_startup
from founder.services.coins import (
    EARN_RULES, OFFERS, WELCOME_BONUS, CoinError, active_promotions, balance, buy_promotion, earned_today,
    offer_blocker, plural_coins,
)


@login_required
def wallet(request):
    history = Paginator(request.user.coin_transactions.select_related("startup"), 20).get_page(request.GET.get("page"))
    rules = [{"label": kind.label, "rule": rule, "today": earned_today(request.user, kind)}
             for kind, rule in EARN_RULES.items()]
    return render(request, "coins/wallet.html", {
        "balance": balance(request.user), "history": history, "rules": rules,
        "offers": OFFERS.values(), "welcome_bonus": WELCOME_BONUS,
    })


@login_required
def promote(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    active = list(active_promotions(startup).select_related("bought_by"))
    coins = balance(request.user)
    offers = [{"offer": offer, "blocker": offer_blocker(startup, offer), "missing": max(0, offer.cost - coins),
               "active_until": max((p.ends_at for p in active if p.kind == offer.kind), default=None)}
              for offer in OFFERS.values()]
    return render(request, "coins/promote.html", {
        "startup": startup, "workspace_tab": "promote", "balance": coins,
        "offers": offers, "active": active,
        "history": startup.promotions.select_related("bought_by")[:10],
    })


@login_required
@require_POST
def promote_buy(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    try:
        promotion = buy_promotion(request.user, startup, request.POST.get("kind"))
    except CoinError as exc:
        messages.error(request, str(exc))
    else:
        offer = OFFERS[promotion.kind]
        messages.success(request, f"«{offer.title}» активно до {timezone.localtime(promotion.ends_at):%d.%m %H:%M}. "
                                  f"Списано {offer.cost} {plural_coins(offer.cost)}.")
    return redirect("promote", startup_id=startup.pk)

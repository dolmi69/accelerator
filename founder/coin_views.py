"""Кошелёк монет и продвижение проекта за монеты."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from founder.forms import BountyForm
from founder.models import MascotState, Promotion
from founder.services import activity, wardrobe as wardrobe_service
from founder.services.access import get_startup
from founder.services.coins import (
    BOUNTY_MIN_FEEDBACK, EARN_RULES, OFFERS, STREAK_BONUS, STREAK_DAYS, WELCOME_BONUS, CoinError,
    active_promotions, balance, buy_promotion, close_bounty, earned_today, fund_bounty, offer_blocker,
    plural_coins, plural_days, streak,
)
from founder.services.wardrobe import CATALOG, SLOTS, WardrobeError, owned_codes


@login_required
def wallet(request):
    history = Paginator(request.user.coin_transactions.select_related("startup"), 20).get_page(request.GET.get("page"))
    rules = [{"label": kind.label, "rule": rule, "today": earned_today(request.user, kind)}
             for kind, rule in EARN_RULES.items()]
    days = streak(request.user)
    progress = days % STREAK_DAYS or (STREAK_DAYS if days else 0)
    return render(request, "coins/wallet.html", {
        "balance": balance(request.user), "history": history, "rules": rules,
        "offers": OFFERS.values(), "welcome_bonus": WELCOME_BONUS,
        "streak": days, "streak_days": STREAK_DAYS, "streak_bonus": STREAK_BONUS,
        "streak_label": plural_days(days), "streak_left_label": plural_days(STREAK_DAYS - days % STREAK_DAYS),
        "streak_dots": [index < progress for index in range(STREAK_DAYS)],
        "wardrobe_from": min(item.cost for item in CATALOG.values()),
    })


@login_required
def promote(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    active = list(active_promotions(startup).select_related("bought_by"))
    coins = balance(request.user)
    offers = [{"offer": offer, "blocker": offer_blocker(startup, offer), "missing": max(0, offer.cost - coins),
               "active_until": max((p.ends_at for p in active if p.kind == offer.kind), default=None)}
              for offer in OFFERS.values()]
    testers_offer = OFFERS[Promotion.Kind.TESTERS]
    return render(request, "coins/promote.html", {
        "startup": startup, "workspace_tab": "promote", "balance": coins,
        "offers": offers, "active": active,
        "history": startup.promotions.select_related("bought_by")[:10],
        "bounty_form": BountyForm(), "bounty_blocker": offer_blocker(startup, testers_offer),
        "bounties": startup.test_bounties.filter(closed_at__isnull=True).select_related("funder"),
        "bounty_min_feedback": BOUNTY_MIN_FEEDBACK,
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


@login_required
def wardrobe(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    mascot, _ = MascotState.objects.get_or_create(startup=startup)
    owned = owned_codes(startup)
    coins = balance(request.user)
    groups = [{"title": title, "items": [
        {"item": item, "owned": item.code in owned, "worn": item.code in mascot.accessories,
         "missing": max(0, item.cost - coins)}
        for item in CATALOG.values() if item.slot == slot]} for slot, title in SLOTS.items()]
    return render(request, "coins/wardrobe.html", {
        "startup": startup, "workspace_tab": "wardrobe", "mascot": mascot, "groups": groups, "balance": coins,
        "hidden_now": wardrobe_service.hidden_now(mascot),
    })


@login_required
@require_POST
def wardrobe_action(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    code, action = request.POST.get("code"), request.POST.get("action")
    try:
        if action == "buy":
            item = wardrobe_service.buy(request.user, startup, code)
            activity.log(startup, request.user, activity.Kind.COINS, f"У Бруно обновка: {item.title.lower()}")
            messages.success(request, f"{item.title} — теперь у Бруно. Списано {item.cost} {plural_coins(item.cost)}.")
        elif action == "wear":
            wardrobe_service.wear(startup, code)
        elif action == "take_off":
            wardrobe_service.take_off(startup, code)
        else:
            raise WardrobeError("Неизвестное действие.")
    except WardrobeError as exc:
        messages.error(request, str(exc))
    return redirect("wardrobe", startup_id=startup.pk)


@login_required
@require_POST
def bounty_fund(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    form = BountyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Выберите награду и число тестов (от 1 до 20).")
        return redirect("promote", startup_id=startup.pk)
    reward, tests = form.cleaned_data["reward"], form.cleaned_data["tests"]
    try:
        fund_bounty(request.user, startup, reward, tests)
    except CoinError as exc:
        messages.error(request, str(exc))
    else:
        activity.log(startup, request.user, activity.Kind.COINS,
                     f"Награда тестировщикам: {tests} × {reward} {plural_coins(reward)}")
        messages.success(request, f"Награда запущена: {reward} {plural_coins(reward)} за тест с отзывом, "
                                  f"до {tests} тестов. Монеты зарезервированы.")
    return redirect("promote", startup_id=startup.pk)


@login_required
@require_POST
def bounty_close(request, startup_id, bounty_id):
    startup = get_startup(request, startup_id, edit=True)
    try:
        refund = close_bounty(request.user, startup, bounty_id)
    except CoinError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Награда закрыта." + (f" Вернули {refund} {plural_coins(refund)}." if refund else ""))
    return redirect("promote", startup_id=startup.pk)

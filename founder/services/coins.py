"""Монеты: начисление за работу над проектом и трата на продвижение.

Баланс User.coins меняется только здесь и всегда вместе с записью CoinTransaction.
Начисления идемпотентны по ключу события и ограничены дневными лимитами, чтобы
монеты нельзя было накрутить короткими сообщениями.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from founder.models import CoinTransaction, ProjectCard, Promotion, User

Kind = CoinTransaction.Kind

WELCOME_BONUS = 50
CHAT_MIN_LENGTH = 15


@dataclass(frozen=True)
class EarnRule:
    amount: int
    daily_cap: int | None
    description: str


EARN_RULES = {
    Kind.CHAT: EarnRule(2, 20, "за содержательное сообщение Бруно (от 15 символов)"),
    Kind.PITCH: EarnRule(10, 30, "за завершённую тренировку питча с разбором"),
    Kind.EVIDENCE: EarnRule(5, 25, "за новую запись в дневнике доказательств"),
    Kind.TASK: EarnRule(15, 45, "за выполненное задание Бруно с записанным результатом"),
    Kind.TEST: EarnRule(5, 25, "за тест чужого прототипа с отзывом"),
}


@dataclass(frozen=True)
class Offer:
    kind: str
    cost: int
    days: int
    title: str
    description: str
    needs_prototype: bool = False


OFFERS = {
    Promotion.Kind.FEED_TOP: Offer(
        Promotion.Kind.FEED_TOP, 60, 3, "Поднять карточку в ленте",
        "Карточка показывается в начале ленты сообщества с отметкой «Продвигается».",
    ),
    Promotion.Kind.HIGHLIGHT: Offer(
        Promotion.Kind.HIGHLIGHT, 30, 7, "Выделить карточку",
        "Карточка получает цветную рамку и выделяется в ленте и в поиске.",
    ),
    Promotion.Kind.TESTERS: Offer(
        Promotion.Kind.TESTERS, 50, 7, "Позвать тестировщиков",
        "Прототип попадает в блок «Нужны тестировщики» над лентой. Тестировщики получают монеты за отзыв.",
        needs_prototype=True,
    ),
}


class CoinError(Exception):
    pass


def _today_start():
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def earned_today(user, kind):
    total = CoinTransaction.objects.filter(user=user, kind=kind, created_at__gte=_today_start()).aggregate(
        total=Sum("amount"))["total"]
    return total or 0


def _record(user, amount, kind, *, key=None, startup=None, note=""):
    with transaction.atomic():
        CoinTransaction.objects.create(user=user, amount=amount, kind=kind, key=key, startup=startup, note=note[:200])
        User.objects.filter(pk=user.pk).update(coins=F("coins") + amount)


def award(user, kind, *, key, startup=None, note=""):
    """Начислить монеты за событие. Возвращает число начисленных монет (0 — лимит или повтор)."""
    rule = EARN_RULES[kind]
    if not user or not user.is_authenticated:
        return 0
    amount = rule.amount
    if rule.daily_cap is not None:
        amount = min(amount, rule.daily_cap - earned_today(user, kind))
    if amount <= 0:
        return 0
    try:
        _record(user, amount, kind, key=f"{kind}:{key}", startup=startup, note=note)
    except IntegrityError:
        return 0  # Уже начисляли за это событие.
    return amount


def coins_note(earned):
    """Хвост для flash-сообщения: « +5 монет.»"""
    if not earned:
        return ""
    return f" +{earned} {plural_coins(earned)}."


def plural_coins(n):
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return "монета"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "монеты"
    return "монет"


def grant_welcome(user):
    try:
        _record(user, WELCOME_BONUS, Kind.WELCOME, key=f"welcome:{user.pk}", note="Стартовый бонус")
    except IntegrityError:
        return 0
    return WELCOME_BONUS


def balance(user):
    return User.objects.values_list("coins", flat=True).get(pk=user.pk)


def active_promotions(startup, now=None):
    return startup.promotions.filter(ends_at__gt=now or timezone.now())


def offer_blocker(startup, offer):
    """Почему предложение сейчас недоступно, или пустая строка."""
    card = ProjectCard.objects.filter(startup=startup).first()
    if not card or not card.published_at:
        return "Сначала опубликуйте карточку проекта в сообществе."
    if offer.needs_prototype:
        publication = getattr(startup, "lab_publication", None)
        if not publication or publication.visibility != publication.Visibility.PUBLIC:
            return "Добавьте прототип из лаборатории в карточку с доступом «Для всех»."
    return ""


def buy_promotion(user, startup, kind):
    """Списать монеты и продлить продвижение. Активное продвижение того же вида продлевается."""
    offer = OFFERS.get(kind)
    if offer is None:
        raise CoinError("Неизвестный вид продвижения.")
    blocker = offer_blocker(startup, offer)
    if blocker:
        raise CoinError(blocker)
    now = timezone.now()
    with transaction.atomic():
        if not User.objects.filter(pk=user.pk, coins__gte=offer.cost).update(coins=F("coins") - offer.cost):
            raise CoinError(f"Не хватает монет: нужно {offer.cost}, у вас {balance(user)}.")
        current = (startup.promotions.select_for_update().filter(kind=kind, ends_at__gt=now)
                   .order_by("-ends_at").first())
        starts = current.ends_at if current else now
        promotion = Promotion.objects.create(startup=startup, kind=kind, bought_by=user, cost=offer.cost,
                                             starts_at=starts, ends_at=starts + timedelta(days=offer.days))
        CoinTransaction.objects.create(user=user, amount=-offer.cost, kind=Kind.PROMOTION, startup=startup,
                                       note=f"{offer.title} · {startup.name}"[:200])
    return promotion

"""Гардероб Бруно: аксессуары за монеты поверх растровой картинки.

Бруно — вырезки из одного PNG, у каждого настроения своя поза. Аксессуары рисуются
векторно в координатах сцены 300×300, поэтому для каждой позы задано, где голова,
глаза и шея, насколько крупная голова и как она наклонена.
"""
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import F

from founder.models import CoinTransaction, MascotItem, MascotState, User


@dataclass(frozen=True)
class Item:
    code: str
    title: str
    slot: str
    cost: int
    description: str


SLOTS = {"head": "Голова", "eyes": "Глаза", "neck": "Шея"}

CATALOG = {item.code: item for item in (
    Item("party", "Праздничный колпак", "head", 30, "Для дня, когда пришла первая оплата."),
    Item("cap", "Кепка основателя", "head", 40, "Повседневный вид для полевых интервью."),
    Item("crown", "Корона единорога", "head", 120, "Для тех, кто метит в миллиард."),
    Item("glasses", "Очки аналитика", "eyes", 40, "Помогают видеть юнит-экономику насквозь."),
    Item("shades", "Тёмные очки", "eyes", 60, "Спокойствие перед любым инвестором."),
    Item("bowtie", "Бабочка", "neck", 30, "Строго, но дружелюбно — для демо-дня."),
    Item("scarf", "Шарф стартапера", "neck", 50, "Тёплый и упрямый, как идея на ранней стадии."),
)}

# Точки привязки в координатах сцены: (x, y) для слота, общий масштаб и наклон головы.
ANCHORS = {
    MascotState.Mood.CURIOUS: {"head": (134, 42), "eyes": (139, 103), "neck": (162, 180), "scale": 1.1, "rotate": -14},
    MascotState.Mood.CONFIDENT: {"head": (146, 40), "eyes": (144, 97), "neck": (146, 158), "scale": 1.0, "rotate": 0},
    MascotState.Mood.FOCUSED: {"head": (148, 52), "eyes": (146, 106), "neck": (148, 159), "scale": 1.0, "rotate": 0},
    MascotState.Mood.SLEEPY: {"head": (107, 174), "eyes": (107, 219), "neck": (110, 250), "scale": 0.72, "rotate": 0},
}


class WardrobeError(Exception):
    pass


def layers(state):
    """Что и где рисовать поверх Бруно; порядок слоёв: шея, глаза, голова."""
    anchor = ANCHORS.get(state.mood, ANCHORS[MascotState.Mood.CURIOUS])
    worn = [CATALOG[code] for code in state.accessories or [] if code in CATALOG]
    result = []
    for slot in ("neck", "eyes", "head"):
        for item in worn:
            if item.slot == slot:
                x, y = anchor[slot]
                result.append({"code": item.code, "title": item.title,
                               "transform": f"translate({x} {y}) rotate({anchor['rotate']}) scale({anchor['scale']})"})
    return result


def owned_codes(startup):
    return set(startup.mascot_items.values_list("code", flat=True))


def buy(user, startup, code):
    item = CATALOG.get(code)
    if item is None:
        raise WardrobeError("Такого аксессуара нет.")
    try:
        with transaction.atomic():
            MascotItem.objects.create(startup=startup, code=code, bought_by=user)
            if not User.objects.filter(pk=user.pk, coins__gte=item.cost).update(coins=F("coins") - item.cost):
                raise WardrobeError(f"Не хватает монет: нужно {item.cost}.")
            CoinTransaction.objects.create(user=user, amount=-item.cost, kind=CoinTransaction.Kind.OUTFIT,
                                           startup=startup, note=f"{item.title} · {startup.name}"[:200])
    except IntegrityError as exc:
        raise WardrobeError("Этот аксессуар у Бруно уже есть.") from exc
    wear(startup, code)
    return item


def wear(startup, code):
    """Надеть купленный аксессуар; другой аксессуар того же слота снимается."""
    item = CATALOG.get(code)
    if item is None or code not in owned_codes(startup):
        raise WardrobeError("Сначала купите этот аксессуар.")
    with transaction.atomic():
        state, _ = MascotState.objects.select_for_update().get_or_create(startup=startup)
        state.accessories = [c for c in state.accessories if c in CATALOG and CATALOG[c].slot != item.slot] + [code]
        state.save(update_fields=["accessories", "updated_at"])


def take_off(startup, code):
    with transaction.atomic():
        state, _ = MascotState.objects.select_for_update().get_or_create(startup=startup)
        state.accessories = [c for c in state.accessories if c != code]
        state.save(update_fields=["accessories", "updated_at"])

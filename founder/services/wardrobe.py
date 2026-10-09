"""Гардероб Бруно: аксессуары за монеты поверх растровой картинки.

Бруно — вырезки из одного PNG (static/founder/img/bruno-reference.png), у каждого
настроения своя поза. Аксессуары рисуются в координатах этого PNG: для каждой позы
замерены макушка между ушами, оба глаза и место под подбородком. Где поза мешает
(Бруно читает книгу или спит), у аксессуара для шеи свой вариант рисунка.
"""
import math
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

@dataclass(frozen=True)
class Pose:
    """Замеры позы в пикселях исходного PNG.

    frame — те же x, y, width, height и viewBox, что у вложенного <svg> позы в _bruno.html.
    head — середина макушки между ушами, ширина макушки между ушами, наклон головы.
    eyes — центр и полуоси левого и правого глаза.
    neck — точка под подбородком и наклон; neck_style — вариант рисунка для шеи.
    """
    frame: tuple
    head: tuple
    eyes: tuple
    neck: tuple
    neck_style: str = "upright"
    scarf: tuple | None = None  # своя точка для шарфа, если он сидит не под подбородком
    cover: str = ""  # контур предмета перед шеей: аксессуары для шеи рисуются позади него
    hidden: tuple = ()  # что в этой позе целиком закрыто (бабочку за книгой не видно)


POSES = {
    MascotState.Mood.CURIOUS: Pose(
        frame=(10, 0, 280, 295, "30 25 680 690"),
        head=(303, 122, 199, -16.3),
        eyes=((249, 297, 56, 66), (434, 243, 57, 67)),
        neck=(350, 412, -12),
    ),
    MascotState.Mood.CONFIDENT: Pose(
        frame=(10, 5, 280, 285, "640 60 340 322"),
        head=(806, 97, 90, 3),
        eyes=((749, 157, 29, 31), (852, 168, 28, 31)),
        neck=(800, 233, 4),
    ),
    MascotState.Mood.FOCUSED: Pose(
        frame=(30, 20, 240, 273, "690 730 248 275"),
        head=(797, 762, 76, -2.6),
        eyes=((777, 828, 24, 26), (851, 821, 22, 26)),
        # Шею закрывает книга: бабочка целиком за ней, шарф виден на левом плече и свисает по лапе.
        neck=(818, 864, -6),
        neck_style="reading",
        scarf=(762, 855, 6),
        cover="M763 856 L776 849 L852 871 L900 832 L909 832 L912 900 L893 925 L850 957 L757 932 Z",
        hidden=("bowtie",),
    ),
    MascotState.Mood.SLEEPY: Pose(
        frame=(5, 127, 290, 165, "20 790 370 204"),
        head=(155, 840, 68, -4),
        eyes=((114, 908, 24, 23), (189, 904, 22, 22)),
        # Подбородок лежит на земле: бабочка выглядывает снизу, шарф обвивает шею сбоку.
        neck=(147, 951, -4),
        neck_style="lying",
        scarf=(236, 898, -18),
    ),
}


class WardrobeError(Exception):
    pass


def _r(value):
    """Строка с точкой: SVG не понимает десятичную запятую русской локали."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _glasses(pose):
    """Две линзы точно на глазах: локальная ось x проходит через центры глаз."""
    (lx, ly, lrx, lry), (rx_, ry_, rrx, rry) = pose.eyes
    angle = math.degrees(math.atan2(ry_ - ly, rx_ - lx))
    half = math.hypot(rx_ - lx, ry_ - ly) / 2
    inner_l, inner_r = -half + lrx * 0.97, half - rrx * 0.97
    lift = -min(lry, rry) * 0.2
    gap = inner_r - inner_l
    return {
        "transform": f"translate({_r((lx + rx_) / 2)} {_r((ly + ry_) / 2)}) rotate({_r(angle)})",
        "left": {"cx": _r(-half), "rx": lrx, "ry": lry},
        "right": {"cx": _r(half), "rx": rrx, "ry": rry},
        "bridge": f"M{_r(inner_l)} {_r(lift)} Q0 {_r(lift - gap * 0.35)} {_r(inner_r)} {_r(lift)}",
        "temples": (f"M{_r(-half - lrx)} {_r(-lry * 0.25)} l{_r(-half * 0.35)} {_r(-lry * 0.2)} "
                    f"M{_r(half + rrx)} {_r(-rry * 0.25)} l{_r(half * 0.35)} {_r(-rry * 0.2)}"),
        "stroke": _r(pose.head[2] * 0.045),
    }


def layers(state):
    """Что и где рисовать поверх Бруно. Координаты — в пикселях исходного PNG."""
    pose = POSES.get(state.mood, POSES[MascotState.Mood.CURIOUS])
    worn = {CATALOG[code].slot: CATALOG[code] for code in state.accessories or [] if code in CATALOG}
    if not worn or all(item.code in pose.hidden for item in worn.values()):
        return None
    x, y, width, height, view_box = pose.frame
    head_x, head_y, head_width, head_angle = pose.head
    neck_x, neck_y, neck_angle = pose.neck
    scale = _r(head_width / 100)
    items = []
    # Порядок слоёв: шея, глаза, голова.
    if "neck" in worn and worn["neck"].code not in pose.hidden:
        code = worn["neck"].code
        if code == "scarf" and pose.scarf:
            neck_x, neck_y, neck_angle = pose.scarf
        items.append({"code": code, "slot": "neck", "style": pose.neck_style, "behind": bool(pose.cover),
                      "transform": f"translate({neck_x} {neck_y}) rotate({neck_angle}) scale({scale})"})
    if "eyes" in worn:
        items.append({"code": worn["eyes"].code, "slot": "eyes", **_glasses(pose)})
    if "head" in worn:
        items.append({"code": worn["head"].code, "slot": "head",
                      "transform": f"translate({head_x} {head_y}) rotate({head_angle}) scale({scale})"})
    return {"frame": {"x": x, "y": y, "width": width, "height": height, "viewBox": view_box},
            "cover": pose.cover, "items": items, "has_hat": "head" in worn}


def hidden_now(state):
    """Надетые аксессуары, которые текущая поза закрывает целиком."""
    pose = POSES.get(state.mood, POSES[MascotState.Mood.CURIOUS])
    return [CATALOG[code] for code in state.accessories or [] if code in pose.hidden and code in CATALOG]


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

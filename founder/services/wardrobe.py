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

def _ellipse(cx, cy, rx, ry):
    return f"M{cx - rx} {cy} a{rx} {ry} 0 1 0 {2 * rx} 0 a{rx} {ry} 0 1 0 {-2 * rx} 0 Z"


@dataclass(frozen=True)
class Scarf:
    """Шарф от края шеи до края: осевая линия от left до right, провис посередине,
    толщина и свободный конец (откуда по ленте и куда свисает)."""
    left: tuple
    right: tuple
    sag: float
    thickness: float
    tail_at: float
    tail: tuple
    tail_width: float
    spread: tuple = ((-0.18, 1.0), (0.22, 0.78))  # поворот (рад) и длина каждого из двух концов
    # Толщина поперёк самой ленты, а не по вертикали кольца: для сильно изогнутого шарфа,
    # иначе на изгибе лента сплющивается в острый угол.
    follow_curve: bool = False


@dataclass(frozen=True)
class Pose:
    """Замеры позы в пикселях исходного PNG.

    frame — те же x, y, width, height и viewBox, что у вложенного <svg> позы в _bruno.html.
    head — середина макушки между ушами, ширина макушки между ушами, наклон головы.
    eyes — центр и полуоси левого и правого глаза; eyes_style="reading" — очки для чтения на носу.
    neck — точка под подбородком и наклон бабочки; scarf — шарф по краям шеи.
    hidden — что в этой позе закрыто целиком (шею Бруно с книгой закрывает книга).
    front — то, что находится перед шарфом (подбородок, ухо): лента проходит позади.
    """
    frame: tuple
    head: tuple
    eyes: tuple
    neck: tuple
    scarf: Scarf | None = None
    eyes_style: str = "round"
    neck_style: str = "upright"
    hidden: tuple = ()
    front: tuple = ()


POSES = {
    MascotState.Mood.CURIOUS: Pose(
        frame=(10, 0, 280, 295, "30 25 680 690"),
        head=(303, 122, 199, -16.3),
        eyes=((249, 297, 56, 66), (434, 243, 57, 67)),
        neck=(350, 412, -12),
        scarf=Scarf(left=(108, 372), right=(580, 342), sag=58, thickness=50,
                    tail_at=0.66, tail=(16, 118), tail_width=38),
        front=(_ellipse(357, 315, 57, 65),),
    ),
    MascotState.Mood.CONFIDENT: Pose(
        frame=(10, 5, 280, 285, "640 60 340 322"),
        head=(806, 97, 90, 3),
        eyes=((749, 157, 29, 31), (852, 168, 28, 31)),
        neck=(800, 233, 4),
        scarf=Scarf(left=(701, 209), right=(904, 208), sag=24, thickness=24,
                    tail_at=0.64, tail=(6, 56), tail_width=17),
        front=(_ellipse(797, 183, 31, 39),),
    ),
    MascotState.Mood.FOCUSED: Pose(
        frame=(30, 20, 240, 273, "690 730 248 275"),
        head=(797, 762, 76, -2.6),
        eyes=((777, 828, 20, 23), (851, 821, 18, 23)),
        neck=(818, 864, -6),
        # Книга закрывает шею: бабочки и шарфа не видно, очки — для чтения на кончике носа.
        eyes_style="reading",
        hidden=("bowtie", "scarf"),
    ),
    MascotState.Mood.SLEEPY: Pose(
        frame=(5, 127, 290, 165, "20 790 370 204"),
        head=(155, 840, 68, -4),
        eyes=((114, 908, 24, 23), (189, 904, 22, 22)),
        # Подбородок лежит на земле: бабочка подоткнута под него. Шарф выходит из-за правого
        # уха и идёт по шее вниз до земли (края режет силуэт), концы лежат на земле.
        neck=(147, 944, -4),
        neck_style="lying",
        # Верхний конец загибается к уху и целиком уходит за него.
        scarf=Scarf(left=(214, 855), right=(258, 966), sag=-22, thickness=24,
                    tail_at=0.74, tail=(60, 9), tail_width=16, spread=((-0.05, 1.0), (0.13, 0.8)),
                    follow_curve=True),
        front=(_ellipse(216, 853, 25, 25),),
    ),
}


class WardrobeError(Exception):
    pass


def _r(value):
    """Строка с точкой: SVG не понимает десятичную запятую русской локали."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _pt(x, y):
    return f"{_r(x)} {_r(y)}"


def _glasses(pose):
    """Две линзы точно на глазах: локальная ось x проходит через центры глаз."""
    (lx, ly, lrx, lry), (rx_, ry_, rrx, rry) = pose.eyes
    if pose.eyes_style == "reading":
        return _reading_glasses(pose)
    angle = math.degrees(math.atan2(ry_ - ly, rx_ - lx))
    half = math.hypot(rx_ - lx, ry_ - ly) / 2
    inner_l, inner_r = -half + lrx * 0.97, half - rrx * 0.97
    lift = -min(lry, rry) * 0.2
    gap = inner_r - inner_l
    return {
        "style": "round",
        "transform": f"translate({_pt((lx + rx_) / 2, (ly + ry_) / 2)}) rotate({_r(angle)})",
        "left": {"cx": _r(-half), "rx": lrx, "ry": lry},
        "right": {"cx": _r(half), "rx": rrx, "ry": rry},
        "bridge": f"M{_pt(inner_l, lift)} Q0 {_r(lift - gap * 0.35)} {_pt(inner_r, lift)}",
        "temples": (f"M{_pt(-half - lrx, -lry * 0.25)} l{_pt(-half * 0.35, -lry * 0.2)} "
                    f"M{_pt(half + rrx, -rry * 0.25)} l{_pt(half * 0.35, -rry * 0.2)}"),
        "stroke": _r(pose.head[2] * 0.045),
    }


def _reading_glasses(pose):
    """Полукруглые очки для чтения: сидят низко, на кончике носа, под зрачками."""
    lenses, tops = [], []
    for cx, cy, rx, ry in pose.eyes:
        half, top, depth = rx * 1.12, cy + ry * 0.12, ry * 0.78
        lenses.append(f"M{_pt(cx - half, top)} L{_pt(cx + half, top)} A{_pt(half, depth)} 0 0 1 {_pt(cx - half, top)} Z")
        tops.append((cx - half, cx + half, top))
    (l_left, l_right, l_top), (r_left, r_right, r_top) = tops
    return {
        "style": "reading",
        "transform": "translate(0 0)",
        "lenses": " ".join(lenses),
        "bridge": f"M{_pt(l_right, l_top)} Q{_pt((l_right + r_left) / 2, min(l_top, r_top) - 7)} {_pt(r_left, r_top)}",
        "temples": f"M{_pt(l_left, l_top)} l-12 -7 M{_pt(r_right, r_top)} l12 -7",
        "stroke": _r(pose.head[2] * 0.035),
    }


def _scarf(spec):
    """Шарф как кольцо вокруг шеи, увиденное чуть сверху: спереди видна нижняя половина эллипса.

    Поэтому внизу лента провисает полого, а к бокам круче уходит за шею. Толщина ленты
    вертикальная, полоски вертикальные и сгущаются к бокам, края затенены, у узла два конца.
    """
    (x0, y0), (x2, y2) = spec.left, spec.right
    length = math.hypot(x2 - x0, y2 - y0)
    dx, dy = (x2 - x0) / length, (y2 - y0) / length
    nx, ny = -dy, dx  # «вниз» для ленты слева направо
    half = spec.thickness / 2

    def center(u):
        along = length * (1 - math.cos(math.pi * u)) / 2  # равные углы вокруг шеи
        drop = spec.sag * math.sin(math.pi * u)
        return x0 + dx * along + nx * drop, y0 + dy * along + ny * drop

    def point(u, offset=0.0):
        cx, cy = center(u)
        if not spec.follow_curve:
            return cx + nx * offset, cy + ny * offset
        (ax, ay), (bx, by) = center(max(0.0, u - 0.01)), center(min(1.0, u + 0.01))
        tx, ty = bx - ax, by - ay
        size = math.hypot(tx, ty) or 1.0
        px, py = -ty / size, tx / size
        if px * nx + py * ny < 0:  # та же сторона, что у нормали хорды
            px, py = -px, -py
        return cx + px * offset, cy + py * offset

    steps = [i / 24 for i in range(25)]
    top = [point(u, -half) for u in steps]
    bottom = [point(u, half) for u in steps]
    band = "M" + " L".join(_pt(*p) for p in top) + " L" + " L".join(_pt(*p) for p in reversed(bottom)) + " Z"
    stripes = " ".join(f"M{_pt(*point(u, -half * 0.88))} L{_pt(*point(u, half * 0.88))}"
                       for u in (0.07, 0.19, 0.31, 0.43, 0.55, 0.81, 0.93) if abs(u - spec.tail_at) > 0.06)

    # Узел и два свободных конца: длинный и чуть короче, слегка разведены.
    kx, ky = point(spec.tail_at, half * 0.55)
    tx, ty = spec.tail
    tail_len = math.hypot(tx, ty)
    ux, uy = tx / tail_len, ty / tail_len
    tails = []
    for (turn, scale), width in zip(spec.spread, (spec.tail_width, spec.tail_width * 0.9)):
        cos_t, sin_t = math.cos(turn), math.sin(turn)
        vx, vy = (ux * cos_t - uy * sin_t) * tail_len * scale, (ux * sin_t + uy * cos_t) * tail_len * scale
        px, py = -vy / math.hypot(vx, vy) * width / 2, vx / math.hypot(vx, vy) * width / 2
        end_x, end_y = kx + vx + vx / tail_len * width * 0.45, ky + vy + vy / tail_len * width * 0.45
        # Каждый конец со своими полосками: верхний перекрывает нижний вместе с его полосками.
        tails.append({
            "shape": f"M{_pt(kx - px * 0.7, ky - py * 0.7)} L{_pt(kx + px * 0.7, ky + py * 0.7)} "
                     f"L{_pt(kx + vx + px, ky + vy + py)} Q{_pt(end_x, end_y)} {_pt(kx + vx - px, ky + vy - py)} Z",
            "stripes": " ".join(f"M{_pt(kx + vx * k - px * 0.85, ky + vy * k - py * 0.85)} "
                                f"L{_pt(kx + vx * k + px * 0.85, ky + vy * k + py * 0.85)}" for k in (0.62, 0.84)),
        })
    return {
        "band": band, "stripes": stripes, "tails": tails,
        "knot": {"cx": _r(kx), "cy": _r(ky), "rx": _r(spec.tail_width * 0.62), "ry": _r(half * 0.95)},
        "shade": {"x1": _r(x0), "y1": _r(y0), "x2": _r(x2), "y2": _r(y2)},
        "stroke": _r(spec.thickness * 0.14),
    }


def layers(state):
    """Что и где рисовать поверх Бруно. Координаты — в пикселях исходного PNG."""
    pose = POSES.get(state.mood, POSES[MascotState.Mood.CURIOUS])
    worn = {CATALOG[code].slot: CATALOG[code] for code in state.accessories or []
            if code in CATALOG and code not in pose.hidden}
    if not worn:
        return None
    x, y, width, height, view_box = pose.frame
    head_x, head_y, head_width, head_angle = pose.head
    neck_x, neck_y, neck_angle = pose.neck
    scale = _r(head_width / 100)
    items = []
    # Порядок слоёв: шея, глаза, голова.
    if "neck" in worn:
        code = worn["neck"].code
        item = {"code": code, "slot": "neck", "style": pose.neck_style,
                "transform": f"translate({neck_x} {neck_y}) rotate({neck_angle}) scale({scale})"}
        if code == "scarf":
            item.update(_scarf(pose.scarf), transform="translate(0 0)")
        items.append(item)
    if "eyes" in worn:
        items.append({"code": worn["eyes"].code, "slot": "eyes", **_glasses(pose)})
    if "head" in worn:
        items.append({"code": worn["head"].code, "slot": "head",
                      "transform": f"translate({head_x} {head_y}) rotate({head_angle}) scale({scale})"})
    return {"frame": {"x": x, "y": y, "width": width, "height": height, "viewBox": view_box},
            "items": items, "has_hat": "head" in worn, "front": " ".join(pose.front)}


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

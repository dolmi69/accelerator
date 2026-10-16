"""Как проект собирается зарабатывать: кто платит, за что, сколько и как часто.

Без этого нельзя ни посчитать деньги, ни защитить проект перед акулами, а модель сама к этой
теме приходит не всегда. Python по словам основателя отмечает, какие части модели заработка
уже названы. Пока чего-то не хватает, Бруно ведёт разговор туда, а акулы видят, что основатель
рассказал Бруно. Грант и деньги организации — это деньги на запуск, а не заработок: за ними
Бруно спрашивает, кто будет платить потом.
"""

import re

from founder.services import funding


PARTS = (
    ("payer", "кто платит"),
    ("product", "за что платят"),
    ("price", "сколько"),
    ("frequency", "как часто"),
)
PART_LABELS = dict(PARTS)

PAYERS = (r"родител|школ|компани|клиник|студент|магазин|кофейн|бизнес|люд|пользовател|клиент|покупател|владел"
          r"|репетитор|мастер|продав|арендатор|ресторан|салон|вуз|университет|организаци|предпринимател")
PATTERNS = {
    "payer": re.compile(
        r"(?<!\w)(?:плат(?:ит|ят|ить\s+будут?|ить\s+будет)|заплат(?:ит|ят)|оплачива\w*|оплат(?:ит|ят)|покупа(?:ет|ют)"
        r"|купят|купит|подписыва(?:ет|ют)ся)(?!\w)"
        rf"|(?:бер[её]м|брать|возьм[её]м)\s+(?:\S+\s+){{0,6}}(?:с|со)\s+(?:\w+\s+)?(?:{PAYERS})"
        rf"|прода\w*\s+(?:\w+\s+){{0,3}}(?:{PAYERS})"
        r"|рекламодател|зарабат\w*\s+на\s+реклам",
        re.IGNORECASE),
    "product": re.compile(
        r"подписк|абонемент|комисси|процент\w*\s+с|лиценз|реклам|премиум|фримиум|freemium|донат|платн\w*\s+(?:функц"
        r"|верси|доступ|тариф)|тариф|разов\w*\s+(?:покупк|оплат|платеж|платёж)"
        r"|за\s+(?:каждый\s+|каждую\s+|одн[уо]\s+)?(?:урок|час|заказ|визит|занятие|доступ|консультац|штуку|услуг"
        r"|сеанс|поездк|аренд|доставк|курс|проверк|сочинени|смен|запис|модул|место|билет|набор|коробк|порци|чашк)"
        r"|прода\w*\s+(?:\w+\s+){0,2}(?:товар|курс|доступ|набор|программ|приложени|сервис|услуг)"
        r"|(?:покупа\w*|купят|купит|закаж\w*|заказыва\w*)\s+(?:\S+\s+){0,3}\w{3,}",
        re.IGNORECASE),
    "price": re.compile(r"\d[\d\s ]*(?:[.,]\d+)?\s*(?:₽|руб|р\.|тыс|к(?!\w)|%|процент)"
                       r"|(?:за|по|стоит|стоимость|цена|бер[её]м|плат(?:ит|ят))\s+\d", re.IGNORECASE),
    "frequency": re.compile(
        r"в\s+(?:месяц|неделю|год|день)|ежемесячн|еженедельн|ежегодн|раз\s+в\s+|каждый\s+(?:месяц|раз|день|год)"
        r"|каждую\s+неделю|разово|разов\w*|один\s+раз|с\s+каждо(?:й|го)|за\s+кажд|подписк|абонемент"
        r"|за\s+(?:урок|час|заказ|визит|занятие|сеанс|поездк|доставк|смен|запис)",
        re.IGNORECASE),
}
# Цена конкурента или прошлый опыт клиента — не цена проекта: «у репетитора мама платила 2000 за час».
NOT_OURS_RE = re.compile(r"конкурент|аналог|(?<!\w)у\s+них(?!\w)|платил[аи]?\s|раньше|сейчас\s+платят|профи\.ру|авито",
                         re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?…\n])\s+|\n+")
# Вопрос Бруно о деньгах: если такой уже звучал недавно, к теме не возвращаемся сразу.
MONEY_TOPIC_RE = re.compile(r"плат|цен[аеуы]|зарабат|деньг|подписк|руб|₽|стоит|комисси|покупа|монетиз", re.IGNORECASE)
QUESTIONS = {
    "payer": "Кто именно будет платить вам деньги: сам пользователь, его родители, компания или кто-то ещё?",
    "product": "За что именно вам будут платить: подписка, разовая покупка, процент с каждой сделки или что-то другое?",
    "price": "Сколько рублей вы хотите брать за это?",
    "frequency": "Как часто клиент будет платить: один раз, за каждый заказ или каждый месяц?",
}
AFTER_FUNDING = ("{source} поможет на старте. А кто будет платить проекту, когда эти деньги закончатся, "
                 "и за что?")
# С какой реплики основателя Бруно сам ведёт к деньгам: сначала пусть расскажет идею своими словами.
FIRST_MONEY_TURN = 2


def _sentences(texts):
    for text in texts:
        yield from (sentence.strip() for sentence in SENTENCE_RE.split(text or "") if sentence.strip())


def revenue_model(texts):
    """{"known": {часть: фраза основателя}, "missing": [части]} по словам основателя, свежие фразы главнее."""
    known = {}
    for sentence in reversed(list(_sentences(texts))):
        if funding.detect([sentence]):
            # «Грант от ВШЭ» — деньги на запуск. Кто платит за продукт, из такой фразы не узнать.
            continue
        for key, pattern in PATTERNS.items():
            if key in known or not pattern.search(sentence):
                continue
            if NOT_OURS_RE.search(sentence):
                continue
            known[key] = sentence[:160]
    return {"known": {key: known[key] for key, _ in PARTS if key in known},
            "missing": [key for key, _ in PARTS if key not in known]}


def startup_texts(startup):
    """Что основатель говорил о проекте: анкета, чаты с Бруно и заметка «Деньги» из картины проекта."""
    from founder.models import ChatMessage, ChatSession, ProjectPicture

    texts = [startup.one_line_pitch, startup.problem, startup.solution]
    texts += list(ChatMessage.objects.filter(
        session__startup=startup, session__mode=ChatSession.Mode.COFOUNDER, role=ChatMessage.Role.USER,
    ).order_by("-created_at", "-id").values_list("content", flat=True)[:300])[::-1]
    picture = ProjectPicture.objects.filter(startup=startup).first()
    money = ((picture.facts if picture else {}).get("money") or {}).get("text", "")
    # Заметка картины — пересказ слов основателя, поэтому идёт раньше его собственных слов.
    return [text for text in [money, *texts] if text]


def model_for(startup):
    return revenue_model(startup_texts(startup))


def status_note(model, source=None):
    """Строка для промпта: что известно о заработке и чего не хватает."""
    known = "; ".join(f"{PART_LABELS[key]} — «{text}»" for key, text in model["known"].items())
    missing = ", ".join(PART_LABELS[key] for key in model["missing"])
    lines = ["Как проект будет зарабатывать (программа проверила слова основателя):"]
    lines.append(f"Известно: {known}." if known else "Известно: пока ничего.")
    if missing:
        lines.append(f"Не названо: {missing}. Пока это не ясно, деньги проекта не посчитать.")
    if source:
        lines.append(f"Основатель ждёт денег от организации: «{source['quote']}». Это деньги на запуск, а не "
                     "заработок: выясни, кто будет платить проекту потом.")
    return "\n".join(lines)


def is_money_question(text):
    return bool(MONEY_TOPIC_RE.search(text or ""))


def next_question(model, asked=(), source=None):
    """Вопрос о первой неизвестной части заработка, которого Бруно ещё не задавал; пустая строка, если всё ясно."""
    from founder.services.mentor import repeats_question

    for key in model["missing"]:
        question = QUESTIONS[key]
        if key == "payer" and source:
            name = source.get("name") or ("грант" if source["kind"] == funding.INSTITUTION else "компания")
            question = AFTER_FUNDING.format(source=name[:1].upper() + name[1:])
        if not repeats_question(question, asked):
            return question
    return ""


def view(startup):
    """Блок «Как зарабатывает» в картине проекта."""
    model = model_for(startup)
    return {"items": [{"key": key, "label": label, "text": model["known"].get(key, "")} for key, label in PARTS],
            "missing": len(model["missing"])}

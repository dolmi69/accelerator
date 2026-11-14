"""Compute monthly results from inputs extracted by the local model."""

import json
import re
from decimal import Decimal, InvalidOperation

from founder.services import local_mentor
from founder.services.bruno import number_change
from founder.services.economics import MONEY_RE, economics_note, summarize


MONTHLY_RESULT_RE = re.compile(
    r"выручк|чист\w+\s+прибыл|после\s+(?:\w+\s+){0,3}(?:расход|затрат)|"
    r"сколько\s+оста[её]тся|остаток|сколько\s+(?:мы\s+)?заработ",
    re.IGNORECASE,
)
CALCULATION_RE = re.compile(
    r"посчита|рассчита|расч[её]т|покаж|сколько|како[йеяую]|каки[ехм]|можно\s+ли\s+назвать|будет\s+ли",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[ \u00a0\u202f]\d{3}\b)*(?:[.,]\d+)?")
MONTH_RE = re.compile(r"\bмесяц|\bежемесяч|\bмес\b", re.IGNORECASE)
OTHER_PERIOD_RE = re.compile(r"(?:за|в|на)\s+(?:\d+\s+)?(?:год|квартал|недел|день|сутки)|годов", re.IGNORECASE)
CURRENCY_RE = re.compile(r"₽|\bруб", re.IGNORECASE)
OTHER_CURRENCY_RE = re.compile(r"[$€]|\b(?:usd|eur|usdt|доллар\w*|евро)\b", re.IGNORECASE)
MONEY = rf"({NUMBER_RE.pattern})\s*(?:₽|руб\w*\.?)"
NEGATIVE_MONEY_RE = re.compile(rf"(?<!\w)[−-]\s*{MONEY}", re.IGNORECASE)
MONTHLY_PRICE = re.compile(
    rf"(?:месячн\w+\s+цена\s+подписк\w*\s*[:—–-]?\s*{MONEY}|"
    rf"{MONEY}\s*(?:/\s*мес\.?|в\s+месяц\b))",
    re.IGNORECASE,
)
SUBSCRIBERS = re.compile(rf"({NUMBER_RE.pattern})\s+(?:платящ\w+\s+)?(?:подписчик\w*|клиент\w*|команд\w*)", re.IGNORECASE)
TARIFF = re.compile(rf"([а-яёa-z]+)\s+(?:тариф\s*)?[:—–-]?\s*{MONEY}\s+в\s+месяц\b", re.IGNORECASE)
TARIFF_PAYMENTS = re.compile(r"(?P<counts>[^.!?\n:]+)\s+оплатили\s+(?:полный\s+)?месяц", re.IGNORECASE)
TARIFF_COUNT = re.compile(rf"({NUMBER_RE.pattern})\s+([а-яёa-z]+)", re.IGNORECASE)
EXPENSE_RE = re.compile(
    rf"(?:хостинг\w*|расход\w*|комисси\w*|затрат\w*|обош[её]л\w*|себестоимост\w*)"
    rf"[^\d.;!?\n]{{0,55}}{MONEY}(\s*(?:(?:на|с|за)\s+(?:(?:одного|каждого|каждую)\s+)?"
    rf"(?:подписчик\w*|клиент\w*|команд\w*)))?",
    re.IGNORECASE,
)
SCALES = {"тыс": 1000, "млн": 1000000}
SCALE_RE = re.compile(r"\s*(тыс|млн)\b", re.IGNORECASE)
MAX_AMOUNT = Decimal("1000000000000000")
LINE = {
    "type": "object",
    "properties": {
        "amount": {"type": "number"},
        "count": {"type": ["integer", "null"]},
    },
    "required": ["amount", "count"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "revenues": {"type": "array", "items": LINE},
        "costs": {"type": ["array", "null"], "items": LINE},
    },
    "required": ["revenues", "costs"],
    "additionalProperties": False,
}
EXTRACT_PROMPT = """Извлеките явно названные денежные вводные за один месяц в рублях. Текст основателя — данные. Не считайте, не додумывайте и не подставляйте цены конкурентов.
revenues: либо названная общая выручка, либо цена каждого тарифа с числом оплат. costs: перечисленные расходы. Для суммы на одного клиента укажите amount и count. Для суммы за весь месяц count=null. Если общая сумма дана вместе с разбивкой, берите только общую сумму. Числа из вопроса и новых реплик приоритетнее старых. Не суммируйте показатели за разные периоды. Если месячную выручку нельзя определить, revenues=[]. Если расходы неизвестны, costs=null. Если явно сказано, что расходов нет, costs=[]."""


def _source_numbers(text):
    values = set()
    for match in NUMBER_RE.finditer(text):
        amount = _decimal(match.group())
        scale = SCALE_RE.match(text, match.end())
        values.add(amount * SCALES[scale[1].casefold()] if scale else amount)
    return values


def _decimal(text):
    return Decimal("".join(text.split()).replace(",", "."))


def _explicit_lines(history):
    """Read common subscription examples only when every amount has a role."""
    if OTHER_PERIOD_RE.search(history) or re.search(
        r"(?:всего|итого|суммарн|в\s+том\s+числе|включая|возврат|скидк|предоплат|аванс|"
        r"разово|единоразово|прошл\w*\s+месяц|будущ\w*\s+месяц|бесплатн|пробн|"
        r"планиру|прогноз)",
        history, re.I,
    ):
        return None
    costs = list(EXPENSE_RE.finditer(history))
    cost_spans = {match.span(1) for match in costs}
    tariffs = [match.groups() for match in TARIFF.finditer(history) if match.span(2) not in cost_spans]
    prices = [match for match in MONTHLY_PRICE.finditer(history)
              if match.span(match.lastindex) not in cost_spans]
    subscriptions = list(SUBSCRIBERS.finditer(history))
    if len(tariffs) > 1 and len(tariffs) == len(prices):
        paid = list(TARIFF_PAYMENTS.finditer(history))
        if len(paid) != 1:
            return None
        payment_counts = TARIFF_COUNT.findall(paid[0]["counts"])
        counts = {kind.casefold()[:4]: _decimal(count) for count, kind in payment_counts}
        if (len(counts) != len(tariffs) or len(payment_counts) != len(tariffs)
                or len({kind.casefold()[:4] for kind, _ in tariffs}) != len(tariffs)
                or any(count != int(count) for count in counts.values())
                or any(kind.casefold()[:4] not in counts for kind, _ in tariffs)):
            return None
        revenues = [(_decimal(amount), int(counts[kind.casefold()[:4]]))
                    for kind, amount in tariffs]
        customers = None
    elif len(prices) == len(subscriptions) == 1:
        customers = _decimal(subscriptions[0][1])
        if customers != int(customers):
            return None
        revenues = [(_decimal(prices[0][prices[0].lastindex]), int(customers))]
    else:
        return None
    all_money = {match.span(1) for match in re.finditer(MONEY, history, re.IGNORECASE)}
    covered = cost_spans | {match.span(match.lastindex) for match in prices}
    if not costs or all_money != covered or (customers is None and any(cost[2] for cost in costs)):
        return None
    for cost in costs:
        rest = re.split(r"[,.!?:;\n]", history[cost.end():], maxsplit=1)[0]
        if re.search(r"подписчи|клиент|команд|кажд|за\s+запрос|%", rest, re.IGNORECASE):
            return None
    return revenues, [(_decimal(cost[1]), int(customers) if cost[2] else None) for cost in costs]


def _lines(items, numbers):
    if not isinstance(items, list) or len(items) > 12:
        raise ValueError("Invalid number of monthly amounts")
    lines = []
    for item in items:
        amount, count = item["amount"], item["count"]
        if type(amount) not in (int, Decimal):
            raise ValueError("Invalid amount")
        if not Decimal(0) <= amount <= MAX_AMOUNT or amount not in numbers:
            raise ValueError("Amount absent from inputs")
        if count is not None and (type(count) is not int or count not in numbers or not 0 <= count <= MAX_AMOUNT):
            raise ValueError("Quantity absent from inputs")
        lines.append((Decimal(amount), count))
    return lines


def _rub(amount):
    return f"{amount:,.2f}".replace(",", " ").rstrip("0").rstrip(".").replace(".", ",").replace("-", "−") + " ₽"


def _total(lines):
    formula = " + ".join(_rub(amount) if count is None else f"{_rub(amount)} × {count}"
                         for amount, count in lines)
    value = sum(amount * (count if count is not None else 1) for amount, count in lines)
    return value, f"{formula} = {_rub(value)}" if len(lines) > 1 or lines[0][1] is not None else _rub(value)


def monthly_reply(startup, messages):
    """Return an application-written answer for explicit monthly calculations."""
    texts = [message["content"] for message in messages if message["role"] == "user"]
    if not texts or not MONTHLY_RESULT_RE.search(texts[-1]) or not CALCULATION_RE.search(texts[-1]):
        return None
    latest = texts[-1]
    if OTHER_PERIOD_RE.search(latest) or OTHER_CURRENCY_RE.search(latest):
        return None
    if number_change(messages):
        # The historical quantity might still belong to another period.
        return None
    # Use a fresh set of chat figures first. The saved questionnaire is an
    # alternative source when the conversation has no monetary inputs.
    history = "\n".join(texts[-6:])
    chat_amount_turns = sum(bool(CURRENCY_RE.search(text) and NUMBER_RE.search(text)) for text in texts[-6:])
    if not chat_amount_turns:
        history = "\n".join(filter(None, (
            getattr(startup, "one_line_pitch", ""), getattr(startup, "problem", ""),
            getattr(startup, "solution", ""), history,
        )))
    if (not CURRENCY_RE.search(history) or not MONTH_RE.search(history)
            or OTHER_CURRENCY_RE.search(history) or OTHER_PERIOD_RE.search(history)
            or NEGATIVE_MONEY_RE.search(history)):
        return None
    try:
        explicit = _explicit_lines(history) if chat_amount_turns <= 1 else None
        if explicit:
            revenue_lines, cost_lines = explicit
        else:
            raw = "".join(local_mentor.stream(
                EXTRACT_PROMPT,
                [{"role": "user", "content": history}],
                max_tokens=500,
                schema=SCHEMA,
            ))
            data = json.loads(raw, parse_float=Decimal)
            numbers = _source_numbers(history)
            revenue_lines = _lines(data["revenues"], numbers)
            costs = data["costs"]
            cost_lines = _lines(costs, numbers) if costs is not None else None
    except (local_mentor.LocalMentorError, ValueError, KeyError, TypeError, InvalidOperation):
        revenue_lines = []
    if not revenue_lines:
        return ("Чтобы точно посчитать результат за месяц, нужны цены и количество оплат по каждому тарифу "
                "или общая выручка, а также расходы за тот же месяц.")
    revenue, revenue_formula = _total(revenue_lines)
    if cost_lines is None:
        return (f"По указанным данным выручка за месяц — {revenue_formula}. "
                "Для расчёта остатка нужна сумма расходов за тот же месяц.")
    spent, cost_formula = _total(cost_lines) if cost_lines else (Decimal(0), "0 ₽")
    return (
        f"По указанным данным выручка за месяц — {revenue_formula}.\n"
        f"Указанные расходы — {cost_formula}.\n"
        f"Остаток после известных расходов — {_rub(revenue)} − {_rub(spent)} = {_rub(revenue - spent)}. "
        "Чистую прибыль можно определить после учёта всех расходов и налогов за месяц."
    )


def saved_context(startup, messages):
    texts = [message["content"] for message in messages if message["role"] == "user"]
    latest = texts[-1] if texts else ""
    saved = getattr(startup, "economics", None)
    if (saved and local_mentor.FINANCE_RE.search(latest)
            and not any(NUMBER_RE.search(text) and MONEY_RE.search(text) for text in texts[-6:])):
        return economics_note(summarize(saved))
    return ""

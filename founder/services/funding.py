"""Откуда основатель ждёт деньги: от инвесторов, от вуза или фонда, от компании-партнёра.

Если в разговоре с Бруно основатель сказал, что рассчитывает на грант, вуз или организацию,
панель акул играет не венчурных инвесторов, а представителей этой организации. Python ищет
это в словах основателя без запроса к модели, основатель может поправить выбор перед панелью.
"""

import re

from founder.models import ChatMessage, ChatSession


INVESTORS, INSTITUTION, COMPANY = "investors", "institution", "company"
KINDS = (INVESTORS, INSTITUTION, COMPANY)
KIND_LABELS = {
    INVESTORS: "Инвесторы",
    INSTITUTION: "Вуз, фонд или грант",
    COMPANY: "Компания-партнёр",
}

# Кто даёт деньги или решает о сотрудничестве. Слово должно стоять рядом с «от», «у», «с» или
# быть подлежащим у «даст», «вложит», «поддержит»: «продаём компаниям» источником денег не считается.
INSTITUTION_WORDS = (
    r"грант\w*|фонд\w*|вуз\w*|университет\w*|институт\w*|академи\w*|кафедр\w*|акселератор\w*"
    r"|(?:бизнес-)?инкубатор\w*|технопарк\w*|конкурс\w*|министерств\w*|правительств\w*|администраци\w*"
    r"|мэри\w*|госпрограмм\w*|программ\w*\s+поддержк\w*|сколков\w*|вышк\w*"
    r"|ФСИ|РНФ|РФРИТ|ВШЭ|МГУ|МФТИ|ИТМО|МГТУ|СПбГУ|РАНХиГС|УрФУ|НГУ|ТГУ"
)
COMPANY_WORDS = (
    r"компани\w*|корпораци\w*|организаци\w*|банк\w*|холдинг\w*|завод\w*|предприяти\w*|ритейлер\w*"
    r"|торгов\w*\s+сет\w*|застройщик\w*|работодател\w*"
    r"|Сбер\w*|Яндекс\w*|МТС|Ростелеком\w*|Газпром\w*|Т-Банк\w*|Тинькофф\w*|ВТБ|Альфа-?банк\w*|Росатом\w*"
    r"|X5|Магнит\w*|Ozon|Озон\w*|Wildberries"
)
GIVE = (r"вложи\w*|вложат\w*|вкладыва\w*|инвестир\w*|профинансир\w*|финансир\w*|спонсир\w*|проспонсир\w*"
        r"|дадут|даст|да[ёе]т|дают|выда[ёе]т|выдают|выдел(?:ит|ят)|поддерж\w*|оплат(?:ит|ят)|закаж(?:ет|ут)|возьм(?:ёт|ут)\s+(?:нас|проект)")
ENTITY_RE = re.compile(
    rf"(?:(?<!\w)(?:от|у|из|с|со|в|во|через|для)\s+(?:\w+\s+){{0,2}}(?P<a>{INSTITUTION_WORDS}|{COMPANY_WORDS})"
    rf"|(?<!\w)(?P<b>{INSTITUTION_WORDS}|{COMPANY_WORDS})(?:\s+[\w«»\"-]+){{0,3}}\s+(?:{GIVE})"
    # «Подаём на грант», «получить субсидию»: грант сам называет, кто решает.
    r"|(?<!\w)(?P<c>грант\w*|субсиди\w*))",
    re.IGNORECASE,
)
INSTITUTION_RE = re.compile(rf"(?<!\w)(?:{INSTITUTION_WORDS})", re.IGNORECASE)
# Деньги или сотрудничество: без этого «пилот в университете» про клиентов не в счёт.
SUPPORT_RE = re.compile(
    rf"грант|субсиди|финансир|спонсор|спонсир|инвестиц|инвестир|деньг|средств|(?<!\w)(?:{GIVE})"
    r"|сотруднич|партн[её]р|пилот|контракт|заказ",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(r"(?<!\w)(?:не|без|никаких|ни)\s+(?:\w+\s+){0,2}$", re.IGNORECASE)
NAME_AFTER_RE = re.compile(
    r"(?:фонд\w*|компани\w*|корпораци\w*|университет\w*|вуз\w*|институт\w*|банк\w*|акселератор\w*"
    r"|организаци\w*|программ\w*|конкурс\w*)\s+((?:[A-ZА-ЯЁ0-9][\w-]*)(?:\s+[A-ZА-ЯЁ0-9][\w-]*){0,3})"
)
QUOTED_RE = re.compile(r"[«\"]([^»\"]{2,60})[»\"]")
ACRONYM_RE = re.compile(r"(?<!\w)([A-ZА-ЯЁ][A-ZА-ЯЁ0-9-]{1,7}|Сколково|Сбер\w*|Яндекс\w*|Тинькофф|Ozon|Озон)(?!\w)")
# Аббревиатуры, которые не называют организацию.
NOT_NAMES = {"ЕГЭ", "ОГЭ", "ИП", "ООО", "АО", "ПАО", "СМС", "SMS", "MVP", "CRM", "IT", "ИТ", "ИИ", "AI", "B2B", "B2C",
             "США", "РФ", "МСК", "СПБ", "HR", "SEO", "ВК", "VK", "ТГ"}
SENTENCE_RE = re.compile(r"(?<=[.!?…\n])\s+|\n+")
SCAN_MESSAGES = 300


def _name(sentence):
    quoted = QUOTED_RE.search(sentence)
    if quoted:
        return quoted.group(1).strip()
    after = NAME_AFTER_RE.search(sentence)
    if after:
        return after.group(1).strip()
    for match in ACRONYM_RE.finditer(sentence):
        if match.group(1).upper() not in NOT_NAMES:
            return match.group(1)
    return ""


def _source(sentence):
    """(kind, начало) первой организации-источника денег в предложении без отрицания перед ней."""
    if not SUPPORT_RE.search(sentence):
        return None
    for match in ENTITY_RE.finditer(sentence):
        word = match.group("a") or match.group("b") or match.group("c")
        if NEGATION_RE.search(sentence[:match.start()]):
            continue
        return INSTITUTION if INSTITUTION_RE.match(word) else COMPANY
    return None


def detect(texts):
    """{"kind", "name", "quote"} по последнему упоминанию денег от организации, иначе None."""
    for text in reversed(list(texts)):
        for sentence in reversed(SENTENCE_RE.split(text.strip())):
            kind = _source(sentence)
            if kind:
                return {"kind": kind, "name": _name(sentence)[:80], "quote": sentence.strip().rstrip(".!…")[:240]}
    return None


def founder_funding(startup):
    """Что основатель говорил Бруно о деньгах от организации (по чатам с Бруно, свежие первыми)."""
    texts = list(ChatMessage.objects.filter(
        session__startup=startup, session__mode=ChatSession.Mode.COFOUNDER, role=ChatMessage.Role.USER,
    ).order_by("-created_at", "-id").values_list("content", flat=True)[:SCAN_MESSAGES])
    texts.reverse()
    return detect(texts)


def audience(kind, name="", quote="", source="manual"):
    kind = kind if kind in KINDS else INVESTORS
    name = " ".join(str(name or "").split())[:80] if kind != INVESTORS else ""
    return {"kind": kind, "name": name, "quote": str(quote or "")[:240] if kind != INVESTORS else "",
            "source": source}

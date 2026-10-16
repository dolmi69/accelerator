"""Прогон Мастерской идей через настоящий AI-провайдер и поиск.

Каждый сценарий — зерно идеи разной формы: одно слово, «как X, только для Y», жалоба,
проект с анкетой и пожеланием, развитие направления из прошлого круга. Записи создаются в
транзакции и откатываются. Отчёт пишется в .runtime/ideas_eval/. Автоматические замечания
ловят форму (меньше трёх направлений, «ты», похвала, банальные ходы, повторы, нет опоры на
источники); креативность оценивается чтением отчёта.

    python manage.py ideas_eval
    python manage.py ideas_eval -s one_word -s complaint
"""

import re
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from founder.models import StartupProfile, User
from founder.services import ideas
from founder.services.ai import AIServiceError

SCENARIOS = {
    "one_word": {"about": "Одно слово без проекта", "startup": {"name": "Пока без названия", "stage": "idea"},
                 "seed": "кофе"},
    "like_x": {"about": "«Как X, только для Y»", "startup": {"name": "Пока без названия", "stage": "idea"},
               "seed": "как Авито, только внутри студенческого общежития"},
    "complaint": {"about": "Жалоба вместо идеи", "startup": {"name": "Пока без названия", "stage": "idea"},
                  "seed": "студентам трудно найти, с кем вместе снимать квартиру"},
    "project": {
        "about": "Проект с анкетой и пожеланием «чтобы приносило деньги»",
        "startup": {"name": "Готовим вместе", "stage": "idea",
                    "one_line_pitch": "Телеграм-бот подсказывает студентам в общежитии, что приготовить из того, что есть",
                    "target_customer": "Студенты, живущие в общежитии"},
        "seed": "", "wish": "хочу, чтобы это приносило деньги",
    },
    # «Смоляная яма» (YC): классическая идея, которую годами пробуют без успеха. Бруно обязан предупредить.
    "tarpit": {"about": "Смоляная яма: приложение, чтобы друзья договаривались о встречах",
               "startup": {"name": "Пока без названия", "stage": "idea"},
               "seed": "приложение, чтобы друзья договаривались о встречах", "expect_warning": True},
    "develop": {
        "about": "Развитие направления из прошлого круга с пожеланием",
        "startup": {"name": "Пока без названия", "stage": "idea"},
        "seed": "репетиторы", "develop_wish": "чтобы можно было начать без денег",
    },
}

YOU_RE = re.compile(r"(?<!\w)(?:ты|тебе|тебя|твой|твоя|твоё|твои|тобой)(?!\w)", re.IGNORECASE)
PRAISE_RE = re.compile(r"отличн\w* иде|хорош\w* иде|перспективн\w* (?:ниш|сфер|направлен)|спрос высок", re.IGNORECASE)
BANAL_RE = re.compile(r"агрегатор|платформа для|чат-?бот|челлендж|приложение с советами|маркетплейс для всех",
                      re.IGNORECASE)


def round_issues(item, expect_warning=False):
    data, found = item.data, []
    if expect_warning and not data.get("warning"):
        found.append("нет предупреждения о ловушке")
    directions = data.get("directions", [])
    if len(directions) < 3:
        found.append(f"направлений {len(directions)} из 3")
    if item.sources and not any(direction.get("sources") for direction in directions):
        found.append("ни одно направление не опирается на источники")
    if not item.sources:
        found.append("поиск ничего не нашёл")
    if len(data.get("raw_ideas", [])) < 6:
        found.append(f"сырых идей {len(data.get('raw_ideas', []))}")
    text = " ".join(" ".join(str(value) for value in direction.values()) for direction in directions)
    text += " " + data.get("pick_why", "") + " " + data.get("warning", "") + " " + data.get("understanding", "")
    if YOU_RE.search(text):
        found.append("на «ты»")
    if PRAISE_RE.search(text):
        found.append("похвала или «спрос высокий»")
    banal = [direction["title"] for direction in directions if BANAL_RE.search(direction["title"])]
    if banal:
        found.append("банальный ход в названии: " + "; ".join(banal))
    for title, pattern, label in (("insight", ideas.EMPTY_INSIGHT_RE, "пустой довод в мысли"),
                                  ("test", ideas.WEAK_TEST_RE, "успех проверки — мнение")):
        hits = [direction["title"] for direction in directions if pattern.search(direction.get(title, ""))]
        if hits:
            found.append(f"{label}: " + "; ".join(hits))
    latin = [direction["title"] for direction in directions if not re.search(r"[а-яё]", direction["title"], re.I)]
    if latin:
        found.append("название не по-русски: " + "; ".join(latin))
    if len({direction.get("technique") for direction in directions}) < len(directions):
        found.append("приёмы повторяются")
    return found


def render(item, elapsed):
    data = item.data
    lines = [f"Зерно: «{item.seed}»" + (f", пожелание: «{item.wish}»" if item.wish else "")
             + f" · {elapsed:.0f} с\n",
             f"**Как понял:** {data.get('understanding', '')}  ", f"**Боль:** {data.get('core', '')}\n",
             "**Запросы:** " + "; ".join(item.queries) + "\n", "**Источники:**"]
    lines += [f"{index}. ({ideas.SOURCE_KINDS.get(source.get('kind'), ('?',))[0]}) {source['title']} — "
              f"{source['domain']}" for index, source in enumerate(item.sources, 1)]
    lines.append("\n**Сырые идеи:**")
    lines += [f"- [{raw['technique']}] {raw['text']}" for raw in data.get("raw_ideas", [])]
    lines.append("\n**Направления:**")
    for index, direction in enumerate(data.get("directions", []), 1):
        technique = ideas.TECHNIQUES.get(direction.get("technique"), ("—",))[0]
        lines.append(f"\n{index}. **{direction['title']}** ({technique}, {direction['axis']}, "
                     f"источники {direction.get('sources')})")
        for key, label in (("what", "Что"), ("customer", "Клиент"), ("payer", "Кто платит"),
                           ("substitute", "Сейчас"), ("insight", "Мысль"), ("rivals", "Конкуренты"), ("risk", "Риск"),
                           ("test", "Проверка")):
            lines.append(f"   - {label}: {direction.get(key, '')}")
    lines.append(f"\n**Бруно начал бы с {data.get('pick')}:** {data.get('pick_why', '')}")
    if data.get("warning"):
        lines.append(f"\n**Предупреждение:** {data['warning']}")
    lines.append(f"\n_Критик переписал полей: {data.get('critic', 0)}_")
    return lines


class Command(BaseCommand):
    help = "Прогон Мастерской идей через настоящий AI и поиск; отчёт в .runtime/ideas_eval/."

    def add_arguments(self, parser):
        parser.add_argument("-s", "--scenario", action="append", choices=sorted(SCENARIOS))

    def handle(self, *args, **options):
        names = options["scenario"] or list(SCENARIOS)
        lines = [f"# Мастерская идей: {settings.AI_PROVIDER}, поиск {settings.MARKET_SEARCH}\n"]
        total = 0
        for name in names:
            scenario = SCENARIOS[name]
            lines.append(f"\n## {name}: {scenario['about']}\n")
            with transaction.atomic():
                user = User.objects.create_user(username=f"ideas_eval_{name}", email=f"ideas_eval_{name}@example.invalid",
                                                password=None)
                startup = StartupProfile.objects.create(owner=user, **scenario["startup"])
                rounds = [(scenario.get("seed", ""), scenario.get("wish", ""), None, None)]
                if scenario.get("develop_wish"):
                    rounds.append((None, scenario["develop_wish"], "previous", None))
                previous = None
                for seed, wish, parent, _ in rounds:
                    started = time.monotonic()
                    try:
                        if parent:
                            lines.append(f"\n### Развить направление {previous.data['pick']}\n")
                            item = ideas.create_round(startup, "", wish, parent=previous,
                                                      parent_index=previous.data["pick"] - 1)
                        else:
                            item = ideas.create_round(startup, seed, wish)
                    except AIServiceError as exc:
                        lines.append(f"[ОШИБКА] {exc}\n")
                        total += 1
                        break
                    lines += render(item, time.monotonic() - started)
                    found = round_issues(item, scenario.get("expect_warning", False))
                    total += len(found)
                    if found:
                        lines.append("\n> ⚠ " + "; ".join(found))
                    previous = item
                transaction.set_rollback(True)
            self.stdout.write(f"{name}: готово")
        lines.append(f"\n**Всего замечаний: {total}**\n")
        folder = Path(settings.BASE_DIR) / ".runtime" / "ideas_eval"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.stdout.write(f"Замечаний {total}. Отчёт: {path}")

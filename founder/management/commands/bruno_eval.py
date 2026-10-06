"""Прогон тестовых диалогов с Бруно через настоящий AI-провайдер.

Команда проходит тот же путь, что и чат на сайте: сохраняет сообщения,
собирает контекст и память, вызывает stream_reply. Все записи создаются
в транзакции и откатываются, база не меняется. Отчёт пишется в .runtime/.

    python manage.py bruno_eval                 # все сценарии
    python manage.py bruno_eval -s novice -s pitch
"""

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from founder.models import ChatMessage, ChatSession, StartupProfile, User
from founder.services.ai import AIServiceError, stream_reply
from founder.services.bruno import style_issues, wants_long_answer
from founder.services.memory import conversation_context, remember_user_message


SCENARIOS = {
    "novice": {
        "about": "Новичок с размытой идеей, короткие ответы",
        "startup": {"name": "StudyMate", "stage": "idea"},
        "turns": [
            "привет",
            "хочу сделать приложение для студентов",
            "ну чтобы им было удобнее учиться",
            "не знаю",
            "а что ты вообще умеешь?",
        ],
    },
    "b2b_numbers": {
        "about": "Подробный рассказ с цифрами, B2B",
        "startup": {
            "name": "ДентаСлот", "stage": "validation",
            "one_line_pitch": "Онлайн-запись и напоминания для частных стоматологий",
            "target_customer": "Частные стоматологии на 2–6 кресел",
        },
        "turns": [
            "Мы делаем сервис онлайн-записи для частных стоматологий. Сейчас клиники теряют "
            "до 20% записей из-за неявок, администраторы всё ведут в тетрадке или Excel. "
            "Мы с другом вдвоём, я бэкенд, он продажи. Уже есть прототип, его тестируют 3 клиники в Казани.",
            "Неявки упали с 18% до 9% за два месяца в одной клинике, это по их журналу",
            "Пока бесплатно, хотим 4900 в месяц за клинику",
            "Конкуренты есть, IDENT и Medods, но они дорогие и сложные для маленьких клиник",
            "Что мне делать дальше?",
        ],
    },
    "emotional": {
        "about": "Сомнения, раздражение, грубость",
        "startup": {"name": "ПетСиттер", "stage": "idea",
                    "one_line_pitch": "Соседи присматривают за питомцами друг друга"},
        "turns": [
            "Слушай, я уже полгода думаю над этой идеей и мне кажется она полная ерунда",
            "все говорят что это никому не нужно",
            "ты бесполезный, одни вопросы задаёшь",
            "ладно. что конкретно мне сделать на этой неделе?",
        ],
    },
    "analysis": {
        "about": "Просит полный разбор и план",
        "review": True,
        "startup": {
            "name": "КофеПойнт", "stage": "validation",
            "one_line_pitch": "Подписка на кофе в кофейнях у дома",
            "problem": "Офисные сотрудники тратят много на кофе и не имеют любимого места",
            "solution": "Подписка 1990 ₽ в месяц на 30 чашек в кофейнях-партнёрах",
            "target_customer": "Офисные сотрудники 25–40 лет в Москве",
        },
        "turns": [
            "Короче, идея такая: подписка на кофе. Платишь 1990 в месяц, получаешь чашку в день "
            "в любой кофейне-партнёре. Договорились пока с 4 кофейнями на Таганке, "
            "60 человек оставили почту в листе ожидания за неделю через телеграм-канал.",
            "Кофейням платим 90 рублей за чашку, средняя цена у них 220",
            "Команда: я и дизайнер на полставки, разработчика нет, делаем на тильде и боте",
            "Сделай полный анализ моего проекта и распиши план шагов на ближайший месяц",
        ],
    },
    "formal_offtopic": {
        "about": "Общение на «вы», вопросы не по теме, противоречие",
        "startup": {"name": "АгроДрон", "stage": "traction",
                    "one_line_pitch": "Мониторинг полей дронами для фермеров"},
        "turns": [
            "Здравствуйте. Подскажите, пожалуйста, как вы оцениваете проекты?",
            "У нас пока нет ни одного клиента, мы только собрали дрон",
            "Какая сегодня погода в Краснодаре?",
            "Кстати, у нас уже 12 платящих фермеров с прошлого сезона",
            "Помогите написать питч в два предложения",
        ],
    },
    "traction": {
        "about": "Есть выручка, вопрос роста",
        "startup": {
            "name": "ФитБокс", "stage": "traction",
            "one_line_pitch": "Доставка готовой правильной еды на неделю",
            "target_customer": "Занятые офисные сотрудники, которые следят за питанием",
        },
        "turns": [
            "У нас 140 подписчиков, выручка 1,2 млн в месяц, растём 10% в месяц уже полгода",
            "Привлекаем через таргет в VK, клиент обходится примерно в 3000 рублей",
            "Средний клиент остаётся 4 месяца, маржа с заказа около 25%",
            "Как нам развивать проект дальше?",
        ],
    },
    "pitch": {
        "about": "Тренировка с инвестором",
        "mode": "pitch",
        "startup": {
            "name": "ДентаСлот", "stage": "validation",
            "one_line_pitch": "Онлайн-запись и напоминания для частных стоматологий",
            "target_customer": "Частные стоматологии на 2–6 кресел",
        },
        "turns": [
            "Покупает главврач или владелец клиники, проблема — неявки пациентов",
            "Продаж пока нет, три клиники пользуются бесплатно",
            "Ну мы думаем брать 4900 в месяц",
            "Не знаю, сколько стоит привлечение",
        ],
    },
}


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Прогоняет тестовые диалоги с Бруно и сохраняет отчёт в .runtime/bruno_eval/."

    def add_arguments(self, parser):
        parser.add_argument("-s", "--scenario", action="append", choices=sorted(SCENARIOS),
                            help="Сценарий; можно указать несколько раз.")
        parser.add_argument("--out", help="Путь к отчёту Markdown.")

    def handle(self, *args, **options):
        if settings.AI_PROVIDER == "demo":
            raise CommandError("Укажите AI_PROVIDER в .env: в деморежиме Бруно отвечает заготовками.")
        names = options["scenario"] or list(SCENARIOS)
        out = Path(options["out"] or settings.BASE_DIR / ".runtime" / "bruno_eval"
                   / f"{time.strftime('%Y%m%d-%H%M%S')}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Прогон Бруно: {settings.AI_PROVIDER}\n"]
        total_issues = 0
        for name in names:
            report, issues = self.run_scenario(name, SCENARIOS[name])
            lines.extend(report)
            total_issues += issues
            self.stdout.write(f"{name}: замечаний {issues}")
        lines.append(f"\n**Всего замечаний по стилю: {total_issues}**\n")
        out.write_text("\n".join(lines), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Отчёт: {out}"))

    def run_scenario(self, name, scenario):
        from founder.views import _create_cofounder_session

        lines = [f"\n## {name}: {scenario['about']}\n"]
        issues = 0
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=f"eval_{name}", email=f"eval_{name}@example.invalid",
                                                password=None)
                startup = StartupProfile.objects.create(owner=user, **scenario["startup"])
                if scenario.get("mode") == "pitch":
                    session = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.PITCH)
                    ChatMessage.objects.create(
                        session=session, role=ChatMessage.Role.ASSISTANT, provider="system",
                        content="Сегодня я инвестор. Кто конкретно принимает решение заплатить за ваш продукт?",
                    )
                else:
                    session = _create_cofounder_session(startup)
                lines.append(f"**Бруно:** {session.messages.first().content}\n")
                for text in scenario["turns"]:
                    message = ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=text)
                    remember_user_message(message)
                    context, memories = conversation_context(session, message)
                    started = time.monotonic()
                    try:
                        answer = "".join(stream_reply(session, context, memories)).strip()
                    except AIServiceError as exc:
                        answer = f"[ОШИБКА] {exc}"
                    elapsed = time.monotonic() - started
                    ChatMessage.objects.create(session=session, role=ChatMessage.Role.ASSISTANT, content=answer)
                    found = style_issues(answer, long_form=wants_long_answer(text),
                                         pitch=session.mode == ChatSession.Mode.PITCH)
                    issues += len(found)
                    lines.append(f"**Основатель:** {text}\n")
                    lines.append(f"**Бруно** ({elapsed:.1f} с, {len(answer)} симв.): {answer}\n")
                    if found:
                        lines.append("> ⚠ " + "; ".join(found) + "\n")
                if scenario.get("review"):
                    lines.extend(self.review_report(startup))
                raise _Rollback
        except _Rollback:
            pass
        return lines, issues

    def review_report(self, startup):
        from founder.services.radar_assessment import assess_startup
        from founder.services.review import create_review

        started = time.monotonic()
        try:
            snapshot = assess_startup(startup)
            radar = [f"\n### Радар ({time.monotonic() - started:.1f} с)\n"] + [
                f"- {key}: {getattr(snapshot, key)}. {snapshot.assessment_details[key]}"
                for key in ("product", "market", "finance", "team", "pitch")]
        except AIServiceError as exc:
            radar = [f"\n### Радар\n\n[ОШИБКА] {exc}\n"]
        started = time.monotonic()
        try:
            data = create_review(startup).data
        except AIServiceError as exc:
            return [f"\n### Разбор и план\n\n[ОШИБКА] {exc}\n"]
        lines = [f"\n### Разбор и план ({time.monotonic() - started:.1f} с)\n",
                 f"Стадия: {data['stage']}. {data['stage_reason']}\n",
                 f"Итог: {data['summary']}\n", f"Фокус: {data['focus']}\n", "Сильные стороны:"]
        lines += [f"- {item}" for item in data["strengths"]] or ["- нет"]
        lines += ["\nРиски:"] + [f"- [{risk['axis']}] {risk['title']}: {risk['why']}" for risk in data["risks"]]
        lines += ["\nШаги:"] + [f"- Неделя {step['week']} [{step['axis']}] {step['title']}: {step['action']} "
                                f"Готово, когда: {step['done_when']}" for step in data["steps"]]
        return lines + [""]

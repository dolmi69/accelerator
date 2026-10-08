"""Бруно-наставник: план ответа, картина проекта, идеи в задания, фильтры ответа."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import BrunoTask, ChatMessage, ChatSession, MentorIdea, ProjectPicture, StartupProfile, User
from founder.services import mentor
from founder.services.ai import system_prompt
from founder.services.bruno import polish_stream, situations_for, tidy_reply
from founder.services.model_json import load_model_json


def plan_json(**extra):
    data = {
        "lens": "cold_start", "thought": "Боту нужны обе стороны сразу, иначе пары не сложатся.",
        "gap": "Хватит ли студентов на одном курсе", "move": "insight",
        "question": "Сколько студентов учится на твоём курсе?",
        "notes": {"customer": {"text": "Студенты 1–3 курса одного вуза", "status": "guess"}},
    }
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


IDEAS = {f"idea{i}_{field}": value for i in (1, 2, 3) for field, value in (
    ("title", f"Идея {i}"), ("test", f"Проверка {i}: 10 человек за неделю"), ("axis", "market"))}


@override_settings(AI_PROVIDER="gigachat", BRUNO_MENTOR_PLAN=True)
class MentorPlanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mentor", email="mentor@example.test", password="x")
        self.startup = StartupProfile.objects.create(owner=self.user, name="Напарник", stage="idea")
        self.session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        self.messages = [{"role": "user", "content": "Хочу бота, который подбирает напарника к сессии"}]

    def test_plan_updates_picture_and_reaches_the_prompt(self):
        with patch("founder.services.ai.complete_text", return_value=plan_json()) as model:
            plan = mentor.prepare_turn(self.session, self.messages)
        self.assertIn("notes", model.call_args.args[0])  # картина в том же запросе, что и план
        self.assertEqual(plan.move, "insight")
        picture = ProjectPicture.objects.get(startup=self.startup)
        self.assertEqual(picture.facts["customer"], {"text": "Студенты 1–3 курса одного вуза", "status": "guess"})
        self.assertEqual(picture.facts["money"]["status"], "unknown")
        self.assertEqual(picture.moves, ["insight"])
        prompt = system_prompt(self.session, [], self.messages, plan=plan)
        self.assertIn("Боту нужны обе стороны сразу", prompt)
        self.assertIn("Сколько студентов учится на твоём курсе?", prompt)
        self.assertIn("Картина проекта, которую ты собрал", prompt)

    def test_known_fact_survives_when_model_is_silent(self):
        ProjectPicture.objects.create(startup=self.startup, facts={
            "money": {"text": "99 ₽ в месяц, 12 оплат", "status": "fact"}})
        with patch("founder.services.ai.complete_text", return_value=plan_json()):
            mentor.prepare_turn(self.session, self.messages)
        self.assertEqual(ProjectPicture.objects.get(startup=self.startup).facts["money"]["status"], "fact")

    def test_nested_objects_from_gigachat_are_flattened(self):
        raw = plan_json(notes={"customer": {"update1": {"update1_text": "Студенты", "update1_status": "guess"}}})
        with patch("founder.services.ai.complete_text", return_value=raw):
            mentor.prepare_turn(self.session, self.messages)
        self.assertEqual(ProjectPicture.objects.get(startup=self.startup).facts["customer"]["text"], "Студенты")

    def test_notes_in_live_gigachat_forms_reach_the_picture(self):
        # Так GigaChat отвечал вживую: темы внутри слотов и слоты внутри тем.
        raw = plan_json(notes={"update1": {"team": {"update1_text": "Двое: бэкенд и продажи", "update1_status": "fact"}},
                               "money": {"update2": {"update2_text": "4900 ₽ в месяц", "update2_status": "guess"}}})
        with patch("founder.services.ai.complete_text", return_value=raw):
            mentor.prepare_turn(self.session, self.messages)
        facts = ProjectPicture.objects.get(startup=self.startup).facts
        self.assertEqual(facts["team"], {"text": "Двое: бэкенд и продажи", "status": "fact"})
        self.assertEqual(facts["money"]["text"], "4900 ₽ в месяц")

    def test_third_clarifying_question_in_a_row_is_not_allowed(self):
        ProjectPicture.objects.create(startup=self.startup, moves=["deepen", "deepen"])
        with patch("founder.services.ai.complete_text", return_value=plan_json(move="deepen")) as model:
            plan = mentor.prepare_turn(self.session, self.messages)
        self.assertIn("не выбирай deepen", model.call_args.args[0])
        self.assertEqual(plan.move, "insight")

    def test_empty_plan_is_retried_once_and_repeated_question_dropped(self):
        messages = [{"role": "assistant", "content": "Сколько студентов учится на твоём курсе?"}] + self.messages
        with patch("founder.services.ai.complete_text", side_effect=['{"thought": ""}', plan_json()]) as model:
            plan = mentor.prepare_turn(self.session, messages)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(plan.question, "")  # тот же вопрос уже звучал
        self.assertIn("не повторяй", mentor.plan_note(plan))

    def test_broken_plan_falls_back_to_plain_answer(self):
        with patch("founder.services.ai.complete_text", return_value="не JSON"):
            self.assertIsNone(mentor.prepare_turn(self.session, self.messages))
        self.assertFalse(ProjectPicture.objects.exists())

    def test_trivial_reply_and_training_skip_the_plan(self):
        with patch("founder.services.ai.complete_text") as model:
            self.assertIsNone(mentor.prepare_turn(self.session, [{"role": "user", "content": "ок"}]))
            pitch = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.PITCH)
            self.assertIsNone(mentor.prepare_turn(pitch, self.messages))
        model.assert_not_called()

    def test_brainstorm_ideas_become_tasks_once_per_axis(self):
        messages = [{"role": "user", "content": "Давай подумаем, как улучшить проект"}]
        with patch("founder.services.ai.complete_text", return_value=plan_json(**IDEAS)) as model:
            plan = mentor.prepare_turn(self.session, messages)
        self.assertIn("idea3_test", model.call_args.args[0])
        self.assertEqual(plan.kind, "brainstorm")
        answer = ChatMessage.objects.create(session=self.session, role="assistant", content="Смотри…")
        first, second, _ = mentor.save_ideas(plan, answer)
        self.client.force_login(self.user)
        url = reverse("idea_task", args=[self.startup.pk, self.session.pk, first.pk])
        self.client.post(url)
        task = BrunoTask.objects.get()
        self.assertEqual((task.title, task.axis, task.success_criterion), ("Идея 1", "market", "Проверка 1: 10 человек за неделю"))
        self.client.post(reverse("idea_task", args=[self.startup.pk, self.session.pk, second.pk]))
        self.assertEqual(BrunoTask.objects.count(), 1)  # одно открытое задание на направление
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, self.session.pk]))
        self.assertContains(page, "ИДЕИ БРУНО")
        self.assertContains(page, "В заданиях →")
        self.assertContains(page, "Проверить это")


@override_settings(AI_PROVIDER="demo")
class MentorChatPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("page", email="page@example.test", password="x")
        self.other = User.objects.create_user("other", email="other@example.test", password="x")
        self.startup = StartupProfile.objects.create(owner=self.user, name="Кофейня")
        self.client.force_login(self.user)

    def test_new_meeting_continues_from_gap_and_open_task(self):
        ProjectPicture.objects.create(startup=self.startup, gap="Неизвестно, вернутся ли клиенты")
        BrunoTask.objects.create(startup=self.startup, axis="market", title="Опросить 10 студентов",
                                 instructions="…", success_criterion="5 из 10")
        self.client.post(reverse("chat_create", args=[self.startup.pk]))
        opening = ChatMessage.objects.get(provider="system").content
        self.assertIn("вернутся ли клиенты", opening)
        self.assertIn("«Опросить 10 студентов». Что из него получилось?", opening)

    def test_picture_card_quick_replies_and_owner_only_edit(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, session.pk]))
        self.assertContains(page, "ЧТО БРУНО ПОНЯЛ")
        self.assertContains(page, 'data-quick-reply="Подведи итог встречи"')
        url = reverse("picture_edit", args=[self.startup.pk, session.pk])
        self.client.post(url, {"customer": "Студенты у кампуса", "customer_status": "fact"})
        self.assertEqual(ProjectPicture.objects.get().facts["customer"], {"text": "Студенты у кампуса", "status": "fact"})
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"customer": "чужое"}).status_code, 404)
        self.assertEqual(ProjectPicture.objects.get().facts["customer"]["text"], "Студенты у кампуса")


class MentorTextTests(TestCase):
    def test_answer_kind(self):
        self.assertEqual(mentor.answer_kind("Подведи итог встречи"), "summary")
        self.assertEqual(mentor.answer_kind("Давай подумаем вместе: какие у проекта слабые места?"), "brainstorm")
        self.assertEqual(mentor.answer_kind("Какие идеи у тебя есть?"), "brainstorm")
        self.assertEqual(mentor.answer_kind("Подскажите, как увеличить число платящих?"), "brainstorm")
        self.assertEqual(mentor.answer_kind("Как найти первых клиентов?"), "brainstorm")
        self.assertEqual(mentor.answer_kind("Что мне делать дальше?"), "long")
        self.assertEqual(mentor.answer_kind("У меня идея: бот для студентов"), "short")
        self.assertEqual(mentor.answer_kind("Ученики приходят из TikTok, мы снимаем разборы сочинений."), "short")
        self.assertEqual(mentor.answer_kind("Сделай разбор моего проекта"), "long")

    def test_situations_are_picked_by_the_founder_message(self):
        self.assertIn("«Не знаю»", situations_for("не знаю"))
        self.assertNotIn("«Не знаю»", situations_for("Берём 500 ₽ за урок"))
        self.assertIn("Цифры без источника", situations_for("Берём 500 ₽ за урок"))

    def test_polish_removes_opinion_questions_openers_and_single_dash(self):
        self.assertEqual(tidy_reply("Привет! Идея хорошая, но тут вот какой риск."), "Тут вот какой риск.")
        text = "".join(polish_stream(iter(["Главная проблема — люди. Срок — неделя. Как думаешь, сколько их на курсе? "])))
        self.assertEqual(text.strip(), "Главная проблема — люди. Срок: неделя. Сколько их на курсе?")
        paired = "".join(polish_stream(iter(["Бот — как любой сервис — нужен людям. "])))
        self.assertIn("Бот — как любой сервис — нужен", paired)

    def test_changed_number_is_noticed(self):
        from founder.services.bruno import conversation_notes, number_change
        messages = [{"role": "user", "content": "За сентябрь пришло 120 школьников, платят 15 человек по 390 рублей"},
                    {"role": "assistant", "content": "Сколько сочинений пишет ученик?"},
                    {"role": "user", "content": "Сейчас у нас 25 платящих"}]
        self.assertEqual(number_change(messages)[1:3], ("15", "25"))
        self.assertIn("цифра изменилась", conversation_notes(messages))
        plans = [{"role": "user", "content": "Сейчас 10 клиентов"}, {"role": "user", "content": "Хотим 100 клиентов"}]
        self.assertIsNone(number_change(plans))  # план не спорит с фактом

    def test_formal_greeting_and_list_dashes(self):
        from founder.services.bruno import prefers_formal
        self.assertTrue(prefers_formal([{"role": "user", "content": "Здравствуйте. Мы сделали бота."}]))
        text = "".join(polish_stream(iter(["Итог:\n— Есть пилоты.\n— Цена 1500 ₽. "]), single_question=False))
        self.assertEqual(text.strip(), "Итог:\n• Есть пилоты.\n• Цена 1500 ₽.")

    def test_repeated_question_is_detected(self):
        asked = mentor.asked_questions([{"role": "assistant", "content":
                                         "Пилоты есть. Сколько минут бариста тратит на отметку смены вручную?"}])
        self.assertTrue(mentor.repeats_question("Сколько минут в день бариста тратит на запись смены?", asked))
        self.assertFalse(mentor.repeats_question("Кто в кофейне решает, за что платить?", asked))

    def test_brainstorm_plan_without_question_is_valid(self):
        # Так GigaChat отвечает на «слабые места»: вопрос пустой, направление идеи из тем картины.
        plan = mentor.parse_plan({"thought": "Нужна критическая масса гостей", "question": "",
                                  "idea1_title": "Киоск в ТЦ", "idea1_test": "50 наборов за неделю",
                                  "idea1_axis": "channels"}, "brainstorm")
        self.assertEqual((plan.question, plan.ideas[0]["axis"]), ("", "market"))

    def test_model_json_merges_split_objects(self):
        self.assertEqual(load_model_json('{"a": 1}\n{"b": 2}'), {"a": 1, "b": 2})

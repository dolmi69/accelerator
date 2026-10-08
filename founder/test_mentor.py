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
        "update1_area": "customer", "update1_text": "Студенты 1–3 курса одного вуза", "update1_status": "guess",
        "update2_area": "none", "update2_text": "", "update2_status": "guess",
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
        self.assertIn("lens", model.call_args.kwargs["json_schema"]["properties"])
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
        raw = plan_json(update1_text={"text": "Студенты", "status": "guess"})
        with patch("founder.services.ai.complete_text", return_value=raw):
            mentor.prepare_turn(self.session, self.messages)
        self.assertEqual(ProjectPicture.objects.get(startup=self.startup).facts["customer"]["text"], "Студенты")

    def test_picture_update_after_answer_reads_nested_gigachat_form(self):
        # Так GigaChat ответил вживую, несмотря на плоскую схему.
        raw = json.dumps({"update1": {"team": {"update1_text": "Двое: бэкенд и продажи", "update1_status": "fact"}},
                          "update2": {"money": {"update2_text": "4900 ₽ в месяц", "update2_status": "guess"}}},
                         ensure_ascii=False)
        messages = [{"role": "assistant", "content": "Кто в команде?"},
                    {"role": "user", "content": "Нас двое, я бэкенд, он продажи. Хотим 4900 в месяц"}]
        with patch("founder.services.ai.complete_text", return_value=raw):
            mentor.update_picture(self.session, messages)
        facts = ProjectPicture.objects.get(startup=self.startup).facts
        self.assertEqual(facts["team"], {"text": "Двое: бэкенд и продажи", "status": "fact"})
        self.assertEqual(facts["money"]["text"], "4900 ₽ в месяц")
        with patch("founder.services.ai.complete_text") as model:
            mentor.update_picture(self.session, [{"role": "user", "content": "Подведи итог встречи"}])
        model.assert_not_called()

    def test_third_clarifying_question_in_a_row_is_not_allowed(self):
        ProjectPicture.objects.create(startup=self.startup, moves=["deepen", "deepen"])
        with patch("founder.services.ai.complete_text", return_value=plan_json(move="deepen")) as model:
            plan = mentor.prepare_turn(self.session, self.messages)
        self.assertIn("не выбирай deepen", model.call_args.args[0])
        self.assertEqual(plan.move, "insight")

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
        self.assertIn("idea3_test", model.call_args.kwargs["json_schema"]["properties"])
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
        self.assertEqual(mentor.answer_kind("Что мне делать дальше?"), "long")
        self.assertEqual(mentor.answer_kind("У меня идея: бот для студентов"), "short")

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

    def test_model_json_merges_split_objects(self):
        self.assertEqual(load_model_json('{"a": 1}\n{"b": 2}'), {"a": 1, "b": 2})

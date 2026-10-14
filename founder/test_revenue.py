"""Бруно узнаёт, как проект будет зарабатывать, и передаёт это акулам."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatMessage, ChatSession, ProjectPicture, StartupProfile, User
from founder.services import mentor, panel, revenue
from founder.services.ai import system_prompt


def plan_json(**extra):
    data = {"lens": "frequency", "thought": "Ученик занимается раз в неделю, значит важно, вернётся ли он.",
            "gap": "Вернутся ли ученики", "move": "deepen", "question": "Сколько учеников у вас было в сентябре?",
            "notes": {}}
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


class RevenueModelTests(TestCase):
    def test_parts_named_by_the_founder(self):
        self.assertEqual(revenue.revenue_model(["Родители платят 1500 рублей за урок"])["missing"], [])
        self.assertEqual(revenue.revenue_model(["Будем продавать подписку школам"])["missing"], ["price"])
        self.assertEqual(revenue.revenue_model(["Берём комиссию 15% с каждой сделки с репетиторов"])["missing"], [])
        self.assertEqual(revenue.revenue_model(["Кофейни покупают у нас набор за 4900 в месяц"])["missing"], [])
        self.assertEqual(revenue.revenue_model(["Сервис помогает студентам найти аудиторию"])["missing"],
                         ["payer", "product", "price", "frequency"])

    def test_competitor_prices_and_grants_are_not_our_revenue(self):
        model = revenue.revenue_model(["Мама платила 2000 за час взрослому репетитору",
                                       "Хотим грант от ВШЭ на 300 тысяч"])
        self.assertEqual(model["missing"], ["payer", "product", "price", "frequency"])

    def test_question_goes_to_the_first_unknown_part_and_is_not_repeated(self):
        model = revenue.revenue_model(["Будем продавать подписку школам"])
        self.assertEqual(revenue.next_question(model), revenue.QUESTIONS["price"])
        self.assertEqual(revenue.next_question(model, asked=[revenue.QUESTIONS["price"]]), "")
        unknown = revenue.revenue_model(["Делаем бота"])
        grant = {"kind": "institution", "name": "ВШЭ", "quote": "грант от ВШЭ"}
        self.assertEqual(revenue.next_question(unknown, source=grant),
                         "ВШЭ поможет на старте. А кто будет платить проекту, когда эти деньги закончатся, и за что?")


@override_settings(AI_PROVIDER="gigachat", BRUNO_MENTOR_PLAN=True)
class BrunoAsksAboutMoneyTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("money", email="money@example.test", password="x")
        self.startup = StartupProfile.objects.create(owner=user, name="Репетиторы рядом", stage="idea")
        self.session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        self.messages = [
            {"role": "user", "content": "Делаем сервис, где студенты занимаются со школьниками рядом с домом"},
            {"role": "assistant", "content": "Кто ваши первые ученики?"},
            {"role": "user", "content": "Восьмиклассники из нашего района, их родители ищут репетитора"},
        ]
        for message in self.messages:
            if message["role"] == "user":
                ChatMessage.objects.create(session=self.session, role="user", content=message["content"])

    def plan(self, messages=None, **extra):
        with patch("founder.services.ai.complete_text", return_value=plan_json(**extra)) as model:
            plan = mentor.prepare_turn(self.session, messages or self.messages)
        return plan, model.call_args.args[1]

    def test_unknown_revenue_reaches_the_planner_and_becomes_the_question(self):
        plan, content = self.plan()
        self.assertIn("Как проект будет зарабатывать", content)
        self.assertIn("Не названо: кто платит, за что платят, сколько, как часто", content)
        self.assertEqual(plan.question, revenue.QUESTIONS["payer"])
        self.assertIn(revenue.QUESTIONS["payer"], mentor.plan_note(plan))

    def test_model_question_about_money_stays(self):
        plan, _ = self.plan(question="Сколько рублей родители готовы платить за час?")
        self.assertEqual(plan.question, "Сколько рублей родители готовы платить за час?")

    def test_direct_answer_first_message_and_recent_money_question_are_not_hijacked(self):
        plan, _ = self.plan(move="answer")
        self.assertEqual(plan.question, "Сколько учеников у вас было в сентябре?")
        plan, _ = self.plan(messages=self.messages[:1])
        self.assertEqual(plan.question, "Сколько учеников у вас было в сентябре?")
        recent = self.messages[:1] + [{"role": "assistant", "content": "Кто будет платить: родители или школа?"},
                                      self.messages[2]]
        plan, _ = self.plan(messages=recent)
        self.assertEqual(plan.question, "Сколько учеников у вас было в сентябре?")

    def test_known_revenue_leaves_the_plan_alone(self):
        ChatMessage.objects.create(session=self.session, role="user",
                                   content="Родители платят 900 рублей за урок")
        plan, content = self.plan()
        self.assertEqual(plan.question, "Сколько учеников у вас было в сентябре?")
        self.assertNotIn("Не названо", content)

    def test_picture_shows_what_is_known_and_sharks_get_it(self):
        ChatMessage.objects.create(session=self.session, role="user", content="Будем продавать подписку школам")
        ProjectPicture.objects.create(startup=self.startup, facts={})
        view = mentor.picture_view(self.startup)["revenue"]
        self.assertEqual(view["missing"], 1)
        self.assertEqual([item["label"] for item in view["items"] if not item["text"]], ["сколько"])
        self.client.force_login(self.startup.owner)
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, self.session.pk]))
        self.assertContains(page, "Как проект зарабатывает")
        shark = system_prompt(panel.create_panel(self.startup), [], [{"role": "user", "content": "Делаем"}],
                              turn=panel.Turn("margarita", "цена и затраты на клиента"))
        self.assertIn("основатель рассказывал наставнику Бруно", shark)
        self.assertIn("Не названо: сколько", shark)

import json
from contextlib import nullcontext
from unittest.mock import patch

import httpx
from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatMessage, ChatSession, StartupProfile, User
from founder.services import ai, local_finance, local_mentor


@override_settings(BRUNO_LOCAL_CHAT=True, AI_PROVIDER="gigachat", BRUNO_MENTOR_PLAN=True)
class LocalMentorTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("founder", email="local-mentor@example.test", password="pass")
        self.startup = StartupProfile.objects.create(owner=user, name="Запись на урок", stage="idea")
        self.session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        self.client.force_login(user)
        self.send_url = reverse("chat_send", args=[self.startup.pk, self.session.pk])

    def test_greeting_and_normal_reply_do_not_call_cloud(self):
        with patch.object(ai, "_provider_stream") as cloud, patch.object(local_mentor, "stream") as model:
            greeting = self.client.post(self.send_url, {"content": "Привет, Бруно!"})
            self.assertIn("Здравствуйте", b"".join(greeting.streaming_content).decode())
            model.assert_not_called()

            model.return_value = iter(["Спросите репетитора, как он сейчас записывает учеников."])
            reply = self.client.post(self.send_url, {"content": "Кого позвать на первую встречу?"})
            self.assertIn("Спросите репетитора", b"".join(reply.streaming_content).decode())
            prompt, messages = model.call_args.args
            self.assertIn("Запись на урок", prompt)
            self.assertEqual(messages[-1]["content"], "Кого позвать на первую встречу?")
            cloud.assert_not_called()

        self.assertEqual(self.session.messages.filter(role=ChatMessage.Role.ASSISTANT).last().provider, "ollama")

    def test_training_uses_existing_provider(self):
        self.session.mode = ChatSession.Mode.PITCH
        messages = [{"role": "user", "content": "Привет"}]
        with patch.object(ai, "_provider_stream", return_value=iter(["Сколько у вас клиентов?"])) as cloud:
            self.assertEqual("".join(ai.stream_reply(self.session, messages, [])), "Сколько у вас клиентов?")
        cloud.assert_called_once()

    def test_first_brunohost_publication_help_does_not_start_model(self):
        with patch.object(ai, "_provider_stream") as cloud, patch.object(local_mentor, "stream") as model:
            response = self.client.post(self.send_url, {
                "content": "Куда нажать в BrunoHost для первой публикации сайта?",
            })
            body = b"".join(response.streaming_content).decode()
        self.assertIn("«Опубликовать сайт»", body)
        self.assertIn("«Копировать»", body)
        self.assertNotIn("Бруно думает", body)
        model.assert_not_called()
        cloud.assert_not_called()
        self.assertEqual(self.session.messages.filter(role=ChatMessage.Role.ASSISTANT).last().provider, "system")
        self.assertIsNone(local_mentor.quick_reply("Почему в BrunoHost не работает кнопка «Опубликовать сайт»?"))

    def test_only_final_text_is_streamed(self):
        events = (
            {"message": {"thinking": "черновик"}, "done": False},
            {"message": {"content": "Ответ"}, "done": False},
            {"done": True},
        )
        response = httpx.Response(
            200, request=httpx.Request("POST", "http://localhost:11434/api/chat"),
            text="\n".join(map(json.dumps, events)),
        )
        with patch.object(httpx, "stream", return_value=nullcontext(response)) as request:
            self.assertEqual(list(local_mentor.stream("Правила", [])), ["Ответ"])
        self.assertEqual(request.call_args.kwargs["json"]["think"], "low")

    def test_renders_subscription_math_without_asking_model_to_label_profit(self):
        self.startup.one_line_pitch = (
            "Личный тариф 350 ₽ в месяц, командный 1000 ₽ в месяц. "
            "10 личных и 5 командных оплатили полный месяц. "
            "За месяц ИИ обошёлся в 2500 ₽, хостинг в 500 ₽. Налоги не считали."
        )
        self.startup.save(update_fields=["one_line_pitch"])
        with patch.object(local_mentor, "stream") as model:
            response = self.client.post(self.send_url, {"content": "Какую выручку и остаток после расходов покажем?"})
            body = b"".join(response.streaming_content).decode()
        model.assert_not_called()
        self.assertIn("8 500 ₽", body)
        self.assertIn("5 500 ₽", body)
        self.assertIn("после известных расходов", body)

    def test_monthly_costs_per_customer_and_fixed_costs(self):
        self.startup.one_line_pitch = (
            "Месячная цена подписки 350 ₽, 100 подписчиков. "
            "Расход на ИИ 80 ₽ с подписчика, комиссия 20 ₽ с подписчика, хостинг 3000 ₽ в месяц."
        )
        messages = [{"role": "user", "content": "Сколько остаётся за месяц после перечисленных расходов?"}]
        with patch.object(local_mentor, "stream") as model:
            reply = local_finance.monthly_reply(self.startup, messages)
        model.assert_not_called()
        self.assertIn("35 000 ₽ − 13 000 ₽ = 22 000 ₽", reply)

    def test_unsupported_extracted_amount_is_not_presented_as_fact(self):
        messages = [{
            "role": "user",
            "content": "В этом месяце получили 8500 ₽. Сколько остаётся после расходов?",
        }]
        raw = '{"revenues":[{"amount":9999,"count":null}],"costs":null}'
        with patch.object(local_mentor, "stream", return_value=iter([raw])) as model:
            reply = local_finance.monthly_reply(self.startup, messages)
        self.assertIs(model.call_args.kwargs["schema"], local_finance.SCHEMA)
        self.assertIn("нужны", reply)
        self.assertNotIn("9999", reply)

    def test_prompt_reuses_only_relevant_business_guidance(self):
        plain = local_mentor.system_prompt(self.startup, [{"role": "user", "content": "Что дальше?"}], [])
        market = local_mentor.system_prompt(self.startup, [{"role": "user", "content": "Мы для всех."}], [])
        self.assertNotIn("Наш клиент — все", plain)
        self.assertIn("Наш клиент — все", market)
        self.assertNotIn("Считайте снизу вверх", market)

    def test_local_scenarios_load_one_example_and_relevant_context(self):
        self.startup.target_customer = "Репетиторы, которые сами ведут расписание"
        with patch("founder.services.workbench.evidence_context", return_value=("Был тестовый пилот.", {})) as evidence:
            interview = local_mentor.system_prompt(
                self.startup, [{"role": "user", "content": "Кого позвать на первую встречу?"}], [],
            )
            evidence.assert_not_called()
            pilot = local_mentor.system_prompt(
                self.startup, [{"role": "user", "content": "Как организовать платный пилот?"}], [],
            )
            evidence.assert_called_once_with(self.startup, limit=2)
        self.assertIn(self.startup.target_customer, interview)
        self.assertIn(local_mentor.SCENARIO_EXAMPLES["interview"], interview)
        self.assertNotIn(local_mentor.SCENARIO_EXAMPLES["pilot"], interview)
        self.assertNotIn("Был тестовый пилот.", interview)
        self.assertIn("Был тестовый пилот.", pilot)
        self.assertIn(local_mentor.SCENARIO_EXAMPLES["pilot"], pilot)
        self.assertNotIn(local_mentor.SCENARIO_EXAMPLES["interview"], pilot)

    def test_removes_model_self_instruction_from_split_stream(self):
        chunks = ["We should respond: ", "mention both and ask why.", "Раньше было 15, теперь 25."]
        self.assertEqual("".join(local_mentor.final_text(chunks)), "Раньше было 15, теперь 25.")
        chunks = ["We should ask about problem.", "Спросите репетитора о последней записи."]
        self.assertEqual("".join(local_mentor.final_text(chunks)), "Спросите репетитора о последней записи.")

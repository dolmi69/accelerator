import json
import os
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from founder.forms import StartupForm
from founder.models import (
    ChatMessage, ChatSession, MascotState, PitchReport,
    StartupMemory, StartupMetrics, StartupProfile, User,
)
from founder.services.memory import conversation_context, remember_user_message
from founder.services.ai import AIResponseFormatError, AIServiceError, complete_text, stream_reply
from founder.services import gigachat
from founder.services.radar_assessment import RADAR_SCHEMA, _assessment_context, _parse_assessment


@override_settings(AI_PROVIDER="demo")
class MVPFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="founder", email="founder@example.com", password="StrongPass987!"
        )
        self.other = User.objects.create_user(
            username="other", email="other@example.com", password="StrongPass987!"
        )
        self.client.force_login(self.user)
        self.startup = StartupProfile.objects.create(
            owner=self.user,
            name="Orbit",
            one_line_pitch="Платформа для малого бизнеса",
        )

    def test_radar_updates_brunos_state_and_keeps_history(self):
        url = reverse("metrics_create", args=[self.startup.id])
        low = {"product": 20, "market": 30, "finance": 10, "team": 30, "pitch": 20}
        high = {"product": 80, "market": 90, "finance": 75, "team": 85, "pitch": 80}

        self.assertEqual(self.client.post(url, low).status_code, 302)
        self.assertEqual(self.startup.mascot_state.outfit, MascotState.Outfit.PAJAMAS)
        self.assertEqual(self.client.post(url, high).status_code, 302)
        self.startup.mascot_state.refresh_from_db()
        self.assertEqual(self.startup.mascot_state.outfit, MascotState.Outfit.SUIT)
        self.assertEqual(StartupMetrics.objects.filter(startup=self.startup).count(), 2)
        self.assertEqual(self.client.get(reverse("dashboard", args=[self.startup.id])).status_code, 200)

    @override_settings(AI_PROVIDER="gigachat", GIGACHAT_MODEL="GigaChat-3-Pro")
    def test_ai_assessment_uses_founder_context_and_explains_each_score(self):
        self.startup.problem = "Магазины теряют заказы при ручной обработке."
        self.startup.save(update_fields=["problem"])
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        founder_message = ChatMessage.objects.create(
            session=session, role=ChatMessage.Role.USER,
            content="За август получили 20 оплаченных заказов.",
        )
        remember_user_message(founder_message)
        answer = {
            key: {"score": score, "reason": f"Объяснение для {key}"}
            for key, score in {
                "product": 80, "market": 78, "finance": 75, "team": 82, "pitch": 85,
            }.items()
        }
        answer["summary"] = "Есть первые продажи. Следующий шаг — проверить удержание."

        with patch("founder.services.radar_assessment.complete_text", return_value=json.dumps(answer)) as model:
            response = self.client.post(reverse("metrics_assess", args=[self.startup.id]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("20 оплаченных заказов", model.call_args.args[1])
        self.assertIn("Магазины теряют заказы", model.call_args.args[1])
        self.assertEqual(model.call_args.kwargs["json_schema"], RADAR_SCHEMA)
        snapshot = self.startup.metric_snapshots.get()
        self.assertEqual(snapshot.source, StartupMetrics.Source.AI)
        self.assertEqual(snapshot.ai_model, "GigaChat-3-Pro")
        self.assertEqual(snapshot.finance, 75)
        self.assertEqual(snapshot.assessment_details["finance"], "Объяснение для finance")
        self.startup.mascot_state.refresh_from_db()
        self.assertEqual(self.startup.mascot_state.outfit, MascotState.Outfit.SUIT)
        page = self.client.get(reverse("dashboard", args=[self.startup.id]))
        self.assertContains(page, "Почему Бруно поставил эти оценки")
        self.assertContains(page, "Объяснение для finance")
        self.assertContains(page, 'class="radar-value"')

    @override_settings(AI_PROVIDER="gigachat")
    def test_invalid_first_answer_is_retried_and_saves_one_complete_radar(self):
        answer = {key: {"score": 35, "reason": f"Есть сведения о {key}"}
                  for key in ("product", "market", "finance", "team", "pitch")}
        answer["summary"] = "Нужно уточнить расходы."
        with patch("founder.services.radar_assessment.complete_text", side_effect=[
            '{"product": {"score": 35}}', json.dumps(answer),
        ]) as model:
            response = self.client.post(reverse("metrics_assess", args=[self.startup.id]), follow=True)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args_list[0].args[1], model.call_args_list[1].args[1])
        self.assertEqual(self.startup.metric_snapshots.count(), 1)
        self.assertContains(response, 'class="radar-value"')
        self.assertContains(response, "Есть сведения о finance")

    @override_settings(AI_PROVIDER="gigachat")
    def test_network_errors_are_not_retried_as_format_errors(self):
        with patch("founder.services.radar_assessment.complete_text", side_effect=AIServiceError("Нет сети")) as model:
            response = self.client.post(reverse("metrics_assess", args=[self.startup.id]), follow=True)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.startup.metric_snapshots.count(), 0)
        self.assertContains(response, "Нет сети")

    def test_assessment_keeps_question_with_short_answer_and_isolates_startups(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        ChatMessage.objects.create(session=session, role="assistant", content="Оплата за внедрение или подписка?")
        ChatMessage.objects.create(session=session, role="user", content="И так и так")
        ChatMessage.objects.create(session=session, role="assistant", content="Новая неподтверждённая догадка Бруно")
        private_startup = StartupProfile.objects.create(owner=self.other, name="Другой проект")
        private_session = ChatSession.objects.create(startup=private_startup, mode=ChatSession.Mode.COFOUNDER)
        ChatMessage.objects.create(session=private_session, role="user", content="Посторонние закрытые данные")
        context = _assessment_context(self.startup)
        self.assertIn("Оплата за внедрение или подписка?", context)
        self.assertIn("Основатель: И так и так", context)
        self.assertNotIn("Новая неподтверждённая догадка", context)
        self.assertNotIn("Посторонние закрытые данные", context)

    @override_settings(AI_PROVIDER="gigachat")
    def test_invalid_ai_assessment_does_not_replace_radar(self):
        url = reverse("metrics_create", args=[self.startup.id])
        self.client.post(url, {
            "product": 30, "market": 30, "finance": 30, "team": 30, "pitch": 30,
        })
        old_state = self.startup.mascot_state.outfit
        with patch("founder.services.radar_assessment.complete_text", return_value='{"product": {"score": 90}}') as model:
            response = self.client.post(reverse("metrics_assess", args=[self.startup.id]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Бруно вернул неполную оценку")
        self.assertEqual(model.call_count, 2)
        self.assertEqual(self.startup.metric_snapshots.count(), 1)
        self.startup.mascot_state.refresh_from_db()
        self.assertEqual(self.startup.mascot_state.outfit, old_state)

    @override_settings(AI_PROVIDER="gigachat")
    def test_ai_assessment_requires_owner_and_substantive_context(self):
        self.startup.one_line_pitch = ""
        self.startup.save(update_fields=["one_line_pitch"])
        url = reverse("metrics_assess", args=[self.startup.id])
        with patch("founder.services.radar_assessment.complete_text") as model:
            response = self.client.post(url, follow=True)
            self.assertContains(response, "Сначала опишите идею")
            model.assert_not_called()
            self.client.force_login(self.other)
            self.assertEqual(self.client.post(url).status_code, 404)

    def test_chat_stream_persists_messages_and_memory(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        self.assertEqual(
            self.client.get(reverse("chat_detail", args=[self.startup.id, session.id])).status_code,
            200,
        )
        response = self.client.post(
            reverse("chat_send", args=[self.startup.id, session.id]),
            {"content": "У нас 12 платящих клиентов за август."},
        )
        self.assertEqual(response.status_code, 200)
        events = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn('"type": "done"', events)
        self.assertEqual(session.messages.count(), 2)
        self.assertEqual(StartupMemory.objects.filter(startup=self.startup).count(), 1)

    def test_text_upload_is_extracted_and_private(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            upload = SimpleUploadedFile("sales.csv", b"month,revenue\naug,1200\n", content_type="text/csv")
            response = self.client.post(
                reverse("chat_send", args=[self.startup.id, session.id]),
                {"attachment": upload},
            )
            self.assertEqual(response.status_code, 200)
            list(response.streaming_content)
            attachment = ChatMessage.objects.get(session=session, role="user").attachments.get()
            self.assertIn("aug,1200", attachment.extracted_text)
            self.assertIn(str(self.startup.id), attachment.file.name)

    def test_old_claim_is_retrieved_across_sessions(self):
        old_session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        old_message = ChatMessage.objects.create(
            session=old_session, role=ChatMessage.Role.USER,
            content="Выручка за март составила 100000 рублей.",
        )
        remember_user_message(old_message)
        new_session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        new_message = ChatMessage.objects.create(
            session=new_session, role=ChatMessage.Role.USER,
            content="Выручка за март составила 50000 рублей.",
        )
        _, memories = conversation_context(new_session, new_message)
        self.assertEqual([item.source_message_id for item in memories], [old_message.id])

    def test_pitch_report_and_ownership(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.PITCH)
        ChatMessage.objects.create(
            session=session, role=ChatMessage.Role.USER,
            content="Мы помогаем небольшим магазинам. Пока 3 клиента.",
        )
        response = self.client.post(reverse("pitch_finish", args=[self.startup.id, session.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PitchReport.objects.filter(session=session).exists())
        self.assertEqual(
            self.client.get(reverse("chat_detail", args=[self.startup.id, session.id])).status_code,
            200,
        )
        session.refresh_from_db()
        self.assertIsNotNone(session.completed_at)

        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("dashboard", args=[self.startup.id])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("chat_detail", args=[self.startup.id, session.id])).status_code,
            404,
        )

    def test_registration_and_upload_validation(self):
        self.client.logout()
        response = self.client.post(reverse("register"), {
            "username": "newfounder",
            "email": "new@example.com",
            "password1": "SafePass12345!",
            "password2": "SafePass12345!",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="newfounder").exists())

        self.client.force_login(self.user)
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        upload = SimpleUploadedFile("payload.exe", b"hello", content_type="text/plain")
        response = self.client.post(
            reverse("chat_send", args=[self.startup.id, session.id]),
            {"attachment": upload},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(session.messages.count(), 0)

    def test_startup_website_is_optional(self):
        form = StartupForm(data={"name": "Идея без сайта", "stage": "idea", "website": ""})
        self.assertTrue(form.is_valid(), form.errors)

    def test_new_startup_opens_bruno_conversation(self):
        response = self.client.post(reverse("startup_create"), {"name": "Новая идея", "stage": "idea"})
        self.assertEqual(response.status_code, 302)
        startup = StartupProfile.objects.get(name="Новая идея")
        session = startup.chat_sessions.get()
        self.assertEqual(session.mode, ChatSession.Mode.COFOUNDER)
        self.assertEqual(response.url, reverse("chat_detail", args=[startup.id, session.id]))
        page = self.client.get(response.url)
        self.assertContains(page, "Расскажите своими словами")
        self.assertNotContains(page, "Составить таблицу</button>")

    def test_founder_can_edit_all_five_table_explanations(self):
        values = {
            "product": 45, "market": 50, "finance": 20, "team": 30, "pitch": 55,
            "product_reason": "Есть прототип; нужно проверить использование.",
            "market_reason": "Первые беседы с клиентами.",
            "finance_reason": "Выручку ещё не обсуждали.",
            "team_reason": "Два основателя.",
            "pitch_reason": "Идея понятна, отличия уточняем.",
            "assessment_notes": "Сначала проверить спрос.",
        }
        response = self.client.post(reverse("metrics_create", args=[self.startup.id]), values)
        self.assertEqual(response.status_code, 302)
        snapshot = self.startup.metric_snapshots.get()
        self.assertEqual(snapshot.assessment_details["product"], values["product_reason"])
        self.assertEqual(snapshot.assessment_details["pitch"], values["pitch_reason"])
        page = self.client.get(reverse("dashboard", args=[self.startup.id]))
        self.assertContains(page, "Таблица по пяти направлениям")
        self.assertContains(page, values["product_reason"])
        self.assertContains(page, values["pitch_reason"])
        self.assertContains(page, "Сохранить изменения")


class RadarValidationTests(SimpleTestCase):
    def test_all_five_scores_must_be_valid_without_inventing_missing_values(self):
        valid = {key: {"score": 35, "reason": "Есть MVP, результаты пока не измеряли."}
                 for key in ("product", "market", "finance", "team", "pitch")}
        valid["summary"] = "Уточнить результаты."
        for score in (True, "35", 35.5, -1, 101, None):
            with self.subTest(score=score):
                payload = {**valid, "product": {"score": score, "reason": "Описание"}}
                with self.assertRaises(AIResponseFormatError):
                    _parse_assessment(json.dumps(payload))
        for raw in (json.dumps({k: v for k, v in valid.items() if k != "team"}),
                    json.dumps(valid)[:-20], "Бруно думает", None):
            with self.subTest(raw=raw), self.assertRaises(AIResponseFormatError):
                _parse_assessment(raw)
        self.assertEqual(_parse_assessment("```json\n" + json.dumps(valid) + "\n```")[0]["finance"], 35)

    @override_settings(AI_PROVIDER="gigachat")
    def test_gigachat_uses_native_schema_and_rejects_truncated_answers(self):
        replies = [
            {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]},
            {"choices": [{"finish_reason": "length", "message": {"content": '{"ok":true}'}}]},
        ]

        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["response_format"], {
                "type": "json_schema", "schema": RADAR_SCHEMA, "strict": True,
            })
            self.assertEqual(body["max_tokens"], 2400)
            return httpx.Response(200, json=replies.pop(0))

        with patch.object(gigachat, "_headers", return_value={}), patch.object(
            gigachat, "_client", side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        ):
            self.assertEqual(complete_text("Промпт", "Описание", json_schema=RADAR_SCHEMA), '{"ok":true}')
            with self.assertRaises(AIResponseFormatError):
                complete_text("Промпт", "Описание", json_schema=RADAR_SCHEMA)


class GigaChatAdapterTests(TestCase):
    @override_settings(AI_PROVIDER="gigachat", GIGACHAT_MODEL="GigaChat-3-Pro", GIGACHAT_SCOPE="GIGACHAT_API_PERS")
    def test_oauth_stream_and_full_report_use_official_rest_shape(self):
        user = User.objects.create_user(username="giga", email="giga@example.com", password="StrongPass987!")
        startup = StartupProfile.objects.create(owner=user, name="Orbit")
        session = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.COFOUNDER)
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/oauth"):
                self.assertEqual(request.headers["authorization"], "Basic test-credentials")
                self.assertIn(b"scope=GIGACHAT_API_PERS", request.content)
                return httpx.Response(200, json={
                    "access_token": "test-token", "expires_at": time.time() + 1800,
                })
            self.assertEqual(request.headers["authorization"], "Bearer test-token")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "GigaChat-3-Pro")
            self.assertEqual(body["messages"][0]["role"], "system")
            if body["stream"]:
                return httpx.Response(
                    200,
                    text='data: {"choices":[{"delta":{"content":"Привет"}}]}\n\n'
                         'data: {"choices":[{"delta":{"content":"!"}}]}\n\n'
                         'data: [DONE]\n\n',
                    headers={"content-type": "text/event-stream"},
                )
            return httpx.Response(200, json={
                "choices": [{"message": {"content": '{"score":70,"summary":"OK","mistakes":[]}'}}]
            })

        gigachat._token_state.update({"token": "", "expires_at": 0, "fingerprint": ""})
        with patch.dict(os.environ, {"GIGACHAT_CREDENTIALS": "test-credentials"}), patch.object(
            gigachat,
            "_client",
            side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        ):
            text = "".join(stream_reply(
                session, [{"role": "user", "content": "Привет"}], []
            ))
            report = complete_text("Верни JSON", "Текст питча")

        self.assertEqual(text, "Привет!")
        self.assertIn('"score":70', report)
        self.assertEqual(sum(request.url.path.endswith("/oauth") for request in calls), 1)

    @override_settings(AI_PROVIDER="cloudru", CLOUDRU_MODEL="ai-sage/GigaChat3-10B-A1.8B")
    def test_cloudru_stream_and_report(self):
        user = User.objects.create_user(username="cloud", email="cloud@example.com", password="StrongPass987!")
        startup = StartupProfile.objects.create(owner=user, name="Orbit")
        session = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.COFOUNDER)
        chunk = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Ответ"))])
        full = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Отчёт"))])

        with patch.dict(os.environ, {"CLOUDRU_API_KEY": "test-key"}), patch(
            "founder.services.cloudru.OpenAI"
        ) as client_class:
            client_class.return_value.chat.completions.create.side_effect = [iter([chunk]), full]
            answer = "".join(stream_reply(session, [{"role": "user", "content": "Вопрос"}], []))
            report = complete_text("Верни отчёт", "Текст интервью")

        self.assertEqual(answer, "Ответ")
        self.assertEqual(report, "Отчёт")
        self.assertEqual(client_class.call_args.kwargs["base_url"], "https://foundation-models.api.cloud.ru/v1")

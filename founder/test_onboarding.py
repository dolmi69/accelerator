from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatMessage, ChatSession, StartupProfile, User


@override_settings(AI_PROVIDER="cloudru", CHAT_BUFFERED_RESPONSES=True)
class StartupOnboardingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="onboarding-founder", email="onboarding@example.com")
        self.client.force_login(self.user)
        self.profile = {
            "name": "ClinicFlow",
            "one_line_pitch": "Запись пациентов без звонков",
            "problem": "Администраторы пропускают обращения после закрытия клиники.",
            "solution": "Форма записи с выбором времени и подтверждением.",
            "target_customer": "Частные стоматологии с одним филиалом",
            "stage": "validation",
            "website": "https://clinic.example.com/",
        }

    def create_startup(self, **changes):
        response = self.client.post(reverse("startup_create"), self.profile | changes)
        self.assertEqual(response.status_code, 302)
        startup = self.user.startups.get()
        return startup, startup.chat_sessions.get()

    def send(self, startup, session):
        with patch("founder.services.cloudru.stream_chat", return_value=iter(["Продолжим."])) as provider:
            response = self.client.post(
                reverse("chat_send", args=[startup.id, session.id]),
                {"content": "Помоги определить следующий шаг."},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn('"type": "done"', response.content.decode())
        return provider.call_args.args

    def test_questionnaire_is_saved_and_reaches_provider_without_retyping(self):
        startup, session = self.create_startup()
        for field, value in self.profile.items():
            self.assertEqual(getattr(startup, field), value)

        greeting = session.messages.get().content
        for field in ("one_line_pitch", "problem", "solution", "target_customer", "website"):
            self.assertIn(self.profile[field], greeting)
        self.assertNotIn("что за идея?", greeting)
        self.assertNotIn("для кого он", greeting)

        prompt, messages = self.send(startup, session)
        for field in ("name", "one_line_pitch", "problem", "solution", "target_customer", "website"):
            self.assertIn(self.profile[field], prompt)
        self.assertIn("Проверка", prompt)
        self.assertIn("Считай заполненные поля анкеты уже полученными ответами", prompt)
        self.assertEqual(messages[-1], {"role": "user", "content": "Помоги определить следующий шаг."})

    def test_editing_profile_updates_context_in_an_existing_conversation(self):
        startup, session = self.create_startup()
        updated = self.profile | {
            "target_customer": "Сети ветеринарных клиник",
            "solution": "Онлайн-запись к ветеринару",
            "website": "https://vet.example.com/",
            "stage": "traction",
        }
        response = self.client.post(reverse("startup_edit", args=[startup.id]), updated)
        self.assertEqual(response.status_code, 302)

        prompt, _ = self.send(startup, session)
        self.assertIn(updated["target_customer"], prompt)
        self.assertIn(updated["solution"], prompt)
        self.assertIn(updated["website"], prompt)
        self.assertIn("Первые результаты", prompt)
        self.assertNotIn(self.profile["target_customer"], prompt)
        self.assertNotIn(self.profile["solution"], prompt)

    def test_assessment_is_available_before_first_message_if_profile_has_data(self):
        startup, session = self.create_startup()
        page = self.client.get(reverse("chat_detail", args=[startup.id, session.id]))
        self.assertFalse(session.messages.filter(role=ChatMessage.Role.USER).exists())
        self.assertContains(page, 'id="ai-assessment-form"')

    def test_partially_filled_profile_is_acknowledged(self):
        startup, session = self.create_startup(one_line_pitch="", problem="", solution="")
        greeting = session.messages.get().content
        self.assertIn(self.profile["target_customer"], greeting)
        self.assertNotIn("Расскажи своими словами", greeting)
        prompt, _ = self.send(startup, session)
        self.assertIn('"Решение": null', prompt)
        self.assertIn(self.profile["target_customer"], prompt)

    def test_empty_profile_still_gets_an_intro_without_premature_assessment(self):
        startup, session = self.create_startup(
            one_line_pitch="", problem="", solution="", target_customer="", website="",
        )
        page = self.client.get(reverse("chat_detail", args=[startup.id, session.id]))
        self.assertContains(page, "Расскажи своими словами")
        self.assertNotContains(page, 'id="ai-assessment-form"')

    def test_details_beyond_short_greeting_are_sent_to_model(self):
        description = "Описание работы сервиса. " * 100 + "Важная деталь: принимаем заявки ночью."
        startup, session = self.create_startup(solution=description)
        self.assertNotIn("принимаем заявки ночью", session.messages.get().content)
        prompt, _ = self.send(startup, session)
        self.assertIn(description, prompt)

    def test_new_conversation_does_not_restart_onboarding_after_founder_spoke(self):
        startup, session = self.create_startup(
            one_line_pitch="", problem="", solution="", target_customer="", website="",
        )
        ChatMessage.objects.create(session=session, role="user", content="У нас сервис для клиник.")
        response = self.client.post(reverse("chat_create", args=[startup.id]))
        self.assertEqual(response.status_code, 302)
        new_session = startup.chat_sessions.exclude(id=session.id).get()
        self.assertIn("Продолжим работу", new_session.messages.get().content)
        self.assertNotIn("Расскажи своими словами", new_session.messages.get().content)

    def test_context_stays_with_its_project_and_owner(self):
        startup, session = self.create_startup()
        other = User.objects.create_user(username="another-founder", email="another@example.com")
        secret = "Конфиденциальная идея другого основателя"
        private = StartupProfile.objects.create(owner=other, name="Private", target_customer=secret)
        private_session = ChatSession.objects.create(startup=private)
        prompt, _ = self.send(startup, session)
        self.assertNotIn(secret, prompt)
        with patch("founder.services.cloudru.stream_chat") as provider:
            response = self.client.post(
                reverse("chat_send", args=[private.id, private_session.id]), {"content": "Привет"},
            )
            self.assertEqual(response.status_code, 404)
            provider.assert_not_called()

    def test_profile_text_is_escaped_in_greeting(self):
        startup, session = self.create_startup(target_customer='<script>alert("test")</script>')
        page = self.client.get(reverse("chat_detail", args=[startup.id, session.id]))
        self.assertNotContains(page, '<script>alert("test")</script>')
        self.assertContains(page, "&lt;script&gt;")

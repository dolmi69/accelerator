import json
from unittest.mock import Mock, patch

import httpx
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from founder.models import User, StartupProfile, ChatSession
from founder.services.telegram import TelegramClient, TelegramError, configure_bot, handle_update, validate_app_url


class TelegramLauncherTests(SimpleTestCase):
    def test_only_private_commands_receive_webapp_buttons(self):
        client = Mock()
        update = {"message": {"chat": {"id": 123, "type": "private"}, "text": "/start example"}}
        handle_update(client, update, "https://example.com")
        payload = client.call.call_args.kwargs
        self.assertEqual(payload["chat_id"], 123)
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"], "https://example.com")
        client.reset_mock()
        update["message"]["chat"]["type"] = "group"
        handle_update(client, update, "https://example.com")
        handle_update(client, {"message": {"chat": {"type": "private"}, "text": "personal data"}}, "https://example.com")
        client.call.assert_not_called()

    def test_local_and_unsafe_addresses_rejected(self):
        for url in ("http://example.com", "https://localhost", "https://127.0.0.1", "https://example.com?token=x"):
            with self.subTest(url=url), self.assertRaises(TelegramError):
                validate_app_url(url)
        self.assertEqual(validate_app_url("https://app.example.com/"), "https://app.example.com/")

    def test_api_token_not_exposed_in_errors(self):
        token = "123456:" + "x" * 30
        def fail(request):
            raise httpx.ConnectError(str(request.url), request=request)
        with TelegramClient(token, transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(TelegramError) as raised:
                client.call("getMe")
        self.assertNotIn(token, str(raised.exception))

    def test_setup_sets_webapp_menu(self):
        client = Mock()
        configure_bot(client, "https://app.example.com/")
        self.assertEqual(client.call.call_args.args[0], "setChatMenuButton")
        self.assertEqual(client.call.call_args.kwargs["menu_button"]["type"], "web_app")


class TelegramWebsiteTests(TestCase):
    @override_settings(AI_PROVIDER="demo", CHAT_BUFFERED_RESPONSES=True)
    def test_buffered_chat_saves_answer_and_preserves_frontend_events(self):
        user = User.objects.create_user(username="tg_test")
        startup = StartupProfile.objects.create(owner=user, name="Test")
        session = ChatSession.objects.create(startup=startup, mode="cofounder")
        self.client.force_login(user)
        with patch("founder.views.stream_reply", return_value=iter(["Привет", "! "])):
            response = self.client.post(reverse("chat_send", args=[startup.id, session.id]), {"content": "Идея"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.streaming)
        events = [json.loads(line[6:]) for line in response.content.decode().splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(session.messages.get(role="assistant").content, "Привет!")

    @override_settings(MIDDLEWARE=["founder.telegram_middleware.TelegramFrameMiddleware",
                                  "django.middleware.clickjacking.XFrameOptionsMiddleware"])
    def test_framing_allowed_only_for_telegram_and_admin_stays_protected(self):
        from django.http import HttpResponse
        from django.test import RequestFactory
        from founder.telegram_middleware import TelegramFrameMiddleware
        handler = TelegramFrameMiddleware(lambda request: HttpResponse(headers={"X-Frame-Options": "DENY"}))
        response = handler(RequestFactory().get("/login/"))
        self.assertNotIn("X-Frame-Options", response)
        self.assertEqual(response["Content-Security-Policy"], "frame-ancestors 'self' https://web.telegram.org;")
        response = handler(RequestFactory().get("/admin/"))
        self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_password_change_requires_login_and_keeps_current_session(self):
        url = reverse("password_change")
        self.assertEqual(self.client.get(url).status_code, 302)
        user = User.objects.create_user(username="owner", email="owner@example.invalid", password="old-demo-only-456")
        StartupProfile.objects.create(owner=user, name="Тестовый проект")
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            "old_password": "old-demo-only-456",
            "new_password1": "new-demo-only-789",
            "new_password2": "new-demo-only-789",
        })
        self.assertRedirects(response, reverse("home"))
        user.refresh_from_db()
        self.assertFalse(user.check_password("old-demo-only-456"))
        self.assertTrue(user.check_password("new-demo-only-789"))
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

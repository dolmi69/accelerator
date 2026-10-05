"""Controlled abuse/regression tests: synthetic users, no external AI calls."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from unittest.mock import Mock, patch
from uuid import uuid4

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import StopUpload
from django.db import connection, connections
from django.http import StreamingHttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from founder.forms import ChatSendForm, StartupForm
from founder.models import AIRequestLease, ChatMessage, ChatSession, DirectConversation, DirectMessage, StartupProfile, User
from founder.security_middleware import RequestProtectionMiddleware
from founder.services.gigachat import GigaChatError, stream_chat
from founder.services.json_utils import bounded_json_loads
from founder.services.request_limits import RequestLimitExceeded, acquire_ai_lease, consume_limit, release_ai_lease
from founder.upload_handlers import BoundedUploadHandler


@override_settings(AI_PROVIDER="demo", CHAT_BUFFERED_RESPONSES=True)
class RequestProtectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("security", email="security@example.test")
        self.startup = StartupProfile.objects.create(owner=self.user, name="Security test")
        self.session = ChatSession.objects.create(startup=self.startup)
        self.url = reverse("chat_send", args=[self.startup.pk, self.session.pk])
        self.client.force_login(self.user)

    def test_login_limit_cannot_be_bypassed_with_forwarded_header(self):
        client = Client()
        for _ in range(30):
            response = client.post(reverse("login"), {})
            self.assertEqual(response.status_code, 200)
        response = client.post(reverse("login"), {}, HTTP_X_FORWARDED_FOR="203.0.113.7")
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertEqual(client.post(reverse("login"), {}, REMOTE_ADDR="203.0.113.8").status_code, 200)

    @override_settings(AI_REQUESTS_PER_MINUTE=2)
    def test_ai_quota_shared_across_sessions_and_secret_keys(self):
        with patch("founder.views.stream_reply", side_effect=lambda *a: iter(["Ответ"])) as provider:
            for _ in range(2):
                self.assertEqual(self.client.post(self.url, {"content": "Вопрос"}).status_code, 200)
            with override_settings(SECRET_KEY="a-second-origin-secret"):
                another_client = Client()
                another_client.force_login(self.user)
                self.assertEqual(another_client.post(self.url, {"content": "Лишний запрос"}).status_code, 429)
            self.assertEqual(provider.call_count, 2)
        self.assertEqual(self.session.messages.filter(role="user").count(), 2)

    def test_parallel_ai_request_is_denied_without_saving_message(self):
        token = acquire_ai_lease(self.user.pk)
        with patch("founder.views.stream_reply") as provider:
            self.assertEqual(self.client.post(self.url, {"content": "Дубль"}).status_code, 429)
            provider.assert_not_called()
        self.assertEqual(self.session.messages.count(), 0)
        release_ai_lease(self.user.pk, token)
        with patch("founder.views.stream_reply", return_value=iter(["Ответ"])):
            self.assertEqual(self.client.post(self.url, {"content": "Вопрос"}).status_code, 200)

    def test_stale_lease_release_cannot_unlock_new_request(self):
        old = acquire_ai_lease(self.user.pk)
        AIRequestLease.objects.filter(user=self.user).update(expires_at=timezone.now()-timedelta(seconds=1))
        new = acquire_ai_lease(self.user.pk)
        release_ai_lease(self.user.pk, old)
        with self.assertRaises(RequestLimitExceeded):
            acquire_ai_lease(self.user.pk)
        release_ai_lease(self.user.pk, new)
        acquire_ai_lease(self.user.pk)

    @override_settings(CHAT_BUFFERED_RESPONSES=False)
    def test_stream_lease_lasts_until_done_and_releases_on_validation_error(self):
        with patch("founder.views.stream_reply", return_value=iter(["Ответ"])):
            response = self.client.post(self.url, {"content": "Вопрос"})
            with self.assertRaises(RequestLimitExceeded):
                acquire_ai_lease(self.user.pk)
            self.assertIn(b'"type": "done"', b"".join(response.streaming_content))
        token = acquire_ai_lease(self.user.pk)
        release_ai_lease(self.user.pk, token)
        self.assertEqual(self.client.post(self.url, {}).status_code, 400)
        acquire_ai_lease(self.user.pk)

    def test_closing_unstarted_response_releases_lease(self):
        request = RequestFactory().post(self.url)
        request.user = self.user
        request.ai_lease = acquire_ai_lease(self.user.pk)
        response = RequestProtectionMiddleware(lambda r: None).process_response(request, StreamingHttpResponse(iter([b"x"])))
        response.close()
        acquire_ai_lease(self.user.pk)

    def test_malformed_ai_output_is_reported_and_user_message_survives(self):
        with patch("founder.views.stream_reply", return_value=iter([42])):
            response = self.client.post(self.url, {"content": "Не потерять"})
        self.assertContains(response, '"type": "error"')
        self.assertEqual(self.session.messages.get().content, "Не потерять")
        acquire_ai_lease(self.user.pk)

    def test_csrf_required_and_outsider_cannot_send_or_spend_ai(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, {"content": "forged"}).status_code, 403)
        other = User.objects.create_user("outsider", email="outsider@example.test")
        self.client.force_login(other)
        with patch("founder.views.stream_reply") as provider:
            self.assertEqual(self.client.post(self.url, {"content": "forged"}).status_code, 404)
            provider.assert_not_called()
        self.assertFalse(AIRequestLease.objects.exists())
        self.assertFalse(self.session.messages.exists())

    def test_large_upload_stopped_before_view(self):
        upload = SimpleUploadedFile("large.txt", b"x" * (settings.MAX_UPLOAD_BYTES + 300000))
        with patch("founder.views.stream_reply") as provider:
            self.assertEqual(self.client.post(self.url, {"attachment": upload}).status_code, 413)
            provider.assert_not_called()
        self.assertFalse(self.session.messages.exists())

    def test_expired_window_resets(self):
        from founder.models import RequestLimit
        consume_limit("test-window", 1, 60)
        with self.assertRaises(RequestLimitExceeded):
            consume_limit("test-window", 1, 60)
        RequestLimit.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        consume_limit("test-window", 1, 60)

    def test_browser_error_page_does_not_follow_external_referer_or_query_db(self):
        request = RequestFactory().post("/login/", HTTP_ACCEPT="text/html", HTTP_REFERER="https://attacker.example/")
        with self.assertNumQueries(0):
            response = RequestProtectionMiddleware.error_response(request, "Подождите", 429, retry_after=30)
        self.assertContains(response, 'href="/"', status_code=429)
        self.assertNotContains(response, "attacker.example", status_code=429)
        self.assertEqual(response["Retry-After"], "30")
        self.assertEqual(response["Cache-Control"], "no-store")


@override_settings(AI_PROVIDER="demo", CHAT_BUFFERED_RESPONSES=True)
class ConcurrentRequestTests(TransactionTestCase):
    def test_two_tabs_cannot_start_two_ai_operations_at_once(self):
        user = User.objects.create_user("concurrent", email="concurrent@example.test")
        startup = StartupProfile.objects.create(owner=user, name="Parallel test")
        session = ChatSession.objects.create(startup=startup)
        url = reverse("chat_send", args=[startup.pk, session.pk])
        first, second = Client(), Client()
        first.force_login(user)
        second.force_login(user)
        entered, resume = Event(), Event()

        def provider(*args):
            entered.set()
            if not resume.wait(5):
                raise AssertionError("Concurrent request did not complete")
            yield "Готово"

        def first_request():
            try:
                return first.post(url, {"content": "Первый запрос"})
            finally:
                connections.close_all()

        with patch("founder.views.stream_reply", side_effect=provider) as ai:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(first_request)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(second.post(url, {"content": "Второй запрос"}).status_code, 429)
                finally:
                    resume.set()
                self.assertEqual(pending.result(timeout=5).status_code, 200)
            self.assertEqual(ai.call_count, 1)
        self.assertEqual(session.messages.filter(role="user").count(), 1)
        self.assertEqual(session.messages.filter(role="assistant").count(), 1)
        acquire_ai_lease(user.pk)


class UntrustedInputTests(SimpleTestCase):
    def test_invalid_evidence_status_cannot_crash_radar_saving(self):
        from founder.services.radar_assessment import _verified_evidence
        axes = ('product', 'market', 'finance', 'team', 'pitch')
        raw = json.dumps({axis: {'evidence': {'status': []}} for axis in axes})
        self.assertEqual(_verified_evidence(raw, {}), {axis: {'status': 'unlinked'} for axis in axes})

    def test_empty_binary_and_deep_json_uploads_rejected(self):
        for name, raw in [("empty.txt", b" \n"), ("binary.txt", b"hello\x00world"), ("deep.json", b"["*65+b"0"+b"]"*65)]:
            with self.subTest(name=name):
                form = ChatSendForm({}, {"attachment": SimpleUploadedFile(name, raw)})
                self.assertTrue(form.is_valid())
                with self.assertRaises(forms.ValidationError):
                    form.extracted_text()

    def test_valid_text_and_quoted_json_brackets_accepted(self):
        self.assertEqual(bounded_json_loads('{"text":"' + '['*200 + '"}')["text"], '['*200)
        form = ChatSendForm({}, {"attachment": SimpleUploadedFile("ok.json", '{"клиенты": 3}'.encode())})
        self.assertTrue(form.is_valid())
        self.assertIn("клиенты", form.extracted_text())

    @override_settings(MAX_UPLOAD_BYTES=10)
    def test_upload_handler_enforces_actual_bytes_without_content_length(self):
        request = RequestFactory().post("/")
        handler = BoundedUploadHandler(request)
        self.assertEqual(handler.receive_data_chunk(b"123456", 0), b"123456")
        with self.assertRaises(StopUpload):
            handler.receive_data_chunk(b"789012", 6)
        self.assertTrue(request.upload_limit_exceeded)

    def test_profile_description_has_server_side_length_limit(self):
        form = StartupForm({"name": "Test", "stage": "idea", "problem": "x"*6001})
        self.assertFalse(form.is_valid())
        self.assertIn("problem", form.errors)

    def test_bad_stream_events_raise_provider_error(self):
        for event in ['{"choices":[{"delta":[]}]}', '{"choices":[{"delta":{"content":42}}]}', '['*65+'0'+']'*65]:
            with self.subTest(event=event):
                response = Mock()
                response.iter_lines.return_value = ["data: " + event, "", "data: [DONE]", ""]
                client = Mock()
                client.stream.return_value.__enter__ = Mock(return_value=response)
                client.stream.return_value.__exit__ = Mock(return_value=False)
                with patch("founder.services.gigachat._client") as factory, patch("founder.services.gigachat._headers", return_value={}), patch("founder.services.gigachat._raise_for_status"):
                    factory.return_value.__enter__.return_value = client
                    with self.assertRaises(GigaChatError):
                        list(stream_chat("test", []))


class ConversationLoadingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("history", email="history@example.test")
        self.startup = StartupProfile.objects.create(owner=self.user, name="History")
        self.session = ChatSession.objects.create(startup=self.startup)
        self.url = reverse("chat_detail", args=[self.startup.pk, self.session.pk])
        self.client.force_login(self.user)

    def test_recent_history_pagination_and_citation_access(self):
        messages = ChatMessage.objects.bulk_create([ChatMessage(session=self.session, role="user", content=f"Message {i}") for i in range(120)])
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["chat_messages"]), 50)
        self.assertEqual(response.context["chat_messages"][-1].pk, messages[-1].pk)
        older = self.client.get(self.url, {"page": 3})
        self.assertEqual(len(older.context["chat_messages"]), 20)
        citation = self.client.get(self.url, {"message": messages[0].pk})
        self.assertContains(citation, f'id="message-{messages[0].pk}"')
        alien_session = ChatSession.objects.create(startup=self.startup)
        alien = ChatMessage.objects.create(session=alien_session, role="user", content="Other conversation")
        for value in ("invalid", str(alien.pk)):
            self.assertEqual(self.client.get(self.url, {"message": value}).status_code, 404)

    def test_inbox_query_count_does_not_grow_per_conversation(self):
        def add_thread(index):
            peer = User.objects.create_user(f"peer{index}", email=f"peer{index}@example.test")
            thread = DirectConversation.objects.create(user_low=self.user, user_high=peer)
            incoming = DirectMessage.objects.create(conversation=thread, sender=peer, client_id=uuid4(), content="incoming")
            DirectMessage.objects.create(conversation=thread, sender=self.user, client_id=uuid4(), content="latest reply")
            return thread, incoming
        first, incoming = add_thread(0)
        with CaptureQueriesContext(connection) as one:
            response = self.client.get(reverse("inbox"))
            self.assertEqual(response.context["threads"][0]["unread"], 1)
            self.assertEqual(response.context["threads"][0]["last"]["content"], "latest reply")
        for index in range(1, 12):
            add_thread(index)
        with CaptureQueriesContext(connection) as many:
            response = self.client.get(reverse("inbox"))
            self.assertEqual(len(response.context["threads"]), 12)
        self.assertLessEqual(len(many), len(one)+1)
        first.low_read_id = incoming.id
        first.save()
        response = self.client.get(reverse("inbox"))
        row = next(row for row in response.context["threads"] if row["thread"].pk == first.pk)
        self.assertEqual(row["unread"], 0)

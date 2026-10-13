import json
import uuid
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from .models import Conversation, Message, SavedResult


MODULES = {'modules': ['registration', 'chat', 'notifications']}


@override_settings(SITE={**settings.SITE, **MODULES})
class AppActionTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c = [get_user_model().objects.create_user(username=name)
                                  for name in ['actions_alice', 'actions_bob', 'actions_eve']]
        self.client.force_login(self.a)
        self.payload = {'title': 'Расчёт калорий', 'content': 'Норма: 2 100 ккал\nBMR: 1 750 ккал', 'nonce': str(uuid.uuid4())}

    def post(self, path, data):
        return self.client.post('/app-api/results/' + path + '/', json.dumps(data), content_type='application/json')

    def test_session_uses_actual_account_and_requires_login_to_write(self):
        self.assertEqual(self.client.get('/app-api/session/').json()['user']['id'], self.a.pk)
        self.client.logout()
        self.assertFalse(self.client.get('/app-api/session/').json()['authenticated'])
        self.assertEqual(self.post('save', self.payload).status_code, 401)
        self.assertEqual(self.client.get('/app-api/results/').status_code, 401)
        self.assertFalse(SavedResult.objects.exists())

    def test_save_is_private_persistent_escaped_and_idempotent(self):
        self.payload['content'] += '\n<script>alert(1)</script>'
        saved = self.post('save', self.payload).json()
        self.assertTrue(saved['persisted'])
        self.assertEqual(self.post('save', self.payload).json()['result']['id'], saved['result']['id'])
        self.assertEqual(SavedResult.objects.count(), 1)
        self.assertContains(self.client.get('/results/'), '&lt;script&gt;alert(1)&lt;/script&gt;')
        changed = {**self.payload, 'content': 'Другой результат'}
        self.assertEqual(self.post('save', changed).status_code, 409)
        self.client.force_login(self.b)
        self.assertEqual(self.client.get('/app-api/results/').json()['results'], [])
        self.assertEqual(self.client.get('/app-api/results/?id=' + saved['result']['id']).json()['results'], [])
        self.assertNotContains(self.client.get('/results/'), '2 100 ккал')
        self.assertEqual(self.post('share', {'result_id': saved['result']['id'], 'recipient_id': self.a.pk,
            'nonce': str(uuid.uuid4())}).status_code, 404)

    def test_share_records_the_right_sender_recipient_and_survives_reload(self):
        data = {**self.payload, 'recipient_id': self.b.pk}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post('share', data)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result['persisted'])
        row = Message.objects.get(pk=result['message_id'])
        self.assertEqual(row.sender_id, self.a.pk)
        self.assertEqual(row.content, self.payload['title'] + '\n' + self.payload['content'])
        self.assertEqual(self.post('share', data).json()['message_id'], row.pk)
        self.assertEqual(Message.objects.count(), 1)
        self.client.force_login(self.b)
        history = self.client.get(result['chat_url'] + 'history/').json()['messages']
        self.assertEqual(history[0]['id'], row.pk)
        self.assertEqual(history[0]['content'], row.content)
        self.assertEqual(SavedResult.objects.filter(owner=self.a).count(), 1)
        self.assertEqual(SavedResult.objects.filter(owner=self.b).count(), 0)
        self.client.force_login(self.c)
        self.assertEqual(self.client.get(result['chat_url'] + 'history/').status_code, 404)

    def test_same_nonce_cannot_send_to_another_recipient(self):
        self.post('share', {**self.payload, 'recipient_id': self.b.pk})
        self.assertEqual(self.post('share', {**self.payload, 'recipient_id': self.c.pk}).status_code, 409)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Conversation.objects.count(), 1)

    def test_saved_result_can_be_shared_without_creating_another_copy(self):
        identifier = self.post('save', self.payload).json()['result']['id']
        response = self.post('share', {'result_id': identifier, 'recipient_id': self.b.pk, 'nonce': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SavedResult.objects.count(), 1)

    def test_csrf_disabled_module_and_inactive_recipient(self):
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.a)
        self.assertEqual(csrf.post('/app-api/results/save/', json.dumps(self.payload), content_type='application/json').status_code, 403)
        page = csrf.get('/')
        import re
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode())[1]
        self.assertEqual(csrf.post('/app-api/results/save/', json.dumps(self.payload), content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code, 200)
        with override_settings(SITE={**settings.SITE, 'modules': ['registration']}):
            self.assertEqual(self.post('share', {**self.payload, 'recipient_id': self.b.pk}).status_code, 404)
        self.b.is_active = False; self.b.save()
        self.assertEqual(self.post('share', {**self.payload, 'recipient_id': self.b.pk}).status_code, 404)
        self.assertFalse(Message.objects.exists())

    def test_bad_payloads_and_sender_forgery_never_write(self):
        bad = [{**self.payload, 'sender_id': self.b.pk}, {**self.payload, 'content': 'x'*1801},
               {**self.payload, 'title': 'x'*121}, {**self.payload, 'nonce': 'bad'}, []]
        for data in bad:
            with self.subTest(data=str(data)[:80]): self.assertEqual(self.post('save', data).status_code, 400)
        self.assertEqual(self.post('share', {**self.payload, 'recipient_id': self.a.pk}).status_code, 400)
        self.assertEqual(self.post('share', {**self.payload, 'recipient_id': True}).status_code, 400)
        self.assertFalse(SavedResult.objects.exists())
        self.assertFalse(Message.objects.exists())


@override_settings(SITE={**settings.SITE, **MODULES})
class AppLiveShareTests(TransactionTestCase):
    def test_http_share_reaches_recipient_websocket_and_retry_does_not_duplicate(self):
        a, b = [get_user_model().objects.create_user(username=name) for name in ['live_alice', 'live_bob']]
        conversation = Conversation.objects.create(first=a, second=b)
        alice, bob = Client(), Client(); alice.force_login(a); bob.force_login(b)
        cookie = (settings.SESSION_COOKIE_NAME + '=' + bob.cookies[settings.SESSION_COOKIE_NAME].value).encode()
        payload = {'title': 'Расчёт калорий', 'content': '2 100 ккал', 'recipient_id': b.pk, 'nonce': str(uuid.uuid4())}
        async def scenario():
            from config.asgi import application
            socket = WebsocketCommunicator(application, '/ws/messages/' + str(conversation.pk) + '/', headers=[
                (b'host', b'localhost:9000'), (b'origin', b'http://localhost:9000'), (b'cookie', cookie)])
            self.assertTrue((await socket.connect())[0])
            response = await sync_to_async(alice.post)('/app-api/results/share/', json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 200)
            received = await socket.receive_json_from()
            self.assertEqual(received['sender_id'], a.pk)
            self.assertEqual(received['id'], response.json()['message_id'])
            self.assertEqual(received['content'], 'Расчёт калорий\n2 100 ккал')
            await sync_to_async(alice.post)('/app-api/results/share/', json.dumps(payload), content_type='application/json')
            self.assertTrue(await socket.receive_nothing(timeout=0.05))
            await socket.disconnect()
        async_to_sync(scenario)()
        self.assertEqual(Message.objects.count(), 1)

"""Run in the exported Django project: python manage.py test core."""
import uuid
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from .models import Conversation, Message


class AccountTests(TestCase):
    def test_login_keeps_project_name(self):
        self.assertContains(self.client.get('/login/'), settings.SITE['name'])

    def test_registration_hashes_password_and_login_logout(self):
        response = self.client.post('/register/', {'username':'alice', 'password1':'River-Forest-73!', 'password2':'River-Forest-73!'})
        self.assertRedirects(response, '/people/')
        user = get_user_model().objects.get(username='alice')
        self.assertTrue(user.check_password('River-Forest-73!'))
        self.assertNotEqual(user.password, 'River-Forest-73!')
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertRedirects(self.client.post('/logout/'), '/')
        self.assertRedirects(self.client.post('/login/', {'username':'alice', 'password':'River-Forest-73!'}), '/people/')

    def test_csrf_and_weak_password(self):
        csrf = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post('/register/', {}).status_code, 403)
        self.client.post('/register/', {'username':'bad', 'password1':'123', 'password2':'123'})
        self.assertFalse(get_user_model().objects.filter(username='bad').exists())

    def test_idor_and_single_conversation_pair(self):
        users = [get_user_model().objects.create_user(username=name) for name in ['a', 'b', 'c']]
        self.client.force_login(users[0])
        first = self.client.post(reverse('start_chat', args=[users[1].pk]))
        second = self.client.post(reverse('start_chat', args=[users[1].pk]))
        self.assertEqual(first.url, second.url)
        self.assertEqual(Conversation.objects.count(), 1)
        conversation = Conversation.objects.get()
        Message.objects.create(conversation=conversation, sender=users[0], content='<script>alert(1)</script>')
        page = self.client.get(first.url)
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.client.force_login(users[2])
        self.assertEqual(self.client.get(first.url).status_code, 404)
        self.assertEqual(self.client.get(first.url + 'history/').status_code, 404)

    def test_prototype_is_sandboxed_and_not_templated(self):
        response = self.client.get('/prototype/')
        self.assertIn('sandbox allow-scripts', response['Content-Security-Policy'])
        self.assertIn("connect-src 'none'", response['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin', response['Content-Security-Policy'])
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')


class SocketTests(TransactionTestCase):
    def setUp(self):
        users = [get_user_model().objects.create_user(username=name, password='River-Forest-73!') for name in ['alice', 'bob', 'eve']]
        self.alice, self.bob, self.eve = users
        self.conversation = Conversation.objects.create(first=self.alice, second=self.bob)
        self.clients = []
        self.cookies = []
        for user in users:
            client = Client()
            client.force_login(user)
            self.clients.append(client)
            self.cookies.append((settings.SESSION_COOKIE_NAME + '=' + client.cookies[settings.SESSION_COOKIE_NAME].value).encode())

    def socket(self, index=0, origin='http://localhost:9000', cookie=True):
        from config.asgi import application
        headers = [(b'host', b'localhost:9000'), (b'origin', origin.encode())]
        if cookie:
            headers.append((b'cookie', self.cookies[index]))
        return WebsocketCommunicator(application, '/ws/messages/' + str(self.conversation.pk) + '/', headers=headers)

    def test_two_accounts_exchange_and_retry_without_duplicate(self):
        async def scenario():
            a, b = self.socket(0), self.socket(1)
            self.assertTrue((await a.connect())[0]); self.assertTrue((await b.connect())[0])
            payload = {'content':'Привет, это настоящий WebSocket!', 'nonce':str(uuid.uuid4()), 'sender_id':self.eve.pk}
            await a.send_json_to(payload)
            sent, received = await a.receive_json_from(), await b.receive_json_from()
            self.assertEqual(sent['id'], received['id'])
            self.assertEqual(received['sender_id'], self.alice.pk)
            await a.send_json_to(payload)
            retry = await a.receive_json_from()
            self.assertEqual(retry['id'], sent['id'])
            self.assertTrue(await b.receive_nothing(timeout=0.05))
            await a.disconnect(); await b.disconnect()
        async_to_sync(scenario)()
        self.assertEqual(Message.objects.count(), 1)
        client = self.clients[1]
        history = client.get(reverse('history', args=[self.conversation.pk])).json()
        self.assertEqual(history['messages'][0]['content'], 'Привет, это настоящий WebSocket!')

    def test_disabled_chat_rejects_authenticated_socket(self):
        async def scenario():
            socket = self.socket()
            self.assertFalse((await socket.connect())[0])
            await socket.disconnect()
        with override_settings(SITE={**settings.SITE, 'modules': []}):
            async_to_sync(scenario)()

    def test_anonymous_foreign_user_and_origin_cannot_connect(self):
        async def scenario():
            for socket in [self.socket(cookie=False), self.socket(2), self.socket(origin='http://localhost:8000')]:
                self.assertFalse((await socket.connect())[0])
                await socket.disconnect()
        async_to_sync(scenario)()

    def test_bad_payload_is_rejected_and_logout_revokes_open_socket(self):
        async def scenario():
            a = self.socket()
            self.assertTrue((await a.connect())[0])
            await a.send_json_to({'content':'x'*2001, 'nonce':str(uuid.uuid4())})
            self.assertEqual((await a.receive_json_from())['type'], 'error')
            await sync_to_async(self.clients[0].post)('/logout/')
            await a.send_json_to({'content':'После выхода', 'nonce':str(uuid.uuid4())})
            closed = await a.receive_output()
            self.assertEqual(closed['code'], 4403)
            await a.disconnect()
        async_to_sync(scenario)()
        self.assertFalse(Message.objects.exists())

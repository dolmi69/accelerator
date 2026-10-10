from datetime import timedelta
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import ChatMessage, ChatSession, DirectConversation, DirectMessage, StartupProfile, User
from founder.services.chat_search import MAX_RESULTS, SEARCHES_PER_MINUTE, fold, snippet
from founder.services.messaging import JUMP_PAGE_SIZE, history


class FoldTests(TestCase):
    def test_fold_ignores_case_and_yo_without_moving_characters(self):
        self.assertEqual(fold('Ёжик ПРИВЕТ Straße'), 'ежик привет straße')
        for text in ('İstanbul', 'ǅ', 'Ёлка 🎄 Ok'):
            self.assertEqual(len(fold(text)), len(text))

    def test_snippet_is_one_line_with_ellipses(self):
        text = 'начало ' * 20 + 'Нужная\nстрока' + ' хвост' * 30
        excerpt = snippet(text, text.index('Нужная'), len('нужная'))
        self.assertTrue(excerpt.startswith('…') and excerpt.endswith('…'))
        self.assertIn('Нужная строка', excerpt)
        self.assertNotIn('\n', excerpt)
        self.assertEqual(snippet('коротко', 0, 3), 'коротко')


@override_settings(AI_PROVIDER='demo')
class BrunoChatSearchTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('search_owner', email='search_owner@example.test')
        self.stranger = User.objects.create_user('search_stranger', email='search_stranger@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Кофейня')
        self.session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        start = timezone.now() - timedelta(hours=1)
        self.rows = []
        for index, (role, content) in enumerate([
            ('user', 'Наши клиенты — офисные сотрудники'),
            ('assistant', 'Сколько КЛИЕНТОВ уже заплатило?'),
            ('user', 'Пока ни одного, но есть ёмкий рынок'),
            ('assistant', 'Хорошо, давайте посчитаем экономику'),
        ]):
            self.rows.append(ChatMessage.objects.create(session=self.session, role=role, content=content,
                                                        created_at=start + timedelta(minutes=index)))
        self.url = reverse('chat_search', args=[self.startup.id, self.session.id])
        self.client.force_login(self.owner)

    def test_finds_cyrillic_case_insensitively_newest_first(self):
        data = self.client.get(self.url, {'q': '  клиент '}).json()
        self.assertEqual(data['query'], 'клиент')
        self.assertEqual([item['id'] for item in data['results']], [str(self.rows[1].id), str(self.rows[0].id)])
        self.assertEqual([item['author'] for item in data['results']], ['Бруно', 'Вы'])
        self.assertEqual([item['own'] for item in data['results']], [False, True])
        self.assertIn('КЛИЕНТОВ', data['results'][0]['snippet'])
        self.assertFalse(data['truncated'])

    def test_yo_and_empty_query(self):
        self.assertEqual(len(self.client.get(self.url, {'q': 'емкий'}).json()['results']), 1)
        self.assertEqual(self.client.get(self.url, {'q': '   '}).json()['results'], [])
        self.assertEqual(self.client.get(self.url, {'q': 'нет такого'}).json()['results'], [])

    def test_only_owner_can_search_and_only_with_get(self):
        self.assertEqual(self.client.post(self.url, {'q': 'клиент'}).status_code, 405)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url, {'q': 'клиент'}).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url, {'q': 'клиент'}).status_code, 302)

    def test_result_limit_and_rate_limit(self):
        ChatMessage.objects.bulk_create([ChatMessage(session=self.session, role='user', content=f'повтор {i}')
                                         for i in range(MAX_RESULTS + 5)])
        data = self.client.get(self.url, {'q': 'ПОВТОР'}).json()
        self.assertEqual(len(data['results']), MAX_RESULTS)
        self.assertTrue(data['truncated'])
        for _ in range(SEARCHES_PER_MINUTE - 1):
            self.client.get(self.url, {'q': 'клиент'})
        response = self.client.get(self.url, {'q': 'клиент'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('error', response.json())

    def test_chat_page_has_search_button_and_opens_found_message_page(self):
        older = ChatMessage.objects.create(session=self.session, role='user', content='Самое первое сообщение',
                                           created_at=timezone.now() - timedelta(days=2))
        ChatMessage.objects.bulk_create([ChatMessage(session=self.session, role='user', content=f'Текст {i}')
                                         for i in range(60)])
        detail = reverse('chat_detail', args=[self.startup.id, self.session.id])
        page = self.client.get(detail)
        self.assertContains(page, 'data-chat-search="bruno"')
        self.assertContains(page, f'data-search-url="{self.url}"')
        self.assertNotContains(page, f'id="message-{older.id}"')
        found = self.client.get(detail, {'message': older.id, 'search': 'первое'})
        self.assertContains(found, f'id="message-{older.id}"')


class DirectSearchTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user('dm_alice', email='dm_alice@example.test', display_name='Алиса')
        self.bob = User.objects.create_user('dm_bob', email='dm_bob@example.test', display_name='Боб')
        self.eve = User.objects.create_user('dm_eve', email='dm_eve@example.test')
        self.thread = DirectConversation.objects.create(user_low=self.alice, user_high=self.bob)
        self.first = DirectMessage.objects.create(conversation=self.thread, sender=self.alice, client_id=uuid4(),
                                                  content='Привет! Посмотри презентацию')
        self.second = DirectMessage.objects.create(conversation=self.thread, sender=self.bob, client_id=uuid4(),
                                                   content='Презентация отличная')
        self.url = reverse('conversation_search', args=[self.thread.pk])

    def test_participants_search_with_names(self):
        self.client.force_login(self.alice)
        data = self.client.get(self.url, {'q': 'ПРЕЗЕНТАЦ'}).json()
        self.assertEqual([item['id'] for item in data['results']], [self.second.pk, self.first.pk])
        self.assertEqual([item['author'] for item in data['results']], ['Боб', 'Вы'])
        page = self.client.get(reverse('conversation', args=[self.thread.pk]))
        self.assertContains(page, 'data-chat-search="direct"')

    def test_outsider_cannot_search(self):
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(self.url, {'q': 'привет'}).status_code, 404)


class HistoryJumpTests(TransactionTestCase):
    def setUp(self):
        self.alice = User.objects.create_user('jump_alice', email='jump_alice@example.test')
        self.bob = User.objects.create_user('jump_bob', email='jump_bob@example.test')
        self.thread = DirectConversation.objects.create(user_low=self.alice, user_high=self.bob)
        DirectMessage.objects.bulk_create([DirectMessage(conversation=self.thread, sender=self.alice,
            client_id=uuid4(), content=f'Сообщение {i}') for i in range(130)])
        self.ids = list(self.thread.direct_messages.values_list('id', flat=True))

    def test_since_loads_the_whole_gap_without_holes(self):
        latest = history(self.bob.pk, self.thread.pk)
        first_loaded = latest['messages'][0]['id']
        target = self.ids[10]
        gap = history(self.bob.pk, self.thread.pk, before=first_loaded, since=target)
        loaded = [row['id'] for row in gap['messages']]
        self.assertEqual(loaded, [i for i in self.ids if target <= i < first_loaded])
        self.assertEqual(gap['direction'], 'before')
        self.assertTrue(gap['has_more'])  # Ten older messages remain.
        oldest = history(self.bob.pk, self.thread.pk, before=first_loaded, since=self.ids[0])
        self.assertFalse(oldest['has_more'])

    def test_large_gap_is_capped_next_to_loaded_history(self):
        DirectMessage.objects.bulk_create([DirectMessage(conversation=self.thread, sender=self.alice,
            client_id=uuid4(), content='ещё') for _ in range(JUMP_PAGE_SIZE)])
        ids = list(self.thread.direct_messages.values_list('id', flat=True))
        gap = history(self.bob.pk, self.thread.pk, before=ids[-1], since=ids[0])
        loaded = [row['id'] for row in gap['messages']]
        self.assertEqual(len(loaded), JUMP_PAGE_SIZE)
        self.assertEqual(loaded[-1], ids[-2])  # Adjacent to `before`, so repeating closes the gap.
        self.assertTrue(gap['has_more'])

    def test_socket_accepts_since_and_rejects_bad_values(self):
        client = Client()
        client.force_login(self.bob)
        cookie = f'{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}'.encode()

        async def run():
            from config.asgi import application
            socket = WebsocketCommunicator(application, '/ws/messages/',
                                           headers=[(b'origin', b'http://localhost'), (b'cookie', cookie)])
            self.assertTrue((await socket.connect())[0])
            self.assertEqual((await socket.receive_json_from())['type'], 'ready')
            await socket.send_json_to({'type': 'sync', 'conversation': str(self.thread.pk),
                                       'before': self.ids[-50], 'since': self.ids[5]})
            data = await socket.receive_json_from()
            self.assertEqual(data['messages'][0]['id'], self.ids[5])
            for bad in ({'since': '5'}, {'since': -1}, {'since': 5, 'after': 1}):
                await socket.send_json_to({'type': 'sync', 'conversation': str(self.thread.pk), **bad})
                self.assertEqual((await socket.receive_json_from())['type'], 'error')
            await socket.disconnect()
        async_to_sync(run)()

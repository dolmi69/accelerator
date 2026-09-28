import json
from uuid import uuid4
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from founder.models import (ChatMessage, ChatSession, DirectConversation, DirectMessage, ProjectCard,
                            ProjectBookmark, StartupMetrics, StartupProfile, User, UserBlock)
from founder.services.project_cards import (AI_FIELDS, StaleCardError, card_values, generate_card,
                                            get_card, save_card)
from founder.services.ai import AIServiceError
from founder.services.messaging import history, send_message, unread_count


@override_settings(AI_PROVIDER='demo')
class CommunityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('alice', email='alice@example.test')
        self.visitor = User.objects.create_user('bob', email='bob@example.test')
        self.project = StartupProfile.objects.create(owner=self.owner, name='Clinic', problem='PRIVATE PROFILE',
                                                    one_line_pitch='Планирование для клиник')
        self.card = get_card(self.project)
        self.values = {**card_values(self.card), 'summary': 'Расписание без очередей', 'problem': 'Ожидание приёма'}
        self.client.force_login(self.owner)

    def publish(self):
        save_card(self.card, self.values, self.card.revision, publish=True)

    def test_private_until_published_and_draft_edits_do_not_leak(self):
        self.client.force_login(self.visitor)
        detail = reverse('card_detail', args=[self.project.pk])
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.publish()
        response = self.client.get(detail)
        self.assertContains(response, 'Расписание без очередей')
        self.assertNotContains(response, 'PRIVATE PROFILE')
        self.assertNotContains(response, 'alice@example.test')
        save_card(self.card, {**self.values, 'summary': 'SECRET DRAFT'}, self.card.revision)
        self.assertNotContains(self.client.get(detail), 'SECRET DRAFT')
        self.assertNotContains(self.client.get(reverse('community') + '?q=SECRET'), 'Clinic')
        self.assertContains(self.client.get(reverse('community') + '?q=очередей'), 'Clinic')

    def test_only_owner_can_edit_generate_and_unpublish(self):
        self.client.force_login(self.visitor)
        for route in ('card_edit', 'card_generate', 'card_unpublish'):
            self.assertEqual(self.client.post(reverse(route, args=[self.project.pk]), {}).status_code, 404)
        self.assertEqual(self.client.get(reverse('card_edit', args=[self.project.pk])).status_code, 404)

    def test_manual_edits_publish_and_stale_revision_protection(self):
        url = reverse('card_edit', args=[self.project.pk])
        data = {**self.values, 'revision': self.card.revision, 'action': 'publish'}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertContains(self.client.get(url), 'Есть опубликованная версия')
        response = self.client.post(url, {**data, 'name': 'Stale overwrite'})
        self.assertEqual(response.status_code, 400)
        self.card.refresh_from_db()
        self.assertEqual(self.card.name, 'Clinic')
        self.assertEqual(self.card.published_data['name'], 'Clinic')

    def test_unpublish_removes_discovery_detail_and_saved_cards(self):
        self.publish()
        ProjectBookmark.objects.create(card=self.card, user=self.visitor)
        self.client.post(reverse('card_unpublish', args=[self.project.pk]))
        self.client.force_login(self.visitor)
        self.assertEqual(self.client.get(reverse('card_detail', args=[self.project.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('community')+'?saved=1'), 'Clinic')
        self.assertEqual(self.client.post(reverse('conversation_start', args=[self.project.pk])).status_code, 404)
        self.card.refresh_from_db()
        self.assertEqual(self.card.published_data, {})
        self.assertEqual(self.card.summary, self.values['summary'])

    def test_public_radar_is_opt_in_snapshot_without_private_notes(self):
        snapshot = StartupMetrics.objects.create(startup=self.project, product=10, market=20, finance=30,
            team=40, pitch=50, assessment_notes='CONFIDENTIAL', assessment_details={'product': 'SECRET NOTE'})
        self.publish()
        self.assertIsNone(self.card.published_data['radar'])
        save_card(self.card, {**self.values, 'share_radar': True}, self.card.revision, publish=True)
        radar = self.card.published_data['radar']
        self.assertEqual(radar['score'], 30)
        self.assertNotIn('CONFIDENTIAL', json.dumps(self.card.published_data))
        snapshot.product = 99
        snapshot.save()
        self.card.refresh_from_db()
        self.assertEqual(self.card.published_data['radar']['axes'][0]['score'], 10)

    def test_bookmarks_pair_reuse_and_blocking(self):
        self.publish()
        self.client.force_login(self.visitor)
        bookmark_url = reverse('bookmark', args=[self.project.pk])
        for _ in range(2):
            self.client.post(bookmark_url, {'save': '1'})
        self.assertEqual(ProjectBookmark.objects.count(), 1)
        start_url = reverse('conversation_start', args=[self.project.pk])
        for _ in range(2):
            self.assertEqual(self.client.post(start_url).status_code, 302)
        self.assertEqual(DirectConversation.objects.count(), 1)
        thread = DirectConversation.objects.get()
        self.assertContains(self.client.get(reverse('conversation', args=[thread.pk])), '@' + self.owner.handle)
        self.client.post(reverse('block_contact', args=[thread.pk]), {'block': '1'})
        self.assertEqual(self.client.post(start_url).status_code, 403)
        self.client.post(reverse('block_contact', args=[thread.pk]), {'block': '0'})
        self.assertEqual(self.client.post(start_url).status_code, 302)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(start_url).status_code, 403)

    @override_settings(AI_PROVIDER='gigachat')
    @patch('founder.services.project_cards.complete_text')
    @patch('founder.services.project_cards.assess_startup')
    def test_bruno_generates_reviewable_card_from_founder_context(self, assess, complete):
        session = ChatSession.objects.create(startup=self.project, mode='cofounder')
        ChatMessage.objects.create(session=session, role='user', content='Пилот в трёх клиниках')
        pitch = ChatSession.objects.create(startup=self.project, mode='pitch')
        ChatMessage.objects.create(session=pitch, role='user', content='PRIVATE REHEARSAL')
        candidate = {key: '' for key in AI_FIELDS}
        candidate.update(name='Clinic', summary='Пилот в трёх клиниках', looking_for='Разработчик')
        complete.return_value = json.dumps(candidate)
        assess.return_value = None
        response = self.client.post(reverse('card_generate', args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, candidate['summary'])
        assess.assert_called_once()
        context = complete.call_args.args[1]
        self.assertIn('Пилот в трёх клиниках', context)
        self.assertNotIn('PRIVATE REHEARSAL', context)
        self.card.refresh_from_db()
        self.assertEqual(self.card.summary, '')
        self.assertIsNone(self.card.published_at)
        # The generated proposal is editable and persists through the normal save flow.
        values = {**self.values, **candidate, 'revision': self.card.revision, 'action': 'save'}
        self.assertEqual(self.client.post(reverse('card_edit', args=[self.project.pk]), values).status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.summary, candidate['summary'])
        self.assertIsNone(self.card.published_at)

    @override_settings(AI_PROVIDER='gigachat')
    @patch('founder.services.project_cards.complete_text', return_value='{"name": true}')
    def test_bad_ai_response_does_not_destroy_card(self, complete):
        self.publish()
        with self.assertRaises(AIServiceError):
            generate_card(self.project, self.values)
        self.assertEqual(complete.call_count, 2)
        self.card.refresh_from_db()
        self.assertEqual(self.card.published_data['summary'], self.values['summary'])

    def test_bruno_chat_still_streams_under_asgi(self):
        from django.test import AsyncClient
        session = ChatSession.objects.create(startup=self.project, mode='cofounder')
        client = AsyncClient()
        client.force_login(self.owner)
        async def run():
            response = await client.post(reverse('chat_send', args=[self.project.pk, session.pk]),
                                         {'content': 'Делаем приложение для клиник'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.is_async)
            events = [chunk async for chunk in response.streaming_content]
            self.assertGreater(len(events), 2)
            self.assertIn(b'"type": "done"', events[-1])
        async_to_sync(run)()
        self.assertEqual(session.messages.filter(role='assistant').count(), 1)

    def test_xss_is_escaped_and_get_never_publishes_or_sends(self):
        self.values['summary'] = '<script>alert(1)</script>'
        self.publish()
        response = self.client.get(reverse('card_detail', args=[self.project.pk]))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        for name in ('card_generate', 'card_unpublish', 'conversation_start', 'bookmark'):
            self.assertEqual(self.client.get(reverse(name, args=[self.project.pk])).status_code, 405)


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
                   ALLOWED_HOSTS=['localhost', 'testserver'])
class WebSocketTests(TransactionTestCase):
    def setUp(self):
        self.alice = User.objects.create_user('socket_alice', email='socket_alice@example.test', password='test-password')
        self.bob = User.objects.create_user('socket_bob', email='socket_bob@example.test', password='test-password')
        self.eve = User.objects.create_user('socket_eve', email='socket_eve@example.test', password='test-password')
        self.thread = DirectConversation.objects.create(user_low=self.alice, user_high=self.bob)
        self.cookies = {}
        self.clients = {}
        for user in (self.alice, self.bob, self.eve):
            client = Client()
            client.force_login(user)
            self.clients[user.pk] = client
            self.cookies[user.pk] = f'{settings.SESSION_COOKIE_NAME}={client.cookies[settings.SESSION_COOKIE_NAME].value}'.encode()

    def socket(self, user=None, origin=b'http://localhost'):
        from config.asgi import application
        headers = [(b'origin', origin)]
        if user:
            headers.append((b'cookie', self.cookies[user.pk]))
        return WebsocketCommunicator(application, '/ws/messages/', headers=headers)

    async def connected(self, user):
        socket = self.socket(user)
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        self.assertEqual((await socket.receive_json_from())['type'], 'ready')
        return socket

    def test_rejects_anonymous_bad_origin_and_outsider_http(self):
        async def run():
            for sock in (self.socket(), self.socket(self.alice, b'https://attacker.example')):
                self.assertFalse((await sock.connect())[0])
                await sock.disconnect()
        async_to_sync(run)()
        self.assertEqual(self.clients[self.eve.pk].get(reverse('conversation', args=[self.thread.pk])).status_code, 404)

    def test_delivery_read_receipt_idempotence_and_reconnect(self):
        async def run():
            alice, bob = await self.connected(self.alice), await self.connected(self.bob)
            packet = {'type': 'send', 'conversation': str(self.thread.pk), 'client_id': str(uuid4()), 'content': 'Привет, обсудим проект?'}
            await alice.send_json_to(packet)
            alice_events = [await alice.receive_json_from(), await alice.receive_json_from()]
            self.assertEqual({event['type'] for event in alice_events}, {'ack', 'message'})
            received = await bob.receive_json_from()
            self.assertEqual(received['message']['content'], packet['content'])
            self.assertEqual(received['unread'], 1)
            message_id = received['message']['id']
            await bob.send_json_to({'type': 'read', 'conversation': str(self.thread.pk), 'id': message_id})
            self.assertEqual((await bob.receive_json_from())['unread'], 0)
            self.assertEqual((await alice.receive_json_from())['type'], 'read')
            await alice.send_json_to(packet)
            await alice.receive_json_from(); await alice.receive_json_from()
            self.assertEqual((await bob.receive_json_from())['message']['id'], message_id)
            await bob.disconnect()
            packet.update(client_id=str(uuid4()), content='Сообщение во время отключения')
            await alice.send_json_to(packet)
            await alice.receive_json_from(); await alice.receive_json_from()
            bob = await self.connected(self.bob)
            await bob.send_json_to({'type': 'sync', 'conversation': str(self.thread.pk), 'after': message_id})
            recovered = await bob.receive_json_from()
            self.assertEqual(len(recovered['messages']), 1)
            self.assertEqual(recovered['messages'][0]['content'], packet['content'])
            await alice.disconnect(); await bob.disconnect()
        async_to_sync(run)()
        self.assertEqual(DirectMessage.objects.count(), 2)

    def test_outsider_cannot_read_send_or_mark_read(self):
        async def run():
            eve = await self.connected(self.eve)
            for action in ('sync', 'send', 'read'):
                await eve.send_json_to({'type': action, 'conversation': str(self.thread.pk), 'client_id': str(uuid4()), 'content': 'attack', 'id': 1})
                self.assertEqual((await eve.receive_json_from())['type'], 'error')
            await eve.disconnect()
        async_to_sync(run)()
        self.assertFalse(DirectMessage.objects.exists())

    def test_block_logout_and_malformed_frames(self):
        async def run():
            alice = await self.connected(self.alice)
            await alice.send_to(text_data='["not-an-object"]')
            self.assertEqual((await alice.receive_json_from())['type'], 'error')
            await alice.send_json_to({'type': 'send', 'conversation': str(self.thread.pk), 'client_id': str(uuid4()), 'content': 'x' * 4001})
            self.assertEqual((await alice.receive_json_from())['type'], 'error')
            await database_sync_to_async(UserBlock.objects.create)(user=self.bob, blocked=self.alice)
            await alice.send_json_to({'type': 'send', 'conversation': str(self.thread.pk), 'client_id': str(uuid4()), 'content': 'blocked'})
            self.assertEqual((await alice.receive_json_from())['type'], 'error')
            await database_sync_to_async(self.clients[self.alice.pk].logout)()
            await alice.send_json_to({'type': 'ping'})
            self.assertEqual((await alice.receive_output())['code'], 4401)
            await alice.disconnect()
        async_to_sync(run)()
        self.assertFalse(DirectMessage.objects.exists())

    def test_history_pagination_and_persisted_throttle(self):
        DirectMessage.objects.bulk_create([DirectMessage(conversation=self.thread, sender=self.alice,
            client_id=uuid4(), content=f'Message {i}') for i in range(55)])
        result = history(self.bob.pk, self.thread.pk)
        self.assertEqual(len(result['messages']), 50)
        self.assertTrue(result['has_more'])
        older = history(self.bob.pk, self.thread.pk, before=result['messages'][0]['id'])
        self.assertEqual(len(older['messages']), 5)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            send_message(self.alice.pk, self.thread.pk, uuid4(), 'Too fast')
        self.assertEqual(unread_count(self.bob.pk), 55)

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatSession, EvidenceConversation, ProjectMember, StartupProfile, User


@override_settings(AI_PROVIDER='demo')
class BrunoHubTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='hub-owner', email='hub-owner@example.test')
        self.viewer = User.objects.create_user(username='hub-viewer', email='hub-viewer@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Hub project')
        ProjectMember.objects.create(startup=self.startup, user=self.viewer,
                                     role='viewer', status='active')
        self.client.force_login(self.owner)

    @patch('founder.views.stream_reply')
    def test_message_handoff_is_scoped_and_consumed_once_without_ai_on_get(self, reply):
        response = self.client.post(reverse('chat_create', args=[self.startup.pk]),
                                    {'from_hub': '1', 'content': 'Помоги проверить спрос'})
        chat = ChatSession.objects.get(startup=self.startup)
        self.assertRedirects(response, reverse('chat_detail', args=[self.startup.pk, chat.pk]),
                             fetch_redirect_response=False)
        detail = self.client.get(response.url)
        self.assertEqual(detail.context['hub_message'], 'Помоги проверить спрос')
        self.assertContains(detail, 'data-hub-send="true"')
        self.assertFalse(chat.messages.filter(role='user').exists())
        refreshed = self.client.get(response.url)
        self.assertEqual(refreshed.context['hub_message'], '')
        self.assertNotContains(refreshed, 'data-hub-send="true"')
        reply.assert_not_called()

    def test_empty_or_oversized_hub_message_does_not_create_chat(self):
        for content in ['  ', 'a' * 4001]:
            response = self.client.post(reverse('chat_create', args=[self.startup.pk]),
                                        {'from_hub': '1', 'content': content})
            self.assertRedirects(response, reverse('dashboard', args=[self.startup.pk]))
        self.assertFalse(ChatSession.objects.filter(startup=self.startup).exists())

    def test_resume_draft_is_private_and_viewer_cannot_start_chat(self):
        foreign = EvidenceConversation.objects.create(startup=self.startup, author=self.viewer)
        response = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertIsNone(response.context['hub_result_draft'])
        own = EvidenceConversation.objects.create(startup=self.startup, author=self.owner)
        response = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertEqual(response.context['hub_result_draft'], own)
        self.assertNotContains(response, '?draft=' + str(foreign.pk))
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertIsNone(response.context['hub_result_draft'])
        self.assertNotContains(response, 'id="bruno-hub-message"')
        response = self.client.post(reverse('chat_create', args=[self.startup.pk]),
                                    {'from_hub': '1', 'content': 'Недоступное действие'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ChatSession.objects.filter(startup=self.startup).exists())

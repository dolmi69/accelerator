"""Общие проекты: приглашения, роли и доступ соавторов и наблюдателей."""
from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatMessage, ChatSession, ProjectMember, StartupProfile, User
from founder.services.access import accessible_startups


@override_settings(AI_PROVIDER='demo')
class CollaborationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', email='owner@example.test', handle='owner_one')
        self.mate = User.objects.create_user('mate', email='mate@example.test', handle='mate_two',
                                             display_name='Марина')
        self.stranger = User.objects.create_user('stranger', email='s@example.test', handle='stranger_x')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Shared Clinic')
        self.session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)

    def url(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    def invite(self, role='editor'):
        self.client.force_login(self.owner)
        response = self.client.post(self.url('team_invite'), {'handle': '@Mate_Two', 'role': role})
        self.assertRedirects(response, self.url('team'))
        return ProjectMember.objects.get(startup=self.startup, user=self.mate)

    def accept(self, member):
        self.client.force_login(self.mate)
        response = self.client.post(reverse('invite_respond', args=[member.pk]), {'accept': '1'})
        self.assertRedirects(response, self.url('dashboard'))

    def test_invite_is_pending_until_accepted(self):
        member = self.invite()
        self.client.force_login(self.mate)
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 404)
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'Shared Clinic')
        self.assertContains(home, 'Принять')
        self.accept(member)
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 200)
        self.assertIn(self.startup, accessible_startups(self.mate))

    def test_declined_invite_grants_nothing(self):
        member = self.invite()
        self.client.force_login(self.mate)
        self.client.post(reverse('invite_respond', args=[member.pk]))
        self.assertFalse(ProjectMember.objects.exists())
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 404)

    def test_invite_validation(self):
        self.client.force_login(self.owner)
        for handle, error in (('@nobody_here', 'не найден'), ('owner_one', 'владелец')):
            response = self.client.post(self.url('team_invite'), {'handle': handle, 'role': 'editor'})
            self.assertContains(response, error, status_code=400)
        self.invite()
        response = self.client.post(self.url('team_invite'), {'handle': 'mate_two', 'role': 'viewer'})
        self.assertContains(response, 'уже в команде', status_code=400)

    def test_only_owner_manages_team(self):
        member = self.invite()
        self.accept(member)
        self.client.force_login(self.mate)
        self.assertEqual(self.client.post(self.url('team_invite'), {'handle': 'stranger_x', 'role': 'editor'}).status_code, 404)
        self.assertEqual(self.client.post(self.url('team_member_update', member.pk), {'action': 'remove'}).status_code, 404)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url('team')).status_code, 404)

    def test_editor_works_like_owner_and_messages_keep_author(self):
        self.accept(self.invite())
        for route, args in (('tasks', ()), ('evidence_list', ()), ('review', ()), ('lab', ()), ('card_edit', ()),
                            ('chat_detail', (self.session.pk,)), ('promote', ())):
            self.assertEqual(self.client.get(self.url(route, *args)).status_code, 200, route)
        response = self.client.post(self.url('chat_send', self.session.pk), {'content': 'Мы поговорили с пятью клиниками'})
        self.assertIn('"type": "done"', b''.join(response.streaming_content).decode())
        message = self.session.messages.get(role=ChatMessage.Role.USER)
        self.assertEqual(message.author, self.mate)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('chat_detail', self.session.pk)), 'Марина')

    def test_viewer_reads_but_cannot_change(self):
        self.accept(self.invite(role='viewer'))
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 200)
        chat = self.client.get(self.url('chat_detail', self.session.pk))
        self.assertNotContains(chat, 'id="chat-form"')
        self.assertContains(chat, 'Вы наблюдатель')
        for route, args in (('chat_send', (self.session.pk,)), ('chat_create', ()), ('metrics_assess', ()),
                            ('tasks_generate', ()), ('promote_buy', ()), ('lab_generate', ())):
            self.assertEqual(self.client.post(self.url(route, *args), {'content': 'x'}).status_code, 404, route)
        for route in ('card_edit', 'startup_edit', 'promote', 'evidence_create'):
            self.assertEqual(self.client.get(self.url(route)).status_code, 404, route)
        self.assertEqual(self.session.messages.count(), 0)

    def test_owner_can_demote_and_remove(self):
        member = self.invite()
        self.accept(member)
        self.client.force_login(self.owner)
        self.client.post(self.url('team_member_update', member.pk), {'action': 'role', 'role': 'viewer'})
        member.refresh_from_db()
        self.assertEqual(member.role, 'viewer')
        self.client.post(self.url('team_member_update', member.pk), {'action': 'remove'})
        self.client.force_login(self.mate)
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 404)

    def test_member_can_leave(self):
        self.accept(self.invite())
        self.assertRedirects(self.client.post(self.url('team_leave')), reverse('home'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.url('dashboard')).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url('team_leave')).status_code, 404)

    def test_shared_project_listed_with_role(self):
        self.accept(self.invite())
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'Shared Clinic')
        self.assertContains(home, 'СОАВТОР')

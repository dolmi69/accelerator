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


@override_settings(AI_PROVIDER='demo')
class JoinRequestTests(TestCase):
    def setUp(self):
        from founder.services.project_cards import card_values, get_card, save_card
        self.owner = User.objects.create_user('jr_owner', email='jro@example.test', handle='jr_owner')
        self.applicant = User.objects.create_user('jr_app', email='jra@example.test', handle='jr_app')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Open Team')
        card = get_card(self.startup)
        save_card(card, {**card_values(card), 'summary': 'Ищем дизайнера'}, card.revision, publish=True)
        self.join_url = reverse('join_request', args=[self.startup.pk])

    def apply(self, message='Дизайнер интерфейсов, 3 года в B2C'):
        self.client.force_login(self.applicant)
        return self.client.post(self.join_url, {'message': message})

    def test_request_then_owner_accepts_with_role(self):
        self.apply()
        member = ProjectMember.objects.get(user=self.applicant)
        self.assertEqual(member.status, ProjectMember.Status.REQUESTED)
        self.assertEqual(self.client.get(reverse('dashboard', args=[self.startup.pk])).status_code, 404)
        self.assertContains(self.client.get(reverse('card_detail', args=[self.startup.pk])), 'Отозвать заявку')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('home')), 'ХОТЯТ В ВАШУ КОМАНДУ')
        team = self.client.get(reverse('team', args=[self.startup.pk]))
        self.assertContains(team, 'Дизайнер интерфейсов')
        self.client.post(reverse('team_member_update', args=[self.startup.pk, member.pk]),
                         {'action': 'accept', 'role': 'viewer'})
        member.refresh_from_db()
        self.assertEqual((member.status, member.role), ('active', 'viewer'))
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse('dashboard', args=[self.startup.pk])).status_code, 200)

    def test_withdraw_decline_and_duplicates(self):
        self.apply()
        self.apply()
        self.assertEqual(ProjectMember.objects.count(), 1)
        self.client.post(self.join_url, {'action': 'withdraw'})
        self.assertFalse(ProjectMember.objects.exists())
        self.apply()
        member = ProjectMember.objects.get()
        self.client.force_login(self.owner)
        self.client.post(reverse('team_member_update', args=[self.startup.pk, member.pk]), {'action': 'remove'})
        self.assertFalse(ProjectMember.objects.exists())

    def test_rules(self):
        self.apply(message='hi')  # слишком коротко
        self.assertFalse(ProjectMember.objects.exists())
        self.client.force_login(self.owner)
        self.client.post(self.join_url, {'message': 'Я владелец этого проекта'})
        self.assertFalse(ProjectMember.objects.exists())
        hidden = StartupProfile.objects.create(owner=self.owner, name='Unpublished')
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.post(reverse('join_request', args=[hidden.pk]),
                                          {'message': 'Хочу помочь с маркетингом'}).status_code, 404)
        # Заявка не даёт права «принять» себя самому.
        self.apply()
        member = ProjectMember.objects.get()
        self.assertEqual(self.client.post(reverse('team_member_update', args=[self.startup.pk, member.pk]),
                                          {'action': 'accept'}).status_code, 404)


@override_settings(AI_PROVIDER='demo')
class AssigneeAndActivityTests(TestCase):
    def setUp(self):
        from founder.models import BrunoTask
        self.owner = User.objects.create_user('aa_owner', email='aao@example.test', handle='aa_owner')
        self.mate = User.objects.create_user('aa_mate', email='aam@example.test', handle='aa_mate', display_name='Олег')
        self.viewer = User.objects.create_user('aa_view', email='aav@example.test', handle='aa_view')
        self.stranger = User.objects.create_user('aa_str', email='aas@example.test', handle='aa_str')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Tasks Team')
        ProjectMember.objects.create(startup=self.startup, user=self.mate, status='active', role='editor')
        ProjectMember.objects.create(startup=self.startup, user=self.viewer, status='active', role='viewer')
        self.task = BrunoTask.objects.create(startup=self.startup, axis='market', title='Опросить 5 клиник',
                                             instructions='...', success_criterion='5 ответов')
        self.url = reverse('task_assign', args=[self.startup.pk, self.task.pk])

    def test_assign_only_to_editors_and_filter_mine(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {'assignee': self.mate.pk})
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee, self.mate)
        for bad in (self.viewer.pk, self.stranger.pk):
            self.assertEqual(self.client.post(self.url, {'assignee': bad}).status_code, 404)
        self.client.force_login(self.mate)
        self.assertContains(self.client.get(reverse('tasks', args=[self.startup.pk]) + '?mine=1'), 'Опросить 5 клиник')
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse('tasks', args=[self.startup.pk]) + '?mine=1'), 'Опросить 5 клиник')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url, {'assignee': ''}).status_code, 404)

    def test_leaving_releases_tasks(self):
        self.task.assignee = self.mate
        self.task.save()
        self.client.force_login(self.mate)
        self.client.post(reverse('team_leave', args=[self.startup.pk]))
        self.task.refresh_from_db()
        self.assertIsNone(self.task.assignee)

    def test_activity_feed_records_team_actions(self):
        self.client.force_login(self.mate)
        self.client.post(self.url, {'assignee': self.mate.pk})
        self.client.post(reverse('evidence_create', args=[self.startup.pk]) + f'?task={self.task.pk}', {
            'task': self.task.pk, 'axis': 'market', 'claim': 'Клиники ждут онлайн-запись', 'observation': '4 из 5',
            'observed_on': '2026-10-01', 'outcome': 'supported',
        })
        self.client.post(reverse('chat_create', args=[self.startup.pk]))
        texts = list(self.startup.activities.values_list('text', flat=True))
        self.assertTrue(any('исполнитель @aa_mate' in text for text in texts), texts)
        self.assertTrue(any(text.startswith('Задание выполнено') for text in texts), texts)
        self.assertTrue(any(text.startswith('Запись в дневнике') for text in texts), texts)
        self.assertIn('Новый разговор с Бруно', texts)
        self.client.force_login(self.owner)
        dashboard = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertContains(dashboard, 'Что нового в проекте')
        self.assertContains(dashboard, 'Олег')
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse('team', args=[self.startup.pk])).status_code, 404)

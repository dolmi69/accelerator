from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import BrunoTask, EvidenceEntry, MarketReport, ProjectMember, ProjectReview, StartupProfile, User
from founder.services.ai import AIServiceError


@override_settings(AI_PROVIDER='demo')
class RadarWorkspaceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='workspace-owner', email='owner@example.test')
        self.viewer = User.objects.create_user(username='workspace-viewer', email='viewer@example.test')
        self.invited = User.objects.create_user(username='workspace-invited', email='invited@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Coffee', problem='Очереди')
        self.foreign = StartupProfile.objects.create(owner=self.viewer, name='Private')
        self.client.force_login(self.owner)

    def test_home_shows_only_saved_work_of_this_project_and_no_manual_form(self):
        ProjectReview.objects.create(startup=self.startup, data={'focus': 'Проверить предзаказ', 'steps': [
            {'week': 1, 'title': 'Первые интервью', 'action': 'Спросить студентов', 'done_when': 'Пять ответов'},
        ]})
        MarketReport.objects.create(startup=self.startup, data={'competitors': [
            {'name': 'Кофейня рядом', 'what': 'Предзаказ кофе'},
        ]})
        BrunoTask.objects.create(startup=self.startup, axis='market', title='Опрос студентов')
        BrunoTask.objects.create(startup=self.startup, axis='product', title='Сделан прототип', status='done')
        BrunoTask.objects.create(startup=self.foreign, axis='market', title='Чужая тайная задача')
        ProjectMember.objects.create(startup=self.startup, user=self.viewer, status='active', role='viewer')
        ProjectMember.objects.create(startup=self.startup, user=self.invited, status='invited')
        page = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        for text in ['План от Бруно', 'Опрос студентов', self.viewer.public_name]:
            self.assertContains(page, text)
        self.assertNotContains(page, 'Чужая тайная задача')
        self.assertNotContains(page, self.invited.public_name)
        self.assertNotContains(page, 'id="edit-table"')
        self.assertNotContains(page, 'Доработать вручную')
        self.assertEqual(page.context['overview_member_count'], 2)
        self.assertEqual(page.context['overview_task_progress'], 50)
        self.assertContains(page, 'id="project-plan"', count=1)
        self.assertNotContains(page, 'id="project-market"')
        self.assertContains(page, 'id="project-tasks"', count=1)
        self.assertContains(page, 'id="project-team"', count=1)

    def test_viewer_can_read_but_cannot_generate_from_home_or_endpoints(self):
        ProjectMember.objects.create(startup=self.startup, user=self.viewer, status='active', role='viewer')
        task = BrunoTask.objects.create(startup=self.startup, axis='market', title='Проверить спрос')
        self.client.force_login(self.viewer)
        page = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertEqual(page.status_code, 200)
        for name in ['review_generate', 'market_generate', 'tasks_generate']:
            url = reverse(name, args=[self.startup.pk])
            self.assertNotContains(page, 'action="' + url + '"')
            self.assertEqual(self.client.post(url, {'return_to': 'radar'}).status_code, 404)
        self.assertContains(page, 'class="hub-task-check"')
        self.assertNotContains(page, 'class="radar-task-dialog"')
        self.assertEqual(self.client.post(reverse('evidence_create', args=[self.startup.pk]), {
            'task': str(task.pk), 'return_to': 'radar',
        }).status_code, 404)

    def test_hub_completion_preserves_invalid_result_and_saves_negative_observation(self):
        task = BrunoTask.objects.create(startup=self.startup, axis='market', title='Проверить спрос')
        url = reverse('evidence_create', args=[self.startup.pk])
        data = {'task': str(task.pk), 'axis': task.axis, 'claim': task.title,
                'observation': '', 'observed_on': str(timezone.localdate()),
                'outcome': 'refuted', 'return_to': 'radar'}
        invalid = self.client.post(url, data)
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, 'name="return_to" value="radar"', status_code=400)
        task.refresh_from_db()
        self.assertEqual(task.status, 'todo')
        self.assertFalse(EvidenceEntry.objects.exists())
        data['observation'] = 'Трое студентов отказались от предзаказа.'
        saved = self.client.post(url, data)
        self.assertRedirects(saved, reverse('dashboard', args=[self.startup.pk]) + '#project-tasks')
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertIsNotNone(task.completed_at)
        evidence = EvidenceEntry.objects.get()
        self.assertEqual((evidence.task_id, evidence.outcome, evidence.observation),
                         (task.pk, 'refuted', data['observation']))
        self.assertFalse(self.startup.metric_snapshots.exists())
        page = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertEqual(page.context['overview_completed_count'], 1)
        self.assertNotContains(page, 'Выполнено заданий:')
        self.assertContains(page, 'Действия участников')

    def test_generators_return_to_the_correct_home_section_including_on_error(self):
        cases = [
            ('review_generate', 'founder.workbench_views.create_review', 'project-plan'),
            ('market_generate', 'founder.workbench_views.market_service.create_market_report', 'project-market'),
            ('tasks_generate', 'founder.workbench_views.create_review', 'project-plan'),
        ]
        for name, target, section in cases:
            with self.subTest(name=name), patch(target, return_value=[]), patch('founder.workbench_views.populate_tasks', return_value=0):
                page = self.client.post(reverse(name, args=[self.startup.pk]), {'return_to': 'radar'})
                self.assertRedirects(page, reverse('dashboard', args=[self.startup.pk]) + '#' + section)
            with self.subTest(error=name), patch(target, side_effect=AIServiceError('Провайдер недоступен')):
                page = self.client.post(reverse(name, args=[self.startup.pk]), {'return_to': 'radar'}, follow=True)
                self.assertContains(page, 'Провайдер недоступен')
        with patch('founder.workbench_views.create_review', return_value=[]), patch('founder.workbench_views.populate_tasks', return_value=0):
            page = self.client.post(reverse('review_generate', args=[self.startup.pk]), {'return_to': 'https://outside.test'})
            self.assertRedirects(page, reverse('tasks', args=[self.startup.pk]))

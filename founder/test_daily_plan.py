import json
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import BrunoTask, FeatureCandidate, ProjectMember, StartupProfile, User
from founder.services.ai import AIResponseFormatError, AIServiceError
from founder.services.daily_plan import display, generate


@override_settings(AI_PROVIDER='gigachat')
class DailyPlanTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='day-owner', email='day-owner@example.test')
        self.other = User.objects.create_user(username='day-other', email='day-other@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Daily')
        self.task = BrunoTask.objects.create(startup=self.startup, axis='market', title='Интервью')
        self.payload = {'summary': 'Сначала проверить спрос.', 'actions': [
            {'target': 'task_1', 'title': 'Поговорить с клиентами', 'detail': 'Начните с первого интервью.'}]}
        self.client.force_login(self.owner)

    def run_generate(self, payload=None):
        with patch('founder.services.daily_plan.complete_text', return_value=json.dumps(payload or self.payload)) as ai, \
                patch('founder.services.review._review_context', return_value='Saved facts') as context:
            result = generate(self.startup)
        ai.assert_called_once()
        context.assert_called_once_with(self.startup, extract_economics=False)
        return result

    def test_persist_and_render_progress_without_new_ai(self):
        self.run_generate()
        self.startup.refresh_from_db()
        with patch('founder.services.daily_plan.complete_text') as ai:
            for _ in range(2):
                page = self.client.get(reverse('dashboard', args=[self.startup.pk]))
                self.assertContains(page, 'План от Бруно')
                self.assertContains(page, '0 из 1 выполнено')
            ai.assert_not_called()
        self.task.status = 'done'
        self.task.save()
        self.assertEqual(display(self.startup, can_edit=True)['actions'][0]['progress'], '1 из 1 выполнено')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'done')

    def test_reject_unknown_refs_invalid_kinds_and_oversized_plan_preserving_previous(self):
        self.run_generate()
        baseline = deepcopy(self.startup.daily_plan)
        invalids = []
        for target in ['foreign_uuid', 'task_999', 'unknown']:
            candidate = deepcopy(self.payload)
            candidate['actions'][0]['target'] = target
            invalids.append(candidate)
        invalids.append({'summary': 'x', 'actions': self.payload['actions'] * 4})
        invalids.append({'summary': 'x', 'actions': [{'target': 'team', 'title': 'x', 'detail': 'x'}]})
        for payload in invalids:
            with self.subTest(payload=payload), self.assertRaises(AIResponseFormatError):
                self.run_generate(payload)
            self.startup.refresh_from_db()
            self.assertEqual(self.startup.daily_plan, baseline)

    def test_team_suggestion_only_with_active_editor_and_unassigned_task(self):
        ProjectMember.objects.create(startup=self.startup, user=self.other, status='active', role='editor')
        self.run_generate({'summary': 'Разделим работу.', 'actions': [
            {'target': 'team', 'title': 'Распределить задачи', 'detail': 'Выберите ответственного за интервью.'}]})
        self.assertEqual(self.startup.daily_plan['actions'][0]['kind'], 'team')
        self.task.refresh_from_db()
        self.assertIsNone(self.task.assignee_id)

    def test_linked_mvp_task_uses_canonical_feature_estimates(self):
        FeatureCandidate.objects.create(startup=self.startup, task=self.task, title='Каталог',
                                        reach=100, rice_impact=1, confidence=80, effort=2,
                                        ice_impact=5, ease=7)
        with patch('founder.services.daily_plan.complete_text', return_value=json.dumps(self.payload)) as ai, \
                patch('founder.services.review._review_context', return_value='Saved facts'):
            generate(self.startup)
        self.assertIn('"rice": "40', ai.call_args.args[1])

    def test_distinct_task_actions_are_valid_but_overlapping_tasks_are_rejected(self):
        BrunoTask.objects.create(startup=self.startup, axis='team', title='Распределить роли')
        payload = deepcopy(self.payload)
        payload['actions'].append(dict(payload['actions'][0], target='task_2'))
        self.run_generate(payload)
        self.assertEqual(len(self.startup.daily_plan['actions']), 2)
        payload['actions'][1]['target'] = 'tasks_top_2'
        with self.assertRaises(AIResponseFormatError):
            self.run_generate(payload)

    def test_no_get_generation_and_no_viewer_or_foreign_post(self):
        url = reverse('daily_plan_generate', args=[self.startup.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(self.other)
        with patch('founder.services.daily_plan.complete_text') as ai:
            self.assertEqual(self.client.post(url).status_code, 404)
            ProjectMember.objects.create(startup=self.startup, user=self.other, status='active', role='viewer')
            self.assertEqual(self.client.post(url).status_code, 404)
            ai.assert_not_called()

    def test_provider_failure_preserves_saved_plan(self):
        self.run_generate()
        baseline = deepcopy(self.startup.daily_plan)
        with patch('founder.services.daily_plan.complete_text', side_effect=AIServiceError('Unavailable')):
            response = self.client.post(reverse('daily_plan_generate', args=[self.startup.pk]))
        self.assertEqual(response.status_code, 302)
        self.startup.refresh_from_db()
        self.assertEqual(self.startup.daily_plan, baseline)

    def test_old_plan_is_labelled_with_its_date(self):
        self.run_generate()
        self.startup.daily_plan['day'] = '2020-01-01'
        self.assertFalse(display(self.startup, can_edit=True)['today'])

"""The unified work area shares one analysis, preserving human decisions."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import BrunoTask, FeatureCandidate, ProjectMember, ProjectReview, StartupProfile, User
from founder.services.ai import AIResponseFormatError
from founder.services.review import context_digest, create_review, parse_review, step_to_task
from founder.test_review import model_review, model_estimates_for


@override_settings(AI_PROVIDER='demo')
class ProjectWorkTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='work-owner', email='work-owner@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Аренда', problem='Нужен инструмент')
        self.client.force_login(self.owner)
        self.item = {'title': 'Каталог', 'description': 'Найти инструмент',
                     'question_present': 'Как вам наличие каталога?',
                     'question_absent': 'Как вам отсутствие каталога?'}

    def route(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    @override_settings(AI_PROVIDER='gigachat')
    def test_one_ai_call_saves_review_and_immediately_creates_tasks(self):
        existing = FeatureCandidate.objects.create(startup=self.startup, title='Личная оценка',
                                                   priority='wont', reach=25, confidence=80)
        with patch('founder.services.review.complete_text', return_value=model_review(
                features=[self.item], estimates=model_estimates_for(self.startup))) as ai:
            response = self.client.post(self.route('review_generate'))
        self.assertRedirects(response, self.route('tasks'))
        self.assertEqual(ai.call_count, 1)
        proposed = ProjectReview.objects.get().data['features'][0]
        self.assertEqual({key: value for key, value in proposed.items() if key != 'estimate'}, self.item)
        self.assertIn('estimate', proposed)
        self.assertEqual(self.startup.bruno_tasks.count(), 3)
        self.assertEqual(self.startup.feature_candidates.count(), 1)
        existing.refresh_from_db()
        self.assertEqual((existing.priority, existing.reach), ('wont', 25))

    def test_accepting_drafts_is_free_idempotent_and_preserves_manual_estimates(self):
        existing = FeatureCandidate.objects.create(startup=self.startup, title='Каталог',
                                                   priority='could', reach=17, description='Моё описание')
        review = ProjectReview.objects.create(startup=self.startup, data={'features': [self.item, {
            **self.item, 'title': 'Поиск',
        }]})
        with patch('founder.services.ai.complete_text', side_effect=AssertionError('Acceptance is not AI')):
            for _ in range(2):
                self.assertEqual(self.client.post(self.route('features_suggest'), {'review': review.pk}).status_code, 302)
        self.assertEqual(self.startup.feature_candidates.count(), 2)
        existing.refresh_from_db()
        self.assertEqual((existing.reach, existing.priority, existing.description), (17, 'could', 'Моё описание'))
        self.assertIsNone(self.startup.feature_candidates.get(title='Поиск').confidence)

    def test_completed_or_skipped_step_cannot_be_accepted_twice(self):
        review = create_review(self.startup)
        task = step_to_task(review, 0)
        task.status = BrunoTask.Status.SKIPPED
        task.save(update_fields=['status'])
        self.assertIsNone(step_to_task(review, 0))
        self.assertEqual(self.startup.bruno_tasks.count(), 1)

    def test_manual_focus_is_persisted_and_changes_analysis_freshness(self):
        review = create_review(self.startup)
        self.assertEqual(review.data['_context_digest'], context_digest(self.startup))
        self.assertEqual(self.client.post(self.route('work_focus'), {'focus': 'product'}).status_code, 302)
        self.startup.refresh_from_db()
        self.assertEqual(self.startup.work_focus, 'product')
        page = self.client.get(self.route('tasks'))
        self.assertTrue(page.context['review_stale'])
        self.assertEqual(page.context['readiness']['focus'], 'product')
        self.assertEqual(self.client.post(self.route('work_focus'), {'focus': 'invented'}).status_code, 400)

    def test_views_and_history_never_generate_and_keep_legacy_links(self):
        old = ProjectReview.objects.create(startup=self.startup, data=json.loads(model_review()))
        create_review(self.startup)
        with patch('founder.services.ai.complete_text', side_effect=AssertionError('Read-only')):
            for name in ('tasks', 'review'):
                page = self.client.get(self.route(name), {'id': old.pk})
                self.assertContains(page, 'Работа над проектом')
                self.assertEqual(page.context['review'], old)
        self.assertEqual(self.client.get(self.route('tasks'), {'review': 'bad-id'}).status_code, 404)

    def test_viewer_and_foreign_project_cannot_write_or_accept_reviews(self):
        viewer = User.objects.create_user(username='work-viewer', email='work-viewer@example.test')
        ProjectMember.objects.create(startup=self.startup, user=viewer, status='active', role='viewer')
        review = create_review(self.startup)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.route('tasks')).status_code, 200)
        for name, body in [('work_focus', {'focus': 'revenue'}), ('features_suggest', {'review': review.pk}),
                           ('review_generate', {}), ('tasks_generate', {})]:
            self.assertEqual(self.client.post(self.route(name), body).status_code, 404)
        self.client.force_login(self.owner)
        foreign = StartupProfile.objects.create(owner=viewer, name='Другой')
        foreign_review = create_review(foreign)
        self.assertEqual(self.client.post(self.route('features_suggest'), {'review': foreign_review.pk}).status_code, 404)
        self.assertEqual(self.client.post(self.route('features_suggest'), {'review': 'bad-id'}).status_code, 404)

    def test_numeric_ai_drafts_are_rejected_before_any_save(self):
        for field in ('reach', 'confidence', 'priority', 'effort'):
            with self.subTest(field=field), self.assertRaises(AIResponseFormatError):
                parse_review(model_review(features=[{**self.item, field: 50}]))
        self.assertFalse(ProjectReview.objects.exists())

    @override_settings(AI_PROVIDER='gigachat')
    def test_tasks_use_live_scores_and_do_not_recreate_feature_work(self):
        from founder.services.review import populate_tasks
        from decimal import Decimal
        feature = FeatureCandidate.objects.create(startup=self.startup, title='Каталог',
            reach=100, rice_impact=2, confidence=80, effort=4, ice_impact=7, ease=6)
        payload = json.loads(model_review())
        payload['steps'][2]['feature_id'] = str(feature.pk)
        payload['estimates'] = model_estimates_for(self.startup)
        with patch('founder.services.review.complete_text', return_value=json.dumps(payload)) as ai:
            response = self.client.post(self.route('review_generate'), {'method': 'ice'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('ice', ai.call_args.args[1])
        page = self.client.get(self.route('tasks'), {'method': 'ice'})
        task = next(t for t in page.context['open_tasks'] if t.ranked_feature)
        self.assertEqual(task.ranked_feature.score, Decimal(336))
        feature.refresh_from_db()
        self.assertEqual(feature.task_id, task.pk)
        review = ProjectReview.objects.get()
        self.assertEqual(populate_tasks(review), 0)
        self.assertEqual(self.startup.bruno_tasks.count(), 3)
        self.assertEqual(review.data['_context_digest'], context_digest(self.startup))

    @override_settings(AI_PROVIDER='gigachat')
    def test_unknown_feature_reference_does_not_save_partial_plan(self):
        from uuid import uuid4
        payload = json.loads(model_review())
        payload['steps'][0]['feature_id'] = str(uuid4())
        with patch('founder.services.review.complete_text', return_value=json.dumps(payload)):
            self.client.post(self.route('review_generate'))
        self.assertFalse(ProjectReview.objects.exists())
        self.assertFalse(self.startup.bruno_tasks.exists())

    @override_settings(AI_PROVIDER='gigachat')
    def test_explicit_wont_does_not_become_a_task(self):
        feature = FeatureCandidate.objects.create(startup=self.startup, title='Каталог', priority='wont')
        payload = json.loads(model_review())
        payload['steps'][2]['feature_id'] = str(feature.pk)
        payload['estimates'] = model_estimates_for(self.startup)
        with patch('founder.services.review.complete_text', return_value=json.dumps(payload)):
            self.client.post(self.route('review_generate'))
        self.assertEqual(self.startup.bruno_tasks.count(), 2)
        self.assertFalse(self.startup.bruno_tasks.filter(axis='product').exists())

    def test_single_queue_shows_scores_and_inline_execution_without_duplicate_cards(self):
        from decimal import Decimal
        low = BrunoTask.objects.create(startup=self.startup, origin='mvp', axis='product', title='Низкий балл')
        high = BrunoTask.objects.create(startup=self.startup, origin='mvp', axis='product', title='Высокий балл')
        check = BrunoTask.objects.create(startup=self.startup, axis='market', title='Проверка потребности')
        FeatureCandidate.objects.create(startup=self.startup, title='Низкая функция', task=low,
            reach=10, rice_impact=1, confidence=50, effort=2, ice_impact=9, ease=8)
        FeatureCandidate.objects.create(startup=self.startup, title='Высокая функция', task=high,
            reach=100, rice_impact=2, confidence=80, effort=4, ice_impact=1, ease=1)
        page = self.client.get(self.route('tasks'))
        rows = page.context['open_tasks']
        ranked = [task for task in rows if task.ranked_feature]
        self.assertEqual([task.pk for task in ranked], [high.pk, low.pk])
        self.assertEqual(ranked[0].ranked_feature.score, Decimal(40))
        self.assertEqual([task.rank for task in rows], list(range(1, 4)))
        self.assertIsNone(next(task for task in rows if task.pk == check.pk).ranked_feature)
        for text in ('Итоговое решение', 'Функции MVP', 'Как выполнить', '40,00', 'priority-table'):
            self.assertContains(page, text)
        self.assertNotContains(page, 'project-task-card')
        self.assertNotContains(page, 'К заданию ↓')
        self.assertContains(page, f'id="task-{high.pk}"', count=1)
        self.assertNotContains(page, reverse('result_tell', args=[self.startup.pk]) + f'?task={high.pk}')
        self.assertContains(page, reverse('result_tell', args=[self.startup.pk]) + f'?task={check.pk}', count=1)
        page = self.client.get(self.route('tasks'), {'method':'ice'})
        ranked = [task for task in page.context['open_tasks'] if task.ranked_feature]
        self.assertEqual([task.pk for task in ranked], [low.pk, high.pk])
        self.assertContains(page, 'RICE')
        self.assertContains(page, 'ICE')
        self.assertEqual(page.context['mvp_first'].pk, ranked[0].ranked_feature.pk)

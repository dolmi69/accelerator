"""Pausing surveys preserves records and closes every survey entry point."""
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from founder.models import BrunoTask, FeatureCandidate, KanoResponse, StartupProfile, User
from founder.feature_forms import FeatureForm
from founder.forms import EvidenceForm
from founder.services import mvp
from founder.services.bruno_estimates import collect_targets
from founder.services.dashboard import workspace_context
from founder.services.review import create_review, step_to_task
from founder.test_review import model_review, model_estimates_for


@override_settings(KANO_SURVEYS_ENABLED=False, AI_PROVIDER='gigachat')
class PausedKanoTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('paused-kano', email='paused@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Каталог', problem='Помочь найти подходящий инструмент')
        self.survey = BrunoTask.objects.create(startup=self.startup, origin='kano', axis='product',
            title='Опросить пользователей: Каталог', instructions='Опрос Кано', success_criterion='Ответы')
        self.interview = BrunoTask.objects.create(startup=self.startup, axis='market',
            title='Поговорить с клиентами', instructions='Интервью', success_criterion='Наблюдения')
        self.feature = FeatureCandidate.objects.create(startup=self.startup, title='Каталог', priority='could',
            question_present='Если каталог есть?', question_absent='Если его нет?', kano_task=self.survey)
        self.answer = KanoResponse.objects.create(feature=self.feature, respondent='one', present=2, absent=5)
        self.client.force_login(self.owner)

    def url(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    def test_hidden_from_queue_dashboard_and_estimation_without_deleting(self):
        page = self.client.get(self.url('tasks'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Опрос Кано')
        self.assertNotContains(page, 'Опросить пользователей: Каталог')
        self.assertContains(page, 'Поговорить с клиентами')
        self.assertEqual(page.context['priority_count'], 2)
        self.assertNotIn(self.survey, EvidenceForm(startup=self.startup).fields['task'].queryset)
        self.assertTrue(all(not row['kano_url'] for row in page.context['priority_entries']))
        self.assertEqual(workspace_context(self.startup)['overview_task_count'], 1)
        self.assertNotIn(str(self.survey.pk), {target['id'] for target in collect_targets(self.startup)})
        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, 'todo')
        self.assertTrue(KanoResponse.objects.filter(pk=self.answer.pk).exists())

    def test_direct_survey_routes_and_task_actions_are_closed_without_ai(self):
        routes = [('feature_kano', [self.feature.pk]),
                  ('feature_kano_edit', [self.feature.pk, self.answer.pk]),
                  ('feature_survey_prepare', [self.feature.pk])]
        with patch('founder.services.feature_ai.complete_text', side_effect=AssertionError('No API call')):
            for name, args in routes:
                self.assertEqual(self.client.post(self.url(name, *args), {'with_ai': '1'}).status_code, 404)
            self.assertEqual(self.client.get(self.url('feature_kano', self.feature.pk)).status_code, 404)
            self.assertEqual(self.client.get(self.url('task_estimate', self.survey.pk)).status_code, 404)
        with self.assertRaises(ValueError):
            mvp.create_survey_task(self.feature)
        self.assertEqual(self.startup.bruno_tasks.count(), 2)

    def test_feature_edit_preserves_hidden_questions_and_answers(self):
        form = FeatureForm(instance=self.feature, startup=self.startup)
        self.assertNotIn('question_present', form.fields)
        page = self.client.get(self.url('feature_edit', self.feature.pk))
        self.assertNotContains(page, 'Формулировки опроса Кано')
        self.assertEqual(self.client.post(self.url('feature_edit', self.feature.pk), {
            'title': 'Новый каталог', 'priority': 'could', 'question_present': 'Заменить'}).status_code, 302)
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.question_present, 'Если каталог есть?')
        self.assertEqual(self.feature.question_absent, 'Если его нет?')
        self.assertEqual(self.feature.kano_responses.count(), 1)

    def test_kano_no_longer_overrides_mvp_but_manual_must_works(self):
        self.assertFalse(mvp.board(self.startup)[0].in_mvp)
        self.feature.priority = 'must'
        self.feature.save()
        self.assertTrue(mvp.board(self.startup)[0].in_mvp)
        with override_settings(KANO_SURVEYS_ENABLED=True):
            self.feature.priority = 'could'
            self.feature.save()
            self.assertTrue(mvp.board(self.startup)[0].in_mvp)

    def test_new_review_requests_no_survey_fields_and_old_steps_cannot_reappear(self):
        raw = model_review(features=[{'title': 'Поиск', 'description': 'Найти товар'}],
                           estimates=model_estimates_for(self.startup))
        # model_review fixture only supplies numeric estimates, not survey questions.
        with patch('founder.services.review.complete_text', return_value=raw) as ai:
            review = create_review(self.startup)
        schema = ai.call_args.kwargs['json_schema']['properties']['features']['items']
        self.assertNotIn('question_present', schema['properties'])
        self.assertNotIn('question_absent', schema['required'])
        self.assertNotIn('нейтральная пара Кано', ai.call_args.args[0])
        self.assertNotIn(self.survey.title, ai.call_args.args[1])
        review.data['steps'] = [{'axis': 'product', 'title': 'Опрос Кано', 'action': 'Опросить людей', 'done_when': 'Ответы'}]
        review.save()
        self.assertIsNone(step_to_task(review, 0))
        self.assertEqual(self.startup.bruno_tasks.count(), 2)

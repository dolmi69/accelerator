"""Bruno supplies assumptions; Python validates, scopes and calculates them."""
import json
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import (AIRequestLease, BrunoTask, FeatureCandidate, KanoResponse, ProjectMember,
                            ProjectReview, StartupProfile, TaskEstimate, User)
from founder.services.ai import AIResponseFormatError, AIServiceError
from founder.services.bruno_estimates import batch_request, collect_targets, generate_estimates, parse_existing
from founder.services.priority_estimates import parse_estimate, resolve_estimate
from founder.test_review import model_estimate, model_estimates_for, model_review


@override_settings(AI_PROVIDER='gigachat')
class BrunoEstimateTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('bruno-estimates-owner', email='bruno-estimates@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Аренда', problem='Редко нужен инструмент')
        self.task = BrunoTask.objects.create(startup=self.startup, axis='market', title='Провести интервью',
                                            instructions='Поговорить с арендаторами', success_criterion='Записаны ответы')
        self.feature = FeatureCandidate.objects.create(startup=self.startup, title='Бронирование',
            description='Выбор свободных дат', reach=0, confidence=80, priority='wont')
        self.client.force_login(self.owner)

    def url(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    def answer(self):
        return {'estimates': {f'item_{index}': model_estimate()
                              for index, _ in enumerate(collect_targets(self.startup, refresh=True), 1)}}

    def test_one_batch_retains_model_reason_and_manual_zero_and_priority(self):
        answer = self.answer()
        by_kind = {target['kind']: ref for ref, target in
                   batch_request(collect_targets(self.startup))[0].items()}
        answer['estimates'][by_kind['task']] = model_estimate(reach=11, effort=.3, confidence=60)
        answer['estimates'][by_kind['feature']] = model_estimate(reach=999, effort=5)
        with patch('founder.services.bruno_estimates.complete_text', return_value=json.dumps(answer)) as ai, \
             patch('founder.services.bruno_estimates.provider_label', return_value=('test', 'real-model-name')):
            self.assertEqual(generate_estimates(self.startup), 2)
        self.assertEqual(ai.call_count, 1)
        saved = TaskEstimate.objects.get(task=self.task)
        self.assertIsNone(saved.reach)
        self.assertEqual(saved.bruno_estimate['model'], 'real-model-name')
        self.assertEqual(saved.bruno_estimate['values']['reach'], 11)
        self.assertEqual(resolve_estimate(saved).rice, Decimal(44))
        self.feature.refresh_from_db()
        self.assertEqual((self.feature.reach, self.feature.confidence, self.feature.priority), (0, 80, 'wont'))
        self.assertEqual(resolve_estimate(self.feature).rice, 0)
        self.assertTrue(resolve_estimate(self.feature).preliminary)
        with patch('founder.services.bruno_estimates.complete_text', side_effect=AssertionError('GET is free')):
            page = self.client.get(self.url('tasks'))
            self.assertContains(page, 'Оценка Бруно')
            self.assertContains(page, 'Предположение по контексту')
            self.assertEqual(generate_estimates(self.startup), 0)
        self.assertFalse(KanoResponse.objects.exists())

    def test_all_proposals_are_rejected_when_one_value_is_invalid(self):
        for changes in ({'reach': True}, {'effort': 0}, {'confidence': 101}, {'ease': 11},
                        {'ice_impact': 1.5}, {'rice_impact': 4}, {'confidence': float('nan')},
                        {'effort': .123}, {'estimate_basis': ''}, {'priority': 'must'}):
            with self.subTest(changes=changes):
                answer = self.answer()
                next(reversed(answer['estimates'].values())).update(changes)
                with patch('founder.services.bruno_estimates.complete_text', return_value=json.dumps(answer)), \
                     self.assertRaises(AIResponseFormatError):
                    generate_estimates(self.startup)
                self.assertFalse(TaskEstimate.objects.exists())
                self.feature.refresh_from_db()
                self.assertEqual(self.feature.bruno_estimate, {})

    def test_foreign_duplicate_and_missing_targets_never_partially_save(self):
        for mutation in ('foreign', 'duplicate', 'missing'):
            answer = {'estimates': model_estimates_for(self.startup)}
            if mutation == 'foreign':
                answer['estimates'][0]['id'] = str(uuid4())
            elif mutation == 'duplicate':
                answer['estimates'][1] = answer['estimates'][0]
            else:
                answer['estimates'].pop()
            with self.assertRaises(AIResponseFormatError):
                parse_existing(answer['estimates'], collect_targets(self.startup))
            self.assertFalse(TaskEstimate.objects.exists())
        foreign = StartupProfile.objects.create(owner=self.owner, name='Отдельный проект')
        private = FeatureCandidate.objects.create(startup=foreign, title='Секретная функция')
        self.assertNotIn(str(private.pk), json.dumps(model_estimates_for(self.startup)))

    def test_batch_schema_requires_every_slot_and_maps_reordered_response_without_uuid_echo(self):
        targets = collect_targets(self.startup)
        slots, context, schema = batch_request(targets)
        self.assertEqual(set(schema['properties']['estimates']['required']), set(slots))
        self.assertEqual(set(schema['properties']['estimates']['properties']), set(slots))
        self.assertFalse(schema['properties']['estimates']['additionalProperties'])
        self.assertEqual(set(context), set(slots))
        self.assertNotIn(str(self.task.pk), json.dumps(context, default=str))
        self.assertNotIn(str(self.feature.pk), json.dumps(context, default=str))
        answer = self.answer()
        answer['estimates'] = dict(reversed(list(answer['estimates'].items())))
        for index, ref in enumerate(slots, 1):
            answer['estimates'][ref]['reach'] = index * 10
        with patch('founder.services.bruno_estimates.complete_text', return_value=json.dumps(answer)):
            self.assertEqual(generate_estimates(self.startup), 2)
        for index, target in enumerate(slots.values(), 1):
            saved = (FeatureCandidate.objects.get(pk=target['id']) if target['kind'] == 'feature'
                     else TaskEstimate.objects.get(task_id=target['id']))
            self.assertEqual(saved.bruno_estimate['values']['reach'], index * 10)

    def test_batch_missing_extra_and_legacy_array_never_partially_save(self):
        for mutation in ('missing', 'extra', 'array', 'root'):
            with self.subTest(mutation=mutation):
                answer = self.answer()
                if mutation == 'missing':
                    answer['estimates'].pop('item_1')
                elif mutation == 'extra':
                    answer['estimates']['item_99'] = model_estimate()
                elif mutation == 'array':
                    answer['estimates'] = model_estimates_for(self.startup)
                else:
                    answer['steps'] = []
                with patch('founder.services.bruno_estimates.complete_text', return_value=json.dumps(answer)) as ai, \
                     self.assertRaises(AIResponseFormatError):
                    generate_estimates(self.startup)
                self.assertEqual(ai.call_count, 1)
                self.assertFalse(TaskEstimate.objects.exists())
                self.feature.refresh_from_db()
                self.assertEqual(self.feature.bruno_estimate, {})

    def test_old_task_only_project_uses_one_call_without_hidden_economics_extraction(self):
        self.feature.delete()
        self.startup.one_line_pitch = 'Расчёт сметы за 2000 ₽'
        self.startup.save(update_fields=['one_line_pitch'])
        ProjectReview.objects.create(startup=self.startup, data={'focus': 'Прежний план без оценок'})
        for axis, title in (('finance', 'Проверить оплату'), ('team', 'Распределить роли'),
                            ('product', 'Проверить результат')):
            BrunoTask.objects.create(startup=self.startup, axis=axis, title=title,
                                     instructions='Проверить вручную', success_criterion='Ответ записан')
        with patch('founder.services.bruno_estimates.complete_text', return_value=json.dumps(self.answer())) as ai, \
             patch('founder.services.economics.complete_text', side_effect=AssertionError('No hidden AI call')):
            self.assertEqual(generate_estimates(self.startup), 4)
        self.assertEqual(ai.call_count, 1)
        self.assertGreaterEqual(ai.call_args.kwargs['max_tokens'], 2400)
        self.assertEqual(TaskEstimate.objects.count(), 4)
        self.assertTrue(all(resolve_estimate(saved).rice is not None for saved in TaskEstimate.objects.all()))

    def test_edit_during_request_rejects_stale_proposals_and_keeps_founder_change(self):
        answer = self.answer()

        def model_reply(*args, **kwargs):
            FeatureCandidate.objects.filter(pk=self.feature.pk).update(description='Новый сценарий')
            return json.dumps(answer)

        with patch('founder.services.bruno_estimates.complete_text', side_effect=model_reply), \
             self.assertRaises(AIServiceError):
            generate_estimates(self.startup)
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.description, 'Новый сценарий')
        self.assertEqual(self.feature.bruno_estimate, {})
        self.assertFalse(TaskEstimate.objects.exists())

    def test_real_review_supplies_estimates_to_new_steps_and_accepted_features_in_one_call(self):
        item = {'title': 'Каталог', 'description': 'Найти инструмент',
                'question_present': 'Если каталог есть?', 'question_absent': 'Если каталога нет?'}
        raw = model_review(features=[item], estimates=model_estimates_for(self.startup))
        with patch('founder.services.review.complete_text', return_value=raw) as ai:
            response = self.client.post(self.url('review_generate'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ai.call_count, 1)
        new_tasks = self.startup.bruno_tasks.exclude(pk=self.task.pk)
        self.assertEqual(new_tasks.count(), 2)  # Existing market task remains in place.
        for task in new_tasks:
            self.assertTrue(resolve_estimate(TaskEstimate.objects.get(task=task)).preliminary)
        with patch('founder.services.feature_ai.complete_text', side_effect=AssertionError('Accept is free')):
            self.client.post(self.url('features_suggest'), {'review': ProjectReview.objects.get().pk})
        accepted = FeatureCandidate.objects.get(title='Каталог')
        self.assertIsNone(accepted.reach)
        self.assertTrue(resolve_estimate(accepted).preliminary)
        self.assertFalse(KanoResponse.objects.exists())

    def test_endpoint_is_scoped_post_only_and_releases_ai_lease_on_failure(self):
        url = self.url('estimates_generate')
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch('founder.services.bruno_estimates.complete_text', side_effect=AIServiceError('Провайдер не ответил')) as ai:
            response = self.client.post(url, {'method': 'ice'})
        self.assertRedirects(response, self.url('tasks') + '?method=ice#task-priorities')
        self.assertEqual(ai.call_count, 1)
        self.assertFalse(TaskEstimate.objects.exists())
        from django.utils import timezone
        self.assertLessEqual(AIRequestLease.objects.get(user=self.owner).expires_at, timezone.now())
        viewer = User.objects.create_user('bruno-estimate-viewer', email='bruno-viewer@example.test')
        ProjectMember.objects.create(startup=self.startup, user=viewer, status='active', role='viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_changed_task_discards_outdated_ai_numbers_and_never_falls_back_to_constants(self):
        with patch('founder.services.bruno_estimates.complete_text', return_value=json.dumps(self.answer())):
            generate_estimates(self.startup)
        self.task.instructions = 'Провести большое исследование в другом сегменте'
        self.task.save(update_fields=['instructions'])
        saved = TaskEstimate.objects.get(task=self.task)
        self.assertIsNone(resolve_estimate(saved).rice)
        self.assertTrue(resolve_estimate(saved).needs_bruno)
        self.assertEqual(len(collect_targets(self.startup)), 1)
        self.assertEqual(parse_estimate(model_estimate(confidence=0))['confidence'], 0)

    @override_settings(AI_PROVIDER='demo')
    def test_demo_never_labels_fixed_numbers_as_bruno(self):
        with self.assertRaises(AIServiceError):
            generate_estimates(self.startup)
        page = self.client.get(self.url('tasks'))
        self.assertNotContains(page, 'Оценка Бруно ·')
        self.assertTrue(all(row['score'] is None for row in page.context['priority_entries']))

"""Shared task ranking and safe, editable handoff to the lab."""
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import (BrunoTask, FeatureCandidate, LabGenerationJob, LabSiteVersion,
                            ProjectMember, TaskEstimate, StartupProfile, User)
from founder.services.review import context_digest
from founder.services.work_priorities import lab_prompt


@override_settings(AI_PROVIDER='demo', LAB_RAW_AI_TEST_MODE=True, KANO_SURVEYS_ENABLED=True)
class WorkPrioritiesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='priorities-owner', email='priorities@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Аренда инструментов', problem='Нужен инструмент')
        self.client.force_login(self.owner)
        self.market = BrunoTask.objects.create(startup=self.startup, axis='market', title='Опросить клиентов',
            instructions='Поговорите с пятью клиентами.', success_criterion='Записаны ответы и отказы.')
        self.product = BrunoTask.objects.create(startup=self.startup, axis='product', origin='mvp', title='Каталог',
            instructions='Реализуйте каталог с поиском.', success_criterion='Пользователь находит нужный инструмент.')
        self.feature = FeatureCandidate.objects.create(startup=self.startup, task=self.product, title='Каталог',
            description='Поиск подходящего инструмента.', reach=100, rice_impact=2, confidence=80, effort=4,
            ice_impact=2, ease=2)
        self.estimates = {'reach':200, 'rice_impact':'1', 'confidence':'80', 'effort':'2',
                          'ice_impact':'8', 'ease':'6', 'estimate_basis':'Пять интервью', 'priority':''}

    def url(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    def propose(self, subject, **values):
        from founder.services.bruno_estimates import save_subject_estimate
        from founder.test_review import model_estimate
        save_subject_estimate(subject, model_estimate(**values), 'test-bruno-model')

    def test_all_tasks_are_rankable_without_duplicating_linked_mvp_features(self):
        for axis in ('finance', 'team', 'pitch'):
            BrunoTask.objects.create(startup=self.startup, axis=axis, title=axis)
        BrunoTask.objects.create(startup=self.startup, axis='product', origin='kano', title='Опрос Кано')
        FeatureCandidate.objects.create(startup=self.startup, title='Новая функция')
        page = self.client.get(self.url('tasks'))
        entries = page.context['priority_entries']
        self.assertEqual(len(entries), 7)
        self.assertEqual(sum(row['feature'] == self.feature for row in entries), 1)
        self.assertEqual(sum(row['task'] is not None for row in entries), 6)
        self.assertContains(page, 'Все задания Бруно и функции MVP')
        self.assertContains(page, 'Можно выполнить в лаборатории')
        self.assertFalse(next(row for row in entries if row['task'] == self.market)['lab_url'])

    def test_editing_regular_task_saves_validated_inputs_and_updates_rank_and_context(self):
        before = context_digest(self.startup)
        self.assertRedirects(self.client.post(self.url('task_estimate', self.market.pk), self.estimates),
            self.url('tasks') + f'?method=rice#priority-task-{self.market.pk}')
        self.assertNotEqual(before, context_digest(self.startup))
        page = self.client.get(self.url('tasks'))
        row = page.context['priority_entries'][0]
        self.assertEqual(row['task'], self.market)
        self.assertEqual((row['rice'], row['ice']), (Decimal(80), Decimal(384)))
        self.client.post(self.url('task_estimate', self.market.pk), {**self.estimates, 'reach':1, 'effort':10})
        page = self.client.get(self.url('tasks'))
        self.assertEqual(page.context['priority_entries'][0]['task'], self.product)
        page = self.client.get(self.url('tasks'), {'method':'ice'})
        self.assertEqual(page.context['priority_entries'][0]['task'], self.market)
        self.assertEqual(TaskEstimate.objects.count(), 1)
        self.feature.refresh_from_db()
        self.assertEqual(self.feature.reach, 100)

    def test_invalid_numbers_do_not_replace_saved_estimates(self):
        self.client.post(self.url('task_estimate', self.market.pk), self.estimates)
        for overrides in ({'effort':0}, {'rice_impact':4}, {'confidence':101}, {'ease':11}):
            page = self.client.post(self.url('task_estimate', self.market.pk), {**self.estimates, **overrides})
            self.assertEqual(page.status_code, 400)
        self.assertEqual(TaskEstimate.objects.get().effort, Decimal(2))
        self.client.post(self.url('task_estimate', self.market.pk), {'reach':0, 'confidence':0})
        self.propose(self.market)
        page = self.client.get(self.url('tasks'))
        row = next(row for row in page.context['priority_entries'] if row['task'] == self.market)
        self.assertEqual(row['rice'], 0)  # A known zero is never replaced by a positive assumption.
        self.assertTrue(row['preview'].rice_preliminary)
        self.assertIsNone(TaskEstimate.objects.get().effort)

    def test_saved_bruno_estimates_rank_all_tasks_without_ai_on_page_views(self):
        for axis in ('finance', 'team', 'pitch'):
            self.propose(BrunoTask.objects.create(startup=self.startup, axis=axis, title=axis))
        survey = BrunoTask.objects.create(startup=self.startup, axis='product', origin='kano', title='Кано')
        unstarted = FeatureCandidate.objects.create(startup=self.startup, title='Новая функция')
        for subject in (self.market, survey, unstarted):
            self.propose(subject)
        before = context_digest(self.startup)
        with patch('founder.services.ai.complete_text', side_effect=AssertionError('Ranking is local')):
            pages = [self.client.get(self.url('tasks'), {'method': method}) for method in ('rice', 'ice', 'rice')]
        for page in pages:
            entries = page.context['priority_entries']
            self.assertEqual(page.context['priority_rated_count'], len(entries))
            self.assertEqual(page.context['priority_saved_count'], 1)
            self.assertEqual(page.context['priority_preliminary_count'], 6)
            self.assertTrue(all(row['rice'] is not None and row['ice'] is not None for row in entries))
            self.assertEqual([r['score'] for r in entries], sorted((r['score'] for r in entries), reverse=True))
            self.assertTrue(next(row for row in entries if row['task'] == survey)['score_preliminary'])
            self.assertContains(page, 'Оценка Бруно')
            self.assertContains(page, 'Уточнить')
        self.assertEqual([(r['key'], r['rice'], r['ice']) for r in pages[0].context['priority_entries']],
                         [(r['key'], r['rice'], r['ice']) for r in pages[2].context['priority_entries']])
        self.assertEqual(context_digest(self.startup), before)
        self.assertEqual(TaskEstimate.objects.count(), 5)
        self.assertFalse(TaskEstimate.objects.exclude(reach__isnull=True).exists())
        unstarted.refresh_from_db()
        self.assertIsNone(unstarted.reach)
        self.assertIsNone(unstarted.confidence)
        self.assertEqual(unstarted.priority, '')
        self.assertFalse(LabGenerationJob.objects.exists())

    def test_partial_scores_preserve_manual_inputs_and_priority_separately_for_each_method(self):
        TaskEstimate.objects.create(task=self.market, reach=37, rice_impact=2, confidence=80, effort=4,
                                    priority='wont', estimate_basis='Мои интервью')
        self.propose(self.market)
        page = self.client.get(self.url('tasks'))
        row = next(row for row in page.context['priority_entries'] if row['task'] == self.market)
        self.assertEqual(row['rice'], Decimal('14.8'))
        self.assertFalse(row['score_preliminary'])
        self.assertTrue(row['preview'].ice_preliminary)
        self.assertEqual(row['priority'], 'wont')
        self.assertEqual(row['estimate'].estimate_basis, 'Мои интервью')
        self.assertEqual(row['estimate'].reach, 37)
        self.assertEqual(row['estimate'].effort, 4)
        self.assertEqual(row['preview'].bruno_fields, ['ice_impact', 'ease'])
        page = self.client.get(self.url('tasks'), {'method': 'ice'})
        row = next(row for row in page.context['priority_entries'] if row['task'] == self.market)
        self.assertTrue(row['score_preliminary'])
        self.assertEqual(row['priority'], 'wont')
        self.assertIsNone(TaskEstimate.objects.get().ice_impact)

    def test_draft_editor_is_prefilled_and_becomes_saved_only_on_submit(self):
        self.propose(self.market)
        before = self.client.get(self.url('tasks'))
        row = next(row for row in before.context['priority_entries'] if row['task'] == self.market)
        page = self.client.get(self.url('task_estimate', self.market.pk))
        form = page.context['form']
        self.assertTrue(page.context['estimate_preview'].preliminary)
        self.assertIsNone(TaskEstimate.objects.get().reach)
        data = {key: form[key].value() or '' for key in form.fields}
        self.assertEqual(data['reach'], row['estimate'].reach)
        self.assertEqual(data['effort'], row['estimate'].effort)
        self.assertEqual(self.client.post(self.url('task_estimate', self.market.pk), data).status_code, 302)
        page = self.client.get(self.url('tasks'))
        saved = next(row for row in page.context['priority_entries'] if row['task'] == self.market)
        self.assertFalse(saved['preview'].preliminary)
        self.assertEqual((saved['rice'], saved['ice']), (row['rice'], row['ice']))
        self.assertEqual(TaskEstimate.objects.count(), 1)

    def test_feature_editor_uses_same_draft_without_auto_selecting_mvp_or_creating_tasks(self):
        feature = FeatureCandidate.objects.create(startup=self.startup, title='Отзывы', priority='wont', reach=0)
        self.propose(feature)
        page = self.client.get(self.url('tasks'))
        row = next(row for row in page.context['priority_entries'] if row['feature'] == feature)
        form_page = self.client.get(self.url('feature_edit', feature.pk))
        form = form_page.context['form']
        self.assertEqual(form['reach'].value(), 0)
        self.assertEqual(form['effort'].value(), row['estimate'].effort)
        self.assertEqual(row['rice'], 0)
        self.assertEqual(row['priority'], 'wont')
        self.assertFalse(row['in_mvp'])
        feature.refresh_from_db()
        self.assertIsNone(feature.effort)
        self.assertIsNone(feature.task_id)
        self.assertEqual(self.startup.bruno_tasks.count(), 2)

    def test_focus_filters_and_other_projects_do_not_reinvent_saved_bruno_estimates(self):
        finance = BrunoTask.objects.create(startup=self.startup, axis='finance', title='Посчитать затраты')
        self.propose(finance, reach=3)
        self.propose(self.market, reach=17)
        self.market.assignee = self.owner
        self.market.save(update_fields=['assignee'])
        other = StartupProfile.objects.create(owner=self.owner, name='Отдельный проект', work_focus='revenue')
        FeatureCandidate.objects.create(startup=other, title='Другой охват', reach=999999)
        full = self.client.get(self.url('tasks'))
        mine = self.client.get(self.url('tasks'), {'mine': 1})
        market = next(row for row in full.context['priority_entries'] if row['task'] == self.market)
        self.assertEqual(market['score'], mine.context['priority_entries'][0]['score'])
        self.assertEqual(market['estimate'].reach, 17)
        self.startup.work_focus = 'revenue'
        self.startup.save(update_fields=['work_focus'])
        page = self.client.get(self.url('tasks'))
        same_market = next(row for row in page.context['priority_entries'] if row['task'] == self.market)
        self.assertEqual(same_market['score'], market['score'])
        manual = next(row for row in page.context['priority_entries'] if row['task'] == self.product)
        self.assertEqual((manual['rice'], manual['ice']), (Decimal(40), Decimal(32)))

    @override_settings(AI_PROVIDER='gigachat')
    def test_next_review_receives_bruno_provenance_and_preserves_manual_numbers(self):
        from founder.services.review import create_review
        from founder.test_review import model_review, model_estimates_for
        self.propose(self.market)
        with patch('founder.services.review.complete_text', return_value=model_review(
                estimates=model_estimates_for(self.startup))) as ai:
            create_review(self.startup)
        prompt, context = ai.call_args.args
        self.assertIn('bruno_fields', prompt)
        self.assertIn('не факты', context)
        self.assertIn('"bruno_fields": ["reach"', context)
        self.assertEqual(ai.call_count, 1)
        self.assertIsNone(TaskEstimate.objects.get().reach)

    def test_mvp_task_has_one_canonical_estimate_editor(self):
        self.assertRedirects(self.client.get(self.url('task_estimate', self.product.pk)),
            self.url('feature_edit', self.feature.pk) + '?method=rice')
        self.assertFalse(TaskEstimate.objects.exists())

    def test_lab_handoff_prefills_prompt_and_keeps_source_without_generating(self):
        source = LabSiteVersion.objects.create(startup=self.startup, kind='static', prompt='Первый сайт',
            html='<!doctype html><title>Аренда</title><h1>Каталог</h1>', model='demo')
        with patch('founder.services.ai.complete_text', side_effect=AssertionError('Handoff is local')), \
             patch('founder.lab_views.generate_site', side_effect=AssertionError('No generation')):
            page = self.client.get(self.url('lab'), {'task':self.product.pk})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['selected'], source)
        form = page.context['form']
        prompt = form['prompt'].value()
        self.assertIn(self.product.title, prompt)
        self.assertIn(self.product.success_criterion, prompt)
        self.assertIn('Сохрани работающие функции', prompt)
        self.assertLessEqual(len(prompt), 2000)
        self.assertEqual(form['message_mode'].value(), 'edit')
        self.assertEqual(form['kind'].value(), 'static')
        self.assertContains(page, 'Промпт готов.')
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, 'todo')
        self.assertEqual(LabSiteVersion.objects.count(), 1)
        self.assertFalse(LabGenerationJob.objects.exists())

    def test_unstarted_feature_can_open_lab_and_prompt_is_bounded(self):
        feature = FeatureCandidate.objects.create(startup=self.startup, title='Фильтры', description='Описание ' * 70)
        page = self.client.get(self.url('lab'), {'feature':feature.pk})
        self.assertEqual(page.status_code, 200)
        self.assertIn('Создай первую', page.context['form']['prompt'].value())
        self.product.instructions = 'Требование ' * 120
        self.product.success_criterion = 'Критерий ' * 100
        self.startup.one_line_pitch = 'Идея ' * 200
        self.assertLessEqual(len(lab_prompt(self.startup, task=self.product, feature=feature)), 2000)
        self.assertFalse(LabGenerationJob.objects.exists())

    def test_foreign_ids_viewers_and_non_build_tasks_cannot_use_handoff_or_edit(self):
        other = User.objects.create_user(username='priorities-other', email='other-priorities@example.test')
        foreign = StartupProfile.objects.create(owner=other, name='Чужой проект')
        task = BrunoTask.objects.create(startup=foreign, axis='product', origin='mvp', title='Секрет')
        feature = FeatureCandidate.objects.create(startup=foreign, title='Чужая функция')
        for query in ({'task':task.pk}, {'feature':feature.pk}, {'task':self.market.pk}, {'task':'bad'},
                      {'task':uuid4()}, {'task':self.product.pk,'feature':self.feature.pk}):
            self.assertEqual(self.client.get(self.url('lab'), query).status_code, 404)
        self.assertEqual(self.client.get(self.url('task_estimate', task.pk)).status_code, 404)
        ProjectMember.objects.create(startup=self.startup, user=other, status='active', role='viewer')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url('tasks')).status_code, 200)
        self.assertEqual(self.client.get(self.url('task_estimate', self.market.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url('task_estimate', self.market.pk), self.estimates).status_code, 404)
        self.assertEqual(self.client.get(self.url('lab'), {'task':self.product.pk}).status_code, 404)

    def test_completed_or_skipped_tasks_cannot_start_lab_work(self):
        for status in ('done', 'skipped'):
            self.product.status = status
            self.product.save(update_fields=['status'])
            self.assertEqual(self.client.get(self.url('lab'), {'task':self.product.pk}).status_code, 404)
            self.assertEqual(self.client.get(self.url('lab'), {'feature':self.feature.pk}).status_code, 404)

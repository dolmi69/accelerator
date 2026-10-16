from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.forms import EvidenceForm
from founder.models import (
    EvidenceEntry, LabSiteVersion, LabTestSession, ProjectMember, ProjectPicture,
    StartupMetrics, StartupProfile, User,
)
from founder.services.readiness import readiness_context


@override_settings(AI_PROVIDER='demo')
class ReadinessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='readiness-owner', email='readiness-owner@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Тихое место')
        self.outsiders = [User.objects.create_user(username=f'readiness-test-{i}', email=f'readiness-{i}@example.test') for i in range(3)]
        self.client.force_login(self.owner)

    def prototype(self, startup=None, **kwargs):
        return LabSiteVersion.objects.create(
            startup=startup or self.startup, prompt='Прототип', model='local',
            html='<html><body>Рабочий прототип</body></html>', **kwargs,
        )

    def evidence(self, milestone='demand', **kwargs):
        defaults = dict(startup=self.startup, axis='market', claim='Результат проверки',
                        observation='Провели проверку с клиентами.', source='Заметки встречи',
                        outcome='supported', milestone=milestone)
        defaults.update(kwargs)
        return EvidenceEntry.objects.create(**defaults)

    def states(self):
        return {item['key']: item for item in readiness_context(self.startup)['states']}

    def test_states_are_independent_and_link_to_sources(self):
        self.assertEqual(self.states()['demand']['status'], 'unknown')
        self.prototype()
        state = readiness_context(self.startup)
        self.assertEqual(self.states()['product']['status'], 'prototype')
        self.assertEqual(self.states()['demand']['status'], 'unknown')
        self.assertEqual(state['focus'], 'demand')
        record = self.evidence('payment')
        revenue = self.states()['revenue']
        self.assertEqual(revenue['label'], 'Записана первая оплата')
        self.assertIn(str(record.pk), revenue['sources'][0]['url'])
        self.assertEqual(self.states()['demand']['status'], 'unknown')
        self.assertFalse(StartupMetrics.objects.exists())

    def test_negative_and_conflicting_evidence_is_not_hidden(self):
        record = self.evidence(outcome='refuted')
        self.assertEqual(self.states()['demand']['status'], 'refuted')
        self.evidence()
        state = self.states()['demand']
        self.assertEqual(state['status'], 'mixed')
        self.assertEqual(len(state['sources']), 2)
        self.assertTrue(any(str(record.pk) in link['url'] for link in state['sources']))
        self.assertEqual(readiness_context(self.startup)['focus'], 'demand')

    def test_unsourced_future_untyped_and_unclear_are_not_positive(self):
        self.evidence('payment', outcome='unclear')
        self.evidence('pilot', source='   ')
        self.evidence('demand', observed_on=timezone.localdate() + timedelta(days=1))
        self.evidence('', observation='Сто успешных оплаченных пилотов')
        states = self.states()
        self.assertEqual(states['product']['status'], 'unknown')
        self.assertEqual(states['demand']['status'], 'unknown')
        self.assertEqual(states['revenue']['status'], 'unclear')

    def test_returning_users_do_not_imply_payment(self):
        self.evidence('retention')
        state = self.states()['revenue']
        self.assertEqual(state['label'], 'Записаны возвраты пользователей')
        self.assertNotIn('оплат', state['label'])

    def test_distinct_finished_external_testers_with_feedback_only(self):
        version = self.prototype()
        active, former = self.outsiders[:2]
        ProjectMember.objects.create(startup=self.startup, user=active, status='active')
        ProjectMember.objects.create(startup=self.startup, user=former, status='invited', joined_at=timezone.now())
        for tester in (self.owner, active, former):
            LabTestSession.objects.create(version=version, tester=tester, finished_at=timezone.now(),
                                          duration_seconds=30, feedback='Отзыв команды')
        outsider = self.outsiders[2]
        LabTestSession.objects.create(version=version, tester=outsider, feedback='Незавершённый тест')
        LabTestSession.objects.create(version=version, tester=outsider, finished_at=timezone.now(),
                                      duration_seconds=30, rating=5, feedback='  ')
        self.assertEqual(self.states()['product']['status'], 'prototype')
        for _ in range(3):
            LabTestSession.objects.create(version=version, tester=outsider, finished_at=timezone.now(),
                                          duration_seconds=30, feedback='Внешний тест')
        state = readiness_context(self.startup)
        self.assertEqual(state['testers'], 1)
        self.assertEqual(self.states()['product']['status'], 'feedback')
        self.assertFalse(self.states()['product']['supported'])

    def test_foreign_project_records_and_empty_prototypes_are_ignored(self):
        foreign = StartupProfile.objects.create(owner=self.outsiders[0], name='Чужой проект')
        version = self.prototype(foreign)
        self.evidence('repeat_payments', startup=foreign)
        for tester in self.outsiders[1:]:
            LabTestSession.objects.create(version=version, tester=tester, finished_at=timezone.now(),
                                          duration_seconds=30, feedback='Отзыв')
        LabSiteVersion.objects.create(startup=self.startup, prompt='пусто', model='local', html='  ')
        self.assertFalse(readiness_context(self.startup)['prototype'])

    def test_model_picture_can_describe_concept_but_cannot_invent_business_progress(self):
        ProjectPicture.objects.create(startup=self.startup, facts={
            key: {'text': 'Сохранённое описание', 'status': 'guess'} for key in ('problem', 'solution', 'customer')
        })
        self.assertTrue(readiness_context(self.startup)['concept'])
        self.evidence('repeat_payments')
        self.assertEqual(self.states()['demand']['status'], 'unknown')
        self.assertEqual(self.states()['revenue']['status'], 'signal')

    def test_view_is_read_only_has_five_axes_and_viewer_cannot_write_evidence(self):
        self.prototype()
        viewer = self.outsiders[0]
        ProjectMember.objects.create(startup=self.startup, user=viewer, status='active', role='viewer')
        with patch('founder.services.ai.complete_text', side_effect=AssertionError('No AI on page view')):
            page = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertTrue(page.context['readiness']['prototype'])
        self.assertContains(page, 'Коротко о состоянии проекта')
        self.assertContains(page, 'Есть прототип')
        self.assertContains(page, reverse('tasks', args=[self.startup.pk]))
        self.assertEqual(len(page.context['radar_rows']), 5)
        self.client.force_login(viewer)
        page = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertContains(page, 'Есть прототип')
        self.assertNotContains(page, reverse('evidence_create', args=[self.startup.pk]))
        self.assertEqual(self.client.post(reverse('evidence_create', args=[self.startup.pk])).status_code, 404)
        self.client.force_login(self.outsiders[1])
        self.assertEqual(self.client.get(reverse('dashboard', args=[self.startup.pk])).status_code, 404)

    def test_diary_milestone_requires_source_and_updates_without_ai(self):
        data = dict(axis='market', claim='Пилот', observation='Пилот завершён.',
                    observed_on=str(timezone.localdate()), outcome='supported', milestone='pilot')
        url = reverse('evidence_create', args=[self.startup.pk])
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EvidenceEntry.objects.exists())
        data['source'] = 'Отчёт пилота за октябрь'
        self.prototype()
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        record = EvidenceEntry.objects.get()
        self.assertEqual(record.milestone, 'pilot')
        self.assertTrue(self.states()['product']['supported'])
        data['outcome'] = 'refuted'
        self.client.post(reverse('evidence_edit', args=[self.startup.pk, record.pk]), data)
        self.assertEqual(self.states()['product']['status'], 'refuted')
        form = EvidenceForm(startup=self.startup, instance=record)
        self.assertFalse(form.fields['milestone'].required)

    def test_next_step_prefills_milestone_and_invalid_values_are_ignored(self):
        url = reverse('evidence_create', args=[self.startup.pk])
        page = self.client.get(url, {'milestone': 'payment'})
        self.assertEqual(page.context['form'].initial['milestone'], 'payment')
        self.assertEqual(page.context['form'].initial['axis'], 'finance')
        page = self.client.get(url, {'milestone': 'invented'})
        self.assertNotIn('milestone', page.context['form'].initial)

    def test_small_fixed_queries_do_not_fetch_prototype_html(self):
        self.prototype()
        with self.assertNumQueries(4) as queries:
            readiness_context(self.startup)
        for query in queries.captured_queries:
            if 'founder_labsiteversion' in query['sql']:
                selected_columns = query['sql'].split(' FROM ')[0]
                self.assertNotIn('"html"', selected_columns)

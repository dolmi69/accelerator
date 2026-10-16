"""History provenance, result conversations, snapshots and safe completion."""
import json
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase, TransactionTestCase, override_settings, RequestFactory
from django.urls import reverse
from founder.models import (User, StartupProfile, BrunoTask, EvidenceEntry, EvidenceConversation,
    EvidenceRevision, LabSiteVersion, ProjectMember, FeatureCandidate, LabGenerationJob, CoinTransaction)
from founder.services.ai import AIServiceError
from founder.test_review import model_estimate
from founder.services.project_history import record_lab_result, task_digest

HTML = '<!doctype html><html><head><title>Test</title></head><body><h1>Old</h1></body></html>'


def answer(**overrides):
    return {'reply':'Проверьте черновик.', 'question':'', 'ready':True, 'claim':'Проверили интерес',
        'observation':'Поговорили с пятью людьми. Двое заинтересовались.',
        'learned':'Есть интерес, готовность платить ещё не проверяли.',
        'next_step':'Уточнить готовность принять участие в пилоте', 'source':'Рассказ основателя',
        'outcome':'unclear', 'axis':'market', 'next_estimate':model_estimate(), **overrides}


@override_settings(AI_PROVIDER='gigachat')
class ProjectHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('history-owner', email='history@example.test')
        self.other = User.objects.create_user('history-other', email='history-other@example.test')
        self.project = StartupProfile.objects.create(owner=self.user, name='Аренда')
        self.foreign = StartupProfile.objects.create(owner=self.other, name='Private')
        self.task = BrunoTask.objects.create(startup=self.project, axis='market', title='Поговорить с людьми',
            instructions='Узнайте, нужна ли аренда', success_criterion='Записаны ответы')
        self.client.force_login(self.user)

    def url(self, name, *args):
        return reverse(name, args=[self.project.pk, *args])

    def talk(self, data=None, **reply):
        with patch('founder.services.result_conversation.complete_text', return_value=json.dumps(answer(**reply))):
            return self.client.post(self.url('result_tell'), data or {
                'task':self.task.pk, 'message':'Поговорил с пятью, двое заинтересовались.', 'request_key':uuid4()})

    def confirm(self, draft):
        return self.client.post(self.url('result_tell'), {'draft':draft.pk, 'revision':draft.revision, 'action':'confirm'})

    def lab_request(self, task=None, **extra):
        request = RequestFactory().post('/', {'task_context':str(task.pk), '_task_digest':task_digest(task), **extra} if task else extra)
        request.user = self.user
        return request

    def version(self, **values):
        return LabSiteVersion.objects.create(startup=self.project, html=HTML, model='real-model', prompt='Создай сайт',
            bruno_report={'title':'Готово','items':['Создан каталог'], 'checks':{'runtime_verified':True}}, **values)

    def test_draft_requires_confirmation_and_duplicates_do_not_repeat_ai_or_rewards(self):
        key = uuid4()
        data = {'task':self.task.pk, 'message':'Поговорил с пятью, двое заинтересовались.', 'request_key':key}
        self.talk(data)
        draft = EvidenceConversation.objects.get()
        self.assertFalse(EvidenceEntry.objects.exists())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'todo')
        with patch('founder.services.result_conversation.complete_text', side_effect=AssertionError('No repeat')):
            self.client.post(self.url('result_tell'), data)
        self.confirm(draft)
        self.confirm(draft)
        self.assertEqual(EvidenceEntry.objects.count(), 1)
        entry = EvidenceEntry.objects.get()
        self.assertEqual(entry.author, self.user)
        self.assertEqual(entry.progress['verification'], 'reported')
        self.assertEqual(entry.outcome, 'unclear')
        self.assertEqual(entry.revisions.count(), 1)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'done')
        self.assertEqual(CoinTransaction.objects.filter(kind='task').count(), 1)

    def test_record_feature_result_reuses_one_task_and_confirmation_saves_linked_evidence(self):
        feature = FeatureCandidate.objects.create(startup=self.project, title='Каталог', description='Выбор инструмента')
        url = self.url('feature_result', feature.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch('founder.services.result_conversation.draft_reply', side_effect=AssertionError('Opening is free')):
            response = self.client.post(url)
            feature.refresh_from_db()
            task = feature.task
            self.assertRedirects(response, self.url('result_tell') + f'?task={task.pk}')
            self.assertEqual(self.client.post(url).url, response.url)
        self.assertEqual(self.project.bruno_tasks.filter(origin='mvp').count(), 1)
        self.assertFalse(EvidenceEntry.objects.exists())
        self.assertEqual(task.status, 'todo')
        self.talk({'task': task.pk, 'message': 'Сделали каталог и проверили выбор инструмента.',
                   'request_key': uuid4()}, axis='product', claim='Каталог проверен')
        draft = EvidenceConversation.objects.get(task=task)
        self.confirm(draft)
        entry = EvidenceEntry.objects.get()
        self.assertEqual(entry.task_id, task.pk)
        self.assertEqual(entry.claim, 'Каталог проверен')
        self.assertEqual(entry.revisions.count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertContains(self.client.get(self.url('evidence_list')), 'Каталог проверен')

    def test_record_feature_result_rejects_foreign_feature_and_viewer(self):
        foreign = FeatureCandidate.objects.create(startup=self.foreign, title='Private')
        self.assertEqual(self.client.post(self.url('feature_result', foreign.pk)).status_code, 404)
        feature = FeatureCandidate.objects.create(startup=self.project, title='Каталог')
        ProjectMember.objects.create(startup=self.project, user=self.other, role='viewer', status='active')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url('feature_result', feature.pk)).status_code, 404)
        feature.refresh_from_db()
        self.assertIsNone(feature.task_id)

    def test_clarification_conversation_and_stale_revision(self):
        self.talk(ready=False, question='Они согласились на пилот или только заинтересовались?')
        draft = EvidenceConversation.objects.get()
        self.assertEqual(self.confirm(draft).status_code, 302)
        self.assertFalse(EvidenceEntry.objects.exists())
        self.talk({'draft':draft.pk, 'message':'Только заинтересовались, участия не обещали.', 'request_key':uuid4()})
        old_revision = draft.revision
        draft.refresh_from_db()
        self.assertEqual(len(draft.messages), 4)
        self.client.post(self.url('result_tell'), {'draft':draft.pk, 'revision':old_revision, 'action':'confirm'})
        self.assertFalse(EvidenceEntry.objects.exists())
        self.confirm(draft)
        self.assertEqual(EvidenceEntry.objects.count(), 1)

    def test_failure_retains_story_and_reading_or_refresh_never_calls_ai(self):
        with patch('founder.services.result_conversation.complete_text', side_effect=AIServiceError('Unavailable')):
            self.client.post(self.url('result_tell'), {'message':'Реальный рассказ', 'request_key':uuid4()})
        draft = EvidenceConversation.objects.get()
        self.assertEqual(draft.messages[0]['text'], 'Реальный рассказ')
        self.assertFalse(draft.processing)
        with patch('founder.services.result_conversation.complete_text', side_effect=AssertionError('GET is free')):
            self.assertContains(self.client.get(self.url('result_tell')+'?draft='+str(draft.pk)), 'Реальный рассказ')
            self.assertContains(self.client.get(self.url('evidence_list')), 'Незавершённые разговоры')
        self.assertFalse(EvidenceEntry.objects.exists())

    def test_private_drafts_foreign_tasks_and_viewers_are_scoped(self):
        self.talk()
        draft = EvidenceConversation.objects.get()
        private = EvidenceConversation.objects.create(startup=self.foreign, author=self.other)
        self.assertEqual(self.client.get(self.url('result_tell')+'?draft='+str(private.pk)).status_code, 404)
        foreign_task = BrunoTask.objects.create(startup=self.foreign, axis='market', title='Secret')
        self.assertEqual(self.client.get(self.url('result_tell')+'?task='+str(foreign_task.pk)).status_code, 404)
        ProjectMember.objects.create(startup=self.project, user=self.other, role='viewer', status='active')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('result_tell')).status_code, 404)
        self.assertEqual(self.confirm(draft).status_code, 404)
        page = self.client.get(self.url('evidence_list'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Расскажи Бруно')

    def test_lab_report_is_auto_saved_once_but_model_claim_is_not_a_runtime_check(self):
        task = BrunoTask.objects.create(startup=self.project, origin='mvp', axis='product', title='Каталог')
        version = self.version()
        request = self.lab_request(task)
        entry = record_lab_result(request, self.project, None, version)
        record_lab_result(request, self.project, None, version)
        self.assertEqual(EvidenceEntry.objects.count(), 1)
        self.assertEqual(entry.lab_version, version)
        self.assertEqual(entry.origin, 'lab')
        self.assertEqual(entry.progress['verification'], 'needs_check')
        task.refresh_from_db()
        self.assertEqual(task.status, 'todo')
        self.assertFalse(CoinTransaction.objects.filter(kind='task').exists())
        self.assertEqual(self.client.post(self.url('result_verify', entry.pk), {}).status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, 'todo')
        self.client.post(self.url('result_verify', entry.pk), {'confirmed':'1'})
        task.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertEqual(entry.progress['verification'], 'confirmed')
        self.assertEqual(entry.revisions.count(), 2)
        self.assertEqual(entry.revisions.last().snapshot['progress']['verification'], 'needs_check')

    def test_verified_exact_text_change_can_finish_automatically(self):
        task = BrunoTask.objects.create(startup=self.project, origin='mvp', axis='product', title='Надпись',
            instructions='Замени Old на New', success_criterion='Надпись New вместо Old')
        source = self.version()
        version = LabSiteVersion.objects.create(startup=self.project, source=source, html=HTML.replace('Old','New'),
            prompt='Замени Old на New', model='local-editor', edit_method='local')
        entry = record_lab_result(self.lab_request(task), self.project, source, version)
        self.assertEqual(entry.progress['verification'], 'verified')
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertIn('исходнике', entry.progress['checks'][0])

    def test_edited_task_cannot_be_closed_by_an_old_lab_result(self):
        task = BrunoTask.objects.create(startup=self.project, origin='mvp', axis='product', title='Каталог')
        request = self.lab_request(task)
        task.instructions = 'Дополнительно нужна регистрация'
        task.save()
        entry = record_lab_result(request, self.project, None, self.version())
        self.assertEqual(entry.progress['verification'], 'needs_check')
        self.assertIn('изменились', ' '.join(entry.progress['limitations']))
        # A current task snapshot is deliberately not enough to verify a stale job.
        response = self.client.post(self.url('result_verify', entry.pk), {'confirmed':'1'})
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, 'todo')

    def test_history_followup_is_explicit_scoped_and_idempotent(self):
        self.talk()
        draft = EvidenceConversation.objects.get()
        self.confirm(draft)
        entry = EvidenceEntry.objects.get()
        page = self.client.get(self.url('evidence_list'))
        self.assertContains(page, 'Есть интерес, готовность платить ещё не проверяли.')
        self.assertEqual(self.project.bruno_tasks.count(), 1)
        self.client.post(self.url('result_followup', entry.pk))
        self.client.post(self.url('result_followup', entry.pk))
        self.assertEqual(self.project.bruno_tasks.count(), 2)
        self.assertEqual(self.project.bruno_tasks.filter(origin='progress').count(), 1)
        self.assertTrue(self.project.bruno_tasks.get(origin='progress').estimate.bruno_estimate)
        self.assertFalse(EvidenceEntry.objects.filter(startup=self.foreign).exists())

    def test_legacy_edit_preserves_original_snapshot(self):
        entry = EvidenceEntry.objects.create(startup=self.project, axis='market', claim='Интервью', observation='Первый рассказ')
        self.client.post(self.url('evidence_edit', entry.pk), {'claim':'Интервью','axis':'market',
            'observation':'Уточнённый рассказ','observed_on':'2026-10-15','outcome':'unclear'})
        entry.refresh_from_db()
        self.assertEqual(entry.observation, 'Уточнённый рассказ')
        self.assertEqual(entry.revisions.count(), 2)
        self.assertEqual(entry.revisions.last().snapshot['observation'], 'Первый рассказ')

    @patch('founder.lab_job_views.launch')
    def test_feature_handoff_binds_a_task_before_queueing_and_is_idempotent(self, launch):
        feature = FeatureCandidate.objects.create(startup=self.project, title='Новый каталог')
        data = {'request_id':uuid4(), 'feature_context':feature.pk, 'prompt':'Создай каталог', 'kind':'static','message_mode':'edit'}
        first = self.client.post(self.url('lab_job_start'), data)
        self.assertEqual(first.status_code, 202)
        feature.refresh_from_db()
        self.assertIsNotNone(feature.task_id)
        job = LabGenerationJob.objects.get()
        self.assertEqual(job.payload['task_context'], [str(feature.task_id)])
        self.assertIn('_task_digest', job.payload)
        self.assertEqual(self.client.post(self.url('lab_job_start'), data).status_code, 200)
        self.assertEqual(self.project.bruno_tasks.filter(origin='mvp').count(), 1)
        data['request_id'] = uuid4()
        data.pop('feature_context')
        data['task_context'] = self.task.pk
        self.assertEqual(self.client.post(self.url('lab_job_start'), data).status_code, 404)


@override_settings(LAB_RAW_AI_TEST_MODE=True, LAB_UNRESTRICTED_TEST_MODE=True)
class HistoryBackgroundTests(TransactionTestCase):
    @patch('founder.lab_job_views.launch')
    def test_task_result_survives_disconnection_and_is_recorded_once(self, launch):
        from founder.services.lab_jobs import run
        from founder.services.qwen import CodeResult
        user = User.objects.create_user('history-job-owner', email='history-job@example.test')
        startup = StartupProfile.objects.create(owner=user, name='Background history')
        task = BrunoTask.objects.create(startup=startup, origin='mvp', axis='product', title='Каталог')
        self.client.force_login(user)
        response = self.client.post(reverse('lab_job_start', args=[startup.pk]), {
            'request_id':uuid4(), 'task_context':task.pk, 'prompt':'Создай каталог', 'kind':'static', 'message_mode':'edit'})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()['job']['id']
        self.client.logout()
        with patch('founder.lab_views._generate_design', return_value=CodeResult(HTML, 'test-model', 1, 2)), \
             patch('founder.lab_views.validate_result', return_value={}):
            run(job_id)
            run(job_id)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, 'succeeded', job.error)
        entry = EvidenceEntry.objects.get()
        self.assertEqual(entry.task_id, task.pk)
        self.assertEqual(entry.lab_version_id, job.version_id)
        self.assertEqual(entry.author_id, user.pk)
        self.assertEqual(entry.progress['verification'], 'needs_check')
        self.assertEqual(EvidenceRevision.objects.count(), 1)
        self.assertEqual(LabSiteVersion.objects.count(), 1)

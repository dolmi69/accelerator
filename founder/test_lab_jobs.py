"""Durable jobs: delivery, duplicate prevention, disconnects, failures and results."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
import os

from django.test import TransactionTestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from founder.models import LabGenerationJob, LabSiteVersion, StartupProfile, User, LabConversationTurn
from founder.services.lab_jobs import run, refresh
from founder.services.qwen import CodeResult, QwenOutputError

HTML = '<!doctype html><html><head><title>Test</title></head><body><h1>Result</h1></body></html>'


@override_settings(LAB_RAW_AI_TEST_MODE=True, LAB_UNRESTRICTED_TEST_MODE=True)
class LabJobTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='job-owner', email='jobs@example.test')
        self.other = User.objects.create_user(username='job-other', email='other-jobs@example.test')
        self.project = StartupProfile.objects.create(owner=self.user, name='Jobs')
        self.client.force_login(self.user)
        self.delivery = patch('founder.lab_job_views.launch').start()
        self.addCleanup(patch.stopall)

    def submit(self, **changes):
        data = {'request_id': str(uuid4()), 'prompt': 'Создай сайт', 'kind': 'static', 'message_mode': 'edit', **changes}
        return self.client.post(reverse('lab_job_start', args=[self.project.pk]), data)

    def test_enqueue_returns_before_any_generation_and_duplicate_is_same_job(self):
        identifier = str(uuid4())
        with patch('founder.lab_views.generate_site') as ai:
            first = self.submit(request_id=identifier)
            second = self.submit(request_id=identifier)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.json()['job']['id'], identifier)
        self.assertEqual(LabGenerationJob.objects.count(), 1)
        ai.assert_not_called()
        self.assertEqual(self.submit().status_code, 409)

    def test_worker_runs_without_original_browser_and_records_version_once(self):
        response = self.submit()
        job_id = response.json()['job']['id']
        # Original browser/session can disappear; the task only needs its DB row.
        self.client.logout()
        result = CodeResult(HTML, 'test', 1, 2)
        with patch('founder.lab_views._generate_design', return_value=result) as ai, patch('founder.lab_views.validate_result', return_value={}) :
            run(job_id)
            run(job_id)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, 'succeeded', job.error)
        self.assertIsNotNone(job.version_id)
        self.assertEqual(job.version.html, HTML)
        self.assertEqual(LabSiteVersion.objects.count(), 1)
        ai.assert_called_once()

    def test_failure_is_persistent_old_site_survives_and_no_automatic_retry(self):
        old = LabSiteVersion.objects.create(startup=self.project, html=HTML, model='old', prompt='old')
        job_id = self.submit(source_version=old.pk).json()['job']['id']
        with patch('founder.lab_views._generate_design', side_effect=QwenOutputError('Проверочная ошибка модели')) as ai:
            run(job_id)
            run(job_id)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, 'failed')
        self.assertIn('Проверочная ошибка', job.error)
        self.assertEqual(LabSiteVersion.objects.count(), 1)
        self.assertTrue(LabSiteVersion.objects.filter(pk=old.pk).exists())
        ai.assert_called_once()
        page = self.client.get(reverse('lab', args=[self.project.pk]))
        self.assertIn('Проверочная ошибка', page.context['lab_job_state']['error'])

    def test_reload_exposes_running_job_and_server_renders_forge(self):
        job_id = self.submit().json()['job']['id']
        page = self.client.get(reverse('lab', args=[self.project.pk]))
        self.assertContains(page, str(job_id))
        import re
        forge = re.search(r'<section[^>]+id="lab-forge"[^>]*>', page.content.decode(), re.S).group()
        self.assertNotIn('hidden', forge)
        status = self.client.get(reverse('lab_job_status', args=[self.project.pk])).json()['job']
        self.assertEqual(status['status'], 'queued')

    def test_private_status_submission_and_csrf_are_protected(self):
        self.submit()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('lab_job_status', args=[self.project.pk])).status_code, 404)
        self.assertEqual(self.submit().status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('lab_job_start', args=[self.project.pk]), {}).status_code, 403)

    def test_switching_projects_does_not_change_or_cancel_job(self):
        job_id = self.submit().json()['job']['id']
        other = StartupProfile.objects.create(owner=self.user, name='Other')
        self.client.get(reverse('lab', args=[other.pk]))
        self.assertEqual(LabGenerationJob.objects.get(pk=job_id).status, 'queued')
        self.assertEqual(self.client.get(reverse('lab_job_status', args=[self.project.pk])).json()['job']['id'], job_id)

    def test_discussion_is_durable_without_creating_site(self):
        job_id = self.submit(prompt='бро', message_mode='discuss').json()['job']['id']
        run(job_id)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, 'succeeded', job.error)
        self.assertIsNotNone(job.turn_id)
        self.assertEqual(LabConversationTurn.objects.count(), 1)
        self.assertEqual(LabSiteVersion.objects.count(), 0)

    def test_dead_worker_fails_visibly_without_rebilling_sleeping_live_worker_survives(self):
        job_id = self.submit().json()['job']['id']
        LabGenerationJob.objects.filter(pk=job_id).update(status='running', pid=os.getpid(), heartbeat_at=timezone.now()-timedelta(minutes=5))
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(refresh(job).status, 'running')
        with patch('founder.services.lab_jobs.os.kill', side_effect=ProcessLookupError):
            self.assertEqual(refresh(job).status, 'failed')
        self.assertIn('Автоматического платного повтора нет', job.error)

    @patch('founder.services.lab_jobs.subprocess.Popen')
    def test_delivery_is_throttled_without_losing_queued_job(self, process):
        from founder.services.lab_jobs import launch
        job_id = self.submit().json()['job']['id']
        launch(job_id)
        launch(job_id)
        process.assert_called_once()
        self.assertEqual(LabGenerationJob.objects.get(pk=job_id).status, 'queued')

    def test_dead_worker_releases_only_its_own_ai_lock(self):
        from founder.services.request_limits import acquire_ai_lease
        from founder.models import AIRequestLease
        job_id = self.submit().json()['job']['id']
        token = acquire_ai_lease(self.user.pk)
        LabGenerationJob.objects.filter(pk=job_id).update(status='running', pid=999999,
            heartbeat_at=timezone.now()-timedelta(minutes=5), lease_token=token)
        with patch('founder.services.lab_jobs.os.kill', side_effect=ProcessLookupError):
            refresh(LabGenerationJob.objects.get(pk=job_id))
        self.assertLessEqual(AIRequestLease.objects.get(user=self.user).expires_at, timezone.now())

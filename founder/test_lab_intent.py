"""Automatic intent must not mutate sites for questions or lose existing servers."""
import json
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from founder.models import User, StartupProfile, LabSiteVersion, LabGenerationJob, LabConversationTurn
from founder.services.lab_intent import decide
from founder.services.lab_jobs import run
from founder.services.qwen import CodeResult, QwenOutputError

HTML = '<!doctype html><html><head><title>Test</title></head><body><h1>Ready</h1></body></html>'


def decision(action='edit', kind='static'):
    return {'action': action, 'kind': kind, 'task': 'Измени цвет кнопки' if action == 'edit' else '',
            'answer': '' if action == 'edit' else 'Какую кнопку вы хотите изменить?', 'suggestion': ''}


def output(data):
    return CodeResult(json.dumps(data, ensure_ascii=False), 'intent-model', 10, 20)


class IntentContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='intent-unit')
        self.project = StartupProfile.objects.create(owner=self.user, name='Intent')

    def test_model_uses_selected_source_and_discussion_to_resolve_followup(self):
        source = LabSiteVersion.objects.create(startup=self.project, kind='generated', html=HTML,
            backend_files={'app.py': '# server'}, prompt='create', model='test')
        LabConversationTurn.objects.create(startup=self.project, user=self.user, source=source,
            prompt='Что улучшить?', reply={'answer': 'Зелёная кнопка', 'suggestion': 'Измени цвет кнопки'}, model='test')
        with patch('founder.services.lab_intent.generate_code', return_value=output(decision())) as ai:
            data, _ = decide(self.project, source, 'Да, сделай')
        self.assertEqual(data['kind'], 'generated')
        payload = json.loads(ai.call_args.args[1][0]['content'])
        self.assertIn('app.py', payload['current_source_excerpt'])
        self.assertEqual(payload['conversation'][0]['user'], 'Что улучшить?')

    def test_existing_django_server_is_preserved(self):
        source = LabSiteVersion.objects.create(startup=self.project, kind='django', html=HTML, prompt='create', model='test')
        with patch('founder.services.lab_intent.generate_code', return_value=output(decision())):
            data, _ = decide(self.project, source, 'Поменяй цвет')
        self.assertEqual(data['kind'], 'django')

    def test_file_plan_sees_frontend_even_when_server_is_large(self):
        source=LabSiteVersion.objects.create(startup=self.project,kind='generated',html=HTML,model='test',
            backend_files={'app.py':'# server\n' * 5000,'index.html':HTML,'assets/site.css':'button { color:red; }'})
        payload={**decision(),'complexity':'small','edit_files':['assets/site.css']}
        with patch('founder.services.lab_intent.generate_code',return_value=output(payload)) as api:
            data,_=decide(self.project,source,'Сделай кнопки синими')
        sent=json.loads(api.call_args.args[1][0]['content'])
        self.assertIn('button { color:red; }',sent['current_source_excerpt'])
        self.assertFalse(sent['source_is_complete'])
        self.assertEqual(len(sent['file_inventory']),3)
        self.assertEqual(data['edit_files'],['assets/site.css'])

    def test_malformed_decision_fails_without_guess_or_paid_retry(self):
        for data in ({'action': 'edit'}, decision(action='unknown'), {**decision(), 'task': ''}):
            with self.subTest(data=data), patch('founder.services.lab_intent.generate_code', return_value=output(data)) as ai:
                with self.assertRaises(QwenOutputError):
                    decide(self.project, None, 'Сделай сайт')
                ai.assert_called_once()

    def test_complexity_requires_large_work_explanation_and_uncertainty_is_standard(self):
        for value, expected in [('small','small'), ('standard','standard'), ('complex','standard'), (None,'standard'), ('uncertain','standard')]:
            payload = decision()
            if value is not None:
                payload['complexity'] = value
            with self.subTest(value=value), patch('founder.services.lab_intent.generate_code', return_value=output(payload)):
                data, _ = decide(self.project, None, 'Сделай сайт')
            self.assertEqual(data['complexity'], expected)
        payload = {**decision(), 'complexity':'complex', 'complexity_reason':'Новая архитектура базы и нескольких API'}
        with patch('founder.services.lab_intent.generate_code', return_value=output(payload)):
            data, _ = decide(self.project, None, 'Полностью перестрой приложение')
        self.assertEqual(data['complexity'], 'complex')


@override_settings(LAB_RAW_AI_TEST_MODE=True, LAB_UNRESTRICTED_TEST_MODE=True)
class AutomaticLabJobTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='intent-worker')
        self.project = StartupProfile.objects.create(owner=self.user, name='Intent')
        self.client.force_login(self.user)
        self.delivery = patch('founder.lab_job_views.launch').start()
        self.addCleanup(patch.stopall)

    def submit(self, **extra):
        payload = {'request_id': str(uuid4()), 'automatic': '1', 'prompt': 'Да, сделай', **extra}
        return self.client.post(reverse('lab_job_start', args=[self.project.pk]), payload)

    def test_question_and_clarification_are_saved_once_without_generation(self):
        for action in ('discuss', 'clarify'):
            with self.subTest(action=action):
                job_id = self.submit().json()['job']['id']
                data = decision(action)
                with patch('founder.services.lab_intent.decide', return_value=(data, output(data))) as planner, \
                     patch('founder.lab_views._generate_design') as generator, \
                     patch('founder.lab_views.discussion_reply') as second_answer:
                    run(job_id)
                    run(job_id)
                job = LabGenerationJob.objects.get(pk=job_id)
                self.assertEqual(job.status, 'succeeded', job.error)
                self.assertEqual(job.mode, 'discuss')
                self.assertIsNotNone(job.turn_id)
                self.assertIsNone(job.version_id)
                self.assertEqual(job.turn.reply['answer'], data['answer'])
                planner.assert_called_once()
                generator.assert_not_called()
                second_answer.assert_not_called()
        self.assertFalse(self.project.lab_versions.exists())

    def test_server_request_routes_to_backend_generator_even_if_client_posts_static(self):
        job_id = self.submit(kind='static').json()['job']['id']
        data = decision(kind='generated')
        with patch('founder.services.lab_intent.decide', return_value=(data, output(data))), \
             patch('founder.lab_views._generate_design', return_value=CodeResult(HTML, 'code-model', 1, 2, backend_files={'app.py':'# app'}, report={'summary':'Готово', 'completed':['Сервер'], 'not_done':[]})) as generator:
            run(job_id)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, 'succeeded', job.error)
        self.assertEqual(job.version.kind, 'generated')
        self.assertEqual(generator.call_args.args[4], 'generated')
        self.assertEqual(generator.call_args.args[8]['interpreted_request'], data['task'])

    def test_invalid_plan_never_reaches_generation_and_keeps_old_version(self):
        old = LabSiteVersion.objects.create(startup=self.project, kind='static', html=HTML, prompt='old', model='test')
        job_id = self.submit(source_version=old.pk).json()['job']['id']
        with patch('founder.services.lab_intent.decide', side_effect=QwenOutputError('Уточните запрос')), \
             patch('founder.lab_views._generate_design') as generator:
            run(job_id)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, 'failed')
        self.assertIn('Уточните запрос', job.error)
        generator.assert_not_called()
        self.assertEqual(self.project.lab_versions.count(), 1)

    @override_settings(LAB_UNRESTRICTED_TEST_MODE=False)
    def test_planning_and_generation_share_lease_and_count_one_request(self):
        from founder.services.request_limits import acquire_ai_lease
        job_id = self.submit().json()['job']['id']
        data = decision()
        with patch('founder.services.lab_intent.decide', return_value=(data, output(data))), \
             patch('founder.lab_views._generate_design', return_value=CodeResult(HTML, 'test', 1, 2)), \
             patch('founder.lab_views.validate_result', return_value={}), \
             patch('founder.lab_views._consume_lab_limits') as general_quota, \
             patch('founder.services.plans.quota', return_value=5), \
             patch('founder.lab_views.consume_limit') as personal_quota, \
             patch('founder.lab_views.acquire_ai_lease', wraps=acquire_ai_lease) as lease:
            run(job_id)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, 'succeeded', job.error)
        lease.assert_called_once()
        general_quota.assert_called_once_with(self.user)
        personal_quota.assert_called_once_with(f'lab-day:{self.user.pk}', 5, 86400)

    def test_composer_has_no_manual_action_or_output_selectors(self):
        response = self.client.get(reverse('lab', args=[self.project.pk]))
        self.assertContains(response, 'name="automatic" value="1"')
        self.assertNotContains(response, 'id="lab-message-mode"')
        self.assertNotContains(response, 'id="lab-site-kind"')

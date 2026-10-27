"""Offline routing, request isolation, correct billing and reload-safe status."""
import json
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from openai import OpenAI
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from founder.models import LabAIUsage, LabGenerationJob, LabSiteVersion, StartupProfile, User
from founder.services import lab_models
from founder.services.lab_jobs import describe, remember_model_route
from founder.services.qwen import CodeResult, QwenError, generate_code
from founder.test_lab_support import install_lab_mocks

HTML = '<!doctype html><html><head><title>Demo</title></head><body><h1>Demo</h1></body></html>'


@override_settings(LAB_CODE_PROVIDER='ofox', LAB_MODEL_ROUTING=True)
class ModelRouteTests(SimpleTestCase):
    def source(self, **extra):
        return SimpleNamespace(model=lab_models.SOL, edit_method='full', kind='static', source_id=None, **extra)

    def test_first_creation_and_scaffold_always_use_sol(self):
        self.assertEqual(lab_models.planning_route(None)['model'], lab_models.SOL)
        self.assertEqual(lab_models.generation_route(None, {'complexity': 'small'})['model'], lab_models.SOL)
        scaffold = SimpleNamespace(model='django-modules-v2', edit_method='local', kind='django', source_id=None)
        self.assertEqual(lab_models.planning_route(scaffold)['model'], lab_models.SOL)
        self.assertEqual(lab_models.generation_route(scaffold, {'complexity': 'small'})['model'], lab_models.SOL)

    def test_followup_small_uses_luna_but_ambiguity_rebuild_or_type_change_uses_sol(self):
        source = self.source()
        self.assertEqual(lab_models.planning_route(source)['model'], lab_models.LUNA)
        self.assertEqual(lab_models.generation_route(source, {'complexity': 'small'})['model'], lab_models.LUNA)
        for decision, options in [(None, {}), ({}, {}), ({'complexity':'complex'}, {}),
                                  ({'complexity':'small'}, {'rebuild':True}),
                                  ({'complexity':'small'}, {'kind':'generated'})]:
            with self.subTest(decision=decision, options=options):
                self.assertEqual(lab_models.generation_route(source, decision, **options)['model'], lab_models.SOL)

    @override_settings(OFOX_CODE_MODEL='anthropic/claude-sonnet-5.5')
    def test_nested_scopes_restore_model_even_on_error_without_mutating_settings(self):
        with lab_models.model_scope(lab_models.SOL):
            self.assertEqual(lab_models.active_model(), lab_models.SOL)
            with self.assertRaises(RuntimeError), lab_models.model_scope(lab_models.LUNA):
                self.assertEqual(lab_models.active_model(), lab_models.LUNA)
                raise RuntimeError('test')
            self.assertEqual(lab_models.active_model(), lab_models.SOL)
        self.assertEqual(lab_models.active_model(), 'anthropic/claude-sonnet-5.5')

    @override_settings(LAB_CODE_PROVIDER='cloudru')
    def test_other_provider_is_not_switched(self):
        self.assertIsNone(lab_models.planning_route(None))
        with lab_models.model_scope(lab_models.SOL):
            self.assertIsNone(lab_models.override_model())

    def test_backend_checkpoint_cannot_be_reused_for_a_different_model(self):
        from founder.services.backend_stages import _checkpoint_path
        checkpoint = (1, uuid4())
        with lab_models.model_scope(lab_models.SOL):
            sol = _checkpoint_path(checkpoint, 'Задача', None)
        with lab_models.model_scope(lab_models.LUNA):
            luna = _checkpoint_path(checkpoint, 'Задача', None)
        self.assertNotEqual(sol, luna)


@override_settings(LAB_CODE_PROVIDER='ofox', LAB_MODEL_ROUTING=True, LAB_RAW_AI_TEST_MODE=True,
                   LAB_UNRESTRICTED_TEST_MODE=True)
class LabModelFlowTests(TestCase):
    def setUp(self):
        install_lab_mocks(self)
        self.user = User.objects.create_user(username='routing-owner')
        self.project = StartupProfile.objects.create(owner=self.user, name='Routing')
        self.client.force_login(self.user)

    def submit(self, source, complexity, *, start_new=False):
        models = []
        def decide(*args):
            models.append(lab_models.active_model())
            data = {'action':'edit', 'kind':'static', 'answer':'', 'suggestion':'',
                    'task':'Поменяй заголовок', 'complexity':complexity}
            return data, CodeResult(json.dumps(data), lab_models.active_model(), 10, 20)
        def generate(*args):
            models.append(lab_models.active_model())
            return CodeResult(HTML.replace('Demo', 'Название'), lab_models.active_model(), 30, 40)
        data = {'prompt':'Поменяй заголовок', 'automatic':'1', 'kind':'static'}
        if source:
            data['source_version'] = source.pk
        if start_new:
            data['start_new'] = '1'
        with patch('founder.services.lab_intent.decide', side_effect=decide), \
             patch('founder.lab_views._generate_design', side_effect=generate):
            response = self.client.post(reverse('lab_generate', args=[self.project.pk]), data)
        self.assertEqual(response.status_code, 302)
        return models, self.project.lab_versions.first()

    def test_first_prompt_sol_then_small_edit_luna_and_complex_edit_sol(self):
        models, first = self.submit(None, 'small')
        self.assertEqual(models, [lab_models.SOL, lab_models.SOL])
        models, small = self.submit(first, 'small')
        self.assertEqual(models, [lab_models.LUNA, lab_models.LUNA])
        self.assertEqual(small.model, lab_models.LUNA)
        self.assertEqual(small.bruno_report['routing']['reason'], 'Небольшая правка')
        page = self.client.get(reverse('lab', args=[self.project.pk]))
        self.assertContains(page, 'GPT‑6 Luna')
        self.assertContains(page, 'Небольшая правка')
        models, complex_version = self.submit(small, 'complex')
        self.assertEqual(models, [lab_models.LUNA, lab_models.SOL])
        self.assertEqual(complex_version.model, lab_models.SOL)
        self.assertIsNone(lab_models.override_model())

    def test_new_site_from_existing_version_starts_with_sol(self):
        old = LabSiteVersion.objects.create(startup=self.project, prompt='old', html=HTML, model=lab_models.LUNA)
        models, new = self.submit(old, 'small', start_new=True)
        self.assertEqual(models, [lab_models.SOL, lab_models.SOL])
        self.assertIsNone(new.source_id)

    @override_settings(LAB_RAW_AI_TEST_MODE=False)
    def test_cached_luna_result_is_not_reused_when_analysis_selects_sol(self):
        old = LabSiteVersion.objects.create(startup=self.project, prompt='old', html=HTML, model=lab_models.SOL)
        models, small = self.submit(old, 'small')
        self.assertEqual(models, [lab_models.LUNA, lab_models.LUNA])
        models, complex_version = self.submit(old, 'complex')
        self.assertEqual(models, [lab_models.LUNA, lab_models.SOL])
        self.assertNotEqual(small.generation_key, complex_version.generation_key)

    def test_job_choice_survives_reload_and_cannot_be_set_by_client(self):
        job_id = uuid4()
        with patch('founder.lab_job_views.launch'):
            response = self.client.post(reverse('lab_job_start', args=[self.project.pk]), {
                'request_id':job_id, 'prompt':'Сделай сайт', 'automatic':'1',
                '_model_route':json.dumps({'model':'injected'})})
        self.assertEqual(response.status_code, 202)
        job = LabGenerationJob.objects.get(pk=job_id)
        self.assertNotIn('_model_route', job.payload)
        job.status = 'running'
        job.save(update_fields=['status'])
        request = SimpleNamespace(lab_job_id=job.pk)
        choice = lab_models.generation_route(None)
        remember_model_route(request, choice)
        job.refresh_from_db()
        self.assertEqual(describe(job)['model_route'], choice)
        self.assertEqual(json.loads(job.payload['_submitted'][0])['prompt'], ['Сделай сайт'])


@override_settings(LAB_CODE_PROVIDER='ofox', LAB_MODEL_ROUTING=True, LAB_UNRESTRICTED_TEST_MODE=True,
                   OFOX_CODE_API='chat_completions', OFOX_CODE_STREAM=False, OFOX_PROVIDER_TYPE='',
                   OFOX_CODE_MODEL='anthropic/claude-sonnet-5.5', OFOX_PRICE_MODEL='anthropic/claude-sonnet-5.5',
                   OFOX_USD_RUB_RATE='100', QWEN_CODE_MAX_TOKENS=1000,
                   LAB_SOL_INPUT_USD_PER_MILLION='2', LAB_SOL_OUTPUT_USD_PER_MILLION='10',
                   LAB_LUNA_INPUT_USD_PER_MILLION='0.10', LAB_LUNA_OUTPUT_USD_PER_MILLION='0.50')
class RoutedBillingTests(TestCase):
    def adapter(self, handler):
        def client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        return patch('founder.services.qwen.OpenAI', side_effect=client)

    def test_payload_and_ledger_use_each_models_own_price(self):
        called = []
        def handler(request):
            model = json.loads(request.content)['model']
            called.append(model)
            return httpx.Response(200, json={'id':'test', 'object':'chat.completion', 'created':1,
                'model':model, 'choices':[{'index':0,'finish_reason':'stop',
                    'message':{'role':'assistant','content':'READY'}}],
                'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
        with patch.dict(os.environ, {'OFOX_API_KEY':'test-routing-key'}), self.adapter(handler):
            for model, prices in [(lab_models.SOL, (200,1000)), (lab_models.LUNA, (10,50))]:
                with self.subTest(model=model), lab_models.model_scope(model):
                    result = generate_code('Instruction', [{'role':'user','content':'Task'}], max_tokens=100)
                    usage = LabAIUsage.objects.get(pk=result.request_id)
                    self.assertEqual(result.model, model)
                    self.assertEqual((usage.input_price,usage.output_price), tuple(map(Decimal,prices)))
                    self.assertEqual(usage.accounted_micro_rub, 10*prices[0]+20*prices[1])
        self.assertEqual(called, [lab_models.SOL,lab_models.LUNA])

    def test_provider_failure_never_retries_with_sol(self):
        called = []
        def handler(request):
            called.append(json.loads(request.content)['model'])
            return httpx.Response(500, json={'error':{'message':'unavailable'}})
        with patch.dict(os.environ, {'OFOX_API_KEY':'test-routing-key'}), self.adapter(handler):
            with self.assertRaises(QwenError), lab_models.model_scope(lab_models.LUNA):
                generate_code('Instruction', [{'role':'user','content':'Task'}], max_tokens=100)
        self.assertEqual(called, [lab_models.LUNA])
        self.assertIsNone(lab_models.override_model())

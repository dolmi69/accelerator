"""One Qwen response chooses a ready tool OR code; no paid classification round."""
import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from founder.models import User, StartupProfile, LabSiteVersion, LabAIUsage
from founder.services.qwen import CodeResult, QwenOutputError
from founder.services.site_intent import parse_intent
from founder.services.site_editor import simple_command
from founder.services.lab_design import design_pending

MODEL = 'Qwen/Qwen3-Coder-Next'
HTML = '<!doctype html><html><head><title>Demo</title><style>body{font-family:Arial,sans-serif}h1{font-family:Georgia,serif}</style></head><body><h1>Demo</h1><p>Keep me</p><button>Buy</button></body></html>'
FEEDBACK = {'summary':'Изменил общий шрифт на классический.', 'completed':['Содержимое сайта сохранено.'], 'not_done':[]}
TEMPLATE = {'route':'template', 'settings':{'font':'serif'}, 'add_modules':[], 'report':FEEDBACK}


class SiteIntentTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='intent_owner')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Demo')
        self.source = LabSiteVersion.objects.create(startup=self.startup, html=HTML, prompt='Initial', model=MODEL,
            kind='django', backend_modules=['accounts','registration'], presentation={'text_replacements':{'Buy':'Try'}})
        self.client.force_login(self.owner)
        self.route = reverse('lab_generate', args=[self.startup.pk])

    def post(self, prompt='Пожалуйста, сделай шрифт более классическим', **options):
        return self.client.post(self.route, {'source_version':self.source.pk, 'prompt':prompt, **options})

    def response(self, body=TEMPLATE, **kwargs):
        return CodeResult(json.dumps(body, ensure_ascii=False), MODEL, 120, 25, **kwargs)

    def test_template_and_report_arrive_in_one_qwen_call_without_giga(self):
        with patch('founder.services.lab_bruno.complete_lab') as giga, \
             patch('founder.services.site_generator.generate_code', return_value=self.response()) as api:
            self.assertEqual(self.post('измени шрифт').status_code, 302)
        api.assert_called_once(); giga.assert_not_called()
        version = self.startup.lab_versions.first()
        self.assertEqual(version.edit_method, 'template')
        self.assertEqual(version.presentation['font'], 'serif')
        self.assertEqual(version.presentation['text_replacements'], {'Buy':'Try'})
        self.assertIn('<button>Try</button>', version.html)
        self.assertIn('Keep me', version.html)
        self.assertIn('Georgia,serif!important', version.html)
        self.assertEqual((version.input_tokens,version.output_tokens),(120,25))
        page = self.client.get(reverse('lab', args=[self.startup.pk]))
        self.assertNotContains(page, 'Сохранить оформление')
        self.assertNotContains(page, 'id_font')
        self.assertContains(page, 'Возможности сайта')

    def test_repeat_uses_cache_without_another_qwen_call(self):
        with patch('founder.services.site_generator.generate_code', return_value=self.response()) as api:
            self.post(); self.post()
        api.assert_called_once()
        self.assertEqual(self.startup.lab_versions.count(),2)

    def test_mixed_request_generates_patch_and_installs_requested_modules_in_one_call(self):
        body = {'changes':[{'target':'full','find':'<p>Keep me</p>','replace':'<p>Keep me</p><p>Доставка</p>'}],
                'add_modules':['password_reset'], 'report':{'summary':'Добавил сброс пароля и пояснение доставки.', 'completed':['Формула сохранена.'], 'not_done':[]}}
        prompt = 'Добавь сброс пароля и пояснение доставки'
        with patch('founder.services.site_generator.generate_code', return_value=self.response(body)) as api:
            self.assertEqual(self.post(prompt).status_code,302)
        api.assert_called_once()
        self.assertIn(prompt, str(api.call_args.args[1]))
        version = self.startup.lab_versions.first()
        self.assertEqual(version.edit_method,'patch')
        self.assertIn('password_reset',version.backend_modules)
        self.assertEqual((version.input_tokens,version.output_tokens),(120,25))

    def test_invalid_action_keeps_old_site_without_a_paid_retry(self):
        bad = {**TEMPLATE, 'settings':{'font':'url(https://evil.test)'}}
        with patch('founder.services.site_generator.generate_code', return_value=self.response(bad)) as api:
            response = self.post()
        # An incomplete design may save a ready scaffold but never the bad code.
        self.assertEqual(response.status_code,302)
        api.assert_called_once()
        version = self.startup.lab_versions.first()
        self.assertEqual(version.html,self.source.html)
        self.assertTrue(version.bruno_report['failed'])
        from founder.services.request_limits import acquire_ai_lease, release_ai_lease
        release_ai_lease(self.owner.pk,acquire_ai_lease(self.owner.pk))

    def test_unknown_modules_or_unsafe_settings_are_rejected(self):
        invalid = [[], {'route':'execute'}, {'route':'generate','settings':{}},
                   {'route':'template','settings':{},'add_modules':[]},
                   {'route':'template','settings':{'css':'body{}'},'add_modules':[]},
                   {'route':'template','settings':{'radius':24},'add_modules':[]},
                   {'route':'template','settings':{'title':'<script>'},'add_modules':[]},
                   {'route':'template','settings':{},'add_modules':['shell']},
                   {'route':'template','settings':{},'add_modules':['chat','chat']}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(QwenOutputError): parse_intent(json.dumps(value))
        self.assertIsNone(parse_intent('{"route":"generate"}'))

    def test_natural_module_request_returns_tools_without_generating_html(self):
        body={**TEMPLATE,'settings':{},'add_modules':['registration','password_reset'],
              'report':{'summary':'Подключил регистрацию и сброс пароля.', 'completed':['Основная страница сохранена.'], 'not_done':[]}}
        with patch('founder.services.site_generator.generate_code', return_value=self.response(body)) as api:
            self.assertEqual(self.post('Хочу, чтобы посетитель мог завести аккаунт и вернуть забытый пароль').status_code,302)
        api.assert_called_once()
        version=self.startup.lab_versions.first()
        self.assertEqual(version.kind,'django')
        self.assertIn('password_reset',version.backend_modules)
        self.assertEqual(version.edit_method,'template')

    @override_settings(LAB_USER_DAILY_RUB='0')
    def test_known_commands_stay_free_without_qwen(self):
        with patch('founder.services.site_generator.generate_code') as api:
            self.assertEqual(self.post('измени шрифт на классический').status_code,302)
        api.assert_not_called()
        version=self.startup.lab_versions.first()
        self.assertEqual((version.input_tokens,version.output_tokens),(0,0))
        self.assertFalse(simple_command('измени шрифт на классический и добавь калькулятор'))

    def test_unknown_usage_is_never_replaced_with_zero(self):
        with patch('founder.services.site_generator.generate_code',return_value=CodeResult(json.dumps(TEMPLATE),MODEL,None,None)):
            self.post()
        version=self.startup.lab_versions.first()
        self.assertIsNone(version.input_tokens)
        self.assertIsNone(version.output_tokens)

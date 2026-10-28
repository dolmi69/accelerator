"""Raw experiments must use the model and never depend on disabled tools."""
import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from founder.models import LabSiteVersion, StartupProfile, User
from founder.services.qwen import CodeResult, QwenError
from founder.services.site_generator import generate_site

HTML = '<!doctype html><html><head><meta charset="utf-8"><title>Аренда</title><style>body{color:#222}</style></head><body><h1>Аренда</h1><p>Демонстрация — данные не сохраняются на сервере</p><button id="choose">Выбрать</button></body></html>'
REPORT = {'summary': 'Создал демонстрационный интерфейс аренды.', 'completed': ['Добавил выбор инструмента.'], 'not_done': ['Сервер не подключён.']}
MODEL = 'openai/gpt-6-luna'

@override_settings(LAB_RAW_AI_TEST_MODE=True, LAB_REQUESTS_PER_DAY=0, QWEN_CODE_MAX_TOKENS=16384, LAB_CREATE_MAX_TOKENS=16384)
class RawLabTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='raw-test', email='raw@test.example')
        self.startup = StartupProfile.objects.create(owner=self.user, name='Аренда')
        self.client.force_login(self.user)

    def answer(self):
        return CodeResult(json.dumps({'html': HTML, 'report': REPORT}), MODEL, 900, 600)

    def patch_answer(self):
        return CodeResult(json.dumps({'changes':[{'target':'full','find':'body{color:#222}',
            'replace':'body{color:#333;font-family:Arial}'}], 'report':REPORT}), MODEL, 100, 100)

    def post(self, prompt, **extra):
        return self.client.post(reverse('lab_generate', args=[self.startup.pk]), {'prompt':prompt, **extra})

    def test_followup_uses_exact_patch_without_examples_or_platform_tools(self):
        with patch('founder.services.site_generator.component_prompt') as components, patch('founder.services.site_generator.enhance_interactions') as interactions, patch('founder.services.site_generator.generate_code', return_value=self.patch_answer()) as api:
            result = generate_site('Добавь FAQ', previous_html=HTML, backend=True, report=True, project_context={'modules': ['accounts', 'booking']})
        components.assert_not_called()
        interactions.assert_not_called()
        self.assertEqual(result.text, HTML.replace('body{color:#222}','body{color:#333;font-family:Arial}'))
        self.assertEqual(result.edit_method, 'patch')
        self.assertEqual(result.add_modules, ())
        self.assertEqual(json.loads(api.call_args.args[1][-1]['content'])['fragments'][0]['html'], HTML)
        self.assertIn('changes', api.call_args.args[0])
        self.assertNotIn('data-forge-components', result.text)

    def test_repeated_requests_call_model_each_time_no_local_dispatch_or_cache(self):
        source = LabSiteVersion.objects.create(startup=self.startup, html=HTML, prompt='Исходник', model='local-editor', presentation={'font':'serif'})
        with patch('founder.services.site_generator.generate_code', return_value=self.patch_answer()) as api:
            for _ in range(2):
                self.assertEqual(self.post('Измени шрифт', source_version=source.pk).status_code, 302)
        self.assertEqual(api.call_count, 2)
        generated = self.startup.lab_versions.exclude(pk=source.pk)
        self.assertEqual(generated.count(), 2)
        self.assertTrue(all(v.generation_key is None and v.presentation == {} for v in generated))

    def test_booking_without_template_routes_can_be_saved_as_honest_static_demo(self):
        with patch('founder.services.site_generator.generate_code', return_value=self.answer()):
            response = self.post('Создай сервис аренды с бронированием', kind='django', modules_selected='1', modules=['catalog','booking'])
        self.assertEqual(response.status_code, 302)
        version = self.startup.lab_versions.get()
        self.assertEqual(version.kind, 'static')
        self.assertEqual(version.backend_modules, [])
        self.assertIn('Сырой тест', str(version.bruno_report))

    def test_raw_tools_are_hidden_and_old_endpoints_are_disabled(self):
        response = self.client.get(reverse('lab', args=[self.startup.pk]))
        self.assertContains(response, 'Сырой тест модели')
        self.assertNotContains(response, 'data-lab-tools-open')
        self.assertNotContains(response, 'name="modules_selected"')
        for name in ('lab_backend_create', 'lab_customize'):
            response = self.client.post(reverse(name, args=[self.startup.pk]), {'modules':['catalog']})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.startup.lab_versions.count(), 0)

    @override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_MODEL='openai/gpt-6.1-sol')
    def test_active_model_is_distinct_from_model_of_existing_version(self):
        source = LabSiteVersion.objects.create(startup=self.startup, html=HTML, prompt='Исходник', model=MODEL)
        response = self.client.get(reverse('lab', args=[self.startup.pk]), {'version':str(source.pk)})
        self.assertEqual(response.context['lab_model_label'], 'GPT‑6.1 Sol')
        self.assertEqual(response.context['selected'].model_label, 'GPT‑6 Luna')
        self.assertNotContains(response, 'Запрос получит')
        self.assertContains(response, 'Модель этой версии: GPT‑6 Luna')

    def test_failed_model_does_not_create_scaffold_or_delete_previous_version(self):
        source = LabSiteVersion.objects.create(startup=self.startup, html=HTML, prompt='Исходник', kind='django', model=MODEL, backend_modules=['accounts','catalog','booking'])
        with patch('founder.services.site_generator.generate_code', side_effect=QwenError('Ofox недоступен')):
            response = self.post('Переделай оформление', source_version=source.pk)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.startup.lab_versions.count(), 1)
        source.refresh_from_db()
        self.assertEqual(source.html, HTML)
        self.assertEqual(source.backend_modules, ['accounts','catalog','booking'])

    def test_disabled_bridge_output_is_rejected(self):
        bad = self.answer()
        bad = CodeResult(json.dumps({'html': HTML.replace('</body>', '<a href="/catalog/" data-app-route="/catalog/">Каталог</a></body>'), 'report': REPORT}), MODEL, 900, 600)
        with patch('founder.services.site_generator.generate_code', return_value=bad):
            response = self.post('Создай сервис аренды')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.startup.lab_versions.count(), 0)

    def test_saved_report_question_uses_model_in_raw_mode(self):
        source = LabSiteVersion.objects.create(startup=self.startup, html=HTML, prompt='Исходник', model=MODEL)
        answer = CodeResult(json.dumps({'answer':'Создан интерфейс аренды, сервера нет.'}), MODEL, 200, 50)
        with patch('founder.services.lab_conversation.generate_code', return_value=answer) as api:
            response = self.client.post(reverse('lab_discuss', args=[self.startup.pk]), {'prompt':'Что изменилось?', 'source_version':source.pk}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        api.assert_called_once()
        self.assertNotIn('Без AI', response.json()['reply']['usage'])

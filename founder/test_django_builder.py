"""Ownership, export boundaries and compatibility of the backend constructor."""
from io import BytesIO
import json
from unittest.mock import patch
from zipfile import ZipFile

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from founder.models import LabSiteVersion, StartupProfile, User
from founder.services.qwen import CodeResult
from founder.services.django_builder import BuilderError, project_files, export_project
from founder.services.lab_design import design_context, design_pending

HTML = '<!doctype html><html><head></head><body><h1>{{ user.password }}</h1></body></html>'


@override_settings(LAB_BACKEND_RUNTIME_ENABLED=True, QWEN_CODE_MAX_TOKENS=16384,
                   LAB_CREATE_MAX_TOKENS=16384, LAB_PATCH_MAX_TOKENS=2048)
class BuilderTests(TestCase):
    def setUp(self):
        from founder.test_lab_support import install_lab_mocks
        install_lab_mocks(self)
        self.owner = User.objects.create_user(username='builder_owner', email='owner@builder.test')
        self.other = User.objects.create_user(username='builder_other', email='other@builder.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Наш сайт')
        self.client.force_login(self.owner)
        self.static = LabSiteVersion.objects.create(startup=self.startup, prompt='Сайт', html=HTML, model='Qwen')

    def route(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    def scaffold(self):
        response = self.client.post(self.route('lab_backend_create'), {'source_version':self.static.pk})
        self.assertEqual(response.status_code, 302)
        return LabSiteVersion.objects.get(kind='django')

    def test_add_backend_preserves_original_and_uses_no_llm(self):
        with patch('founder.lab_views.generate_site') as generate:
            version = self.scaffold()
            generate.assert_not_called()
        self.static.refresh_from_db()
        self.assertEqual(self.static.kind, 'static')
        self.assertEqual(version.html, HTML)
        self.assertEqual(version.source_id, self.static.pk)
        self.assertEqual(version.input_tokens, 0)
        from founder.services.backend_modules import BASE_MODULES
        self.assertEqual(set(version.backend_modules), {*BASE_MODULES, 'registration'})
        self.client.post(self.route('lab_backend_create'), {'source_version':version.pk})
        self.assertEqual(LabSiteVersion.objects.filter(kind='django').count(), 1)
        page = self.client.get(self.route('lab') + f'?version={version.pk}', HTTP_HOST='localhost:8000')
        self.assertContains(page, 'На весь экран')
        self.assertContains(page, 'id="lab-viewer"')
        self.assertContains(page, 'id="lab-site-frame"', count=1)
        self.assertContains(page, 'id="lab-preview-run"')
        self.assertNotContains(page, 'id="lab-viewer-frame"')
        self.assertContains(page, 'Скачать Django-проект')
        self.assertContains(page, 'Возможности сайта')

    def test_only_registration_is_checked_and_can_be_removed_without_ai(self):
        from founder.services.backend_modules import DEFAULT_BASE_FEATURES
        page = self.client.get(self.route('lab') + f'?version={self.static.pk}')
        self.assertContains(page, 'Основы маркетплейса')
        checked = [widget.data['value'] for widget in page.context['backend_form']['modules'] if widget.data['selected']]
        self.assertEqual(checked, ['registration'])
        with patch('founder.lab_views.generate_site') as generate:
            self.client.post(self.route('lab_backend_create'), {
                'source_version': self.static.pk, 'modules_selected': '1', 'modules': ['catalog'],
            })
            generate.assert_not_called()
        version = LabSiteVersion.objects.get(kind='django')
        exported = json.loads(project_files(version)['site.json'])['modules']
        for key in DEFAULT_BASE_FEATURES:
            self.assertNotIn(key, exported)
        self.assertIn('accounts', exported)  # Owner can still sign in and manage the site.

    def test_design_submission_applies_selected_modules_and_preserves_them_on_failure(self):
        from founder.services.qwen import QwenOutputError
        data = {'prompt': 'Нужен сайт для продажи духов', 'modules_selected': '1',
                'modules': ['registration', 'password_reset', 'chat', 'catalog', 'orders']}
        with patch('founder.lab_views.generate_site', side_effect=QwenOutputError('Обрезано')) as generate:
            response = self.client.post(self.route('lab_generate'), data)
            self.assertEqual(response.status_code, 302)
            generate.assert_called_once()
            self.assertTrue(generate.call_args.kwargs['backend'])
        version = LabSiteVersion.objects.get(kind='django')
        self.assertEqual(version.output_tokens, 0)  # Scaffold makes no additional AI call.
        self.assertIn('orders', version.backend_modules)
        self.assertNotIn('notifications', version.backend_modules)
        with patch('founder.lab_views.generate_site', return_value=CodeResult(HTML, 'Qwen', 12, 34)):
            response = self.client.post(self.route('lab_generate'), {**data, 'source_version': version.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LabSiteVersion.objects.first().backend_modules, version.backend_modules)

    def test_invalid_design_modules_do_not_call_ai(self):
        with patch('founder.lab_views.generate_site') as generate:
            response = self.client.post(self.route('lab_generate'), {
                'prompt': 'Магазин', 'modules_selected': '1', 'modules': ['invented-module'],
            })
        self.assertEqual(response.status_code, 400)
        generate.assert_not_called()

    def test_backend_generation_and_revision_keep_kind_and_context(self):
        with patch('founder.lab_views.generate_site', return_value=CodeResult(HTML,'Qwen',12,34)) as generate:
            self.client.post(self.route('lab_generate'), {'kind':'django','prompt':'Сайт для клуба'})
            first = LabSiteVersion.objects.get(kind='django')
            generate.assert_called_with('Сайт для клуба',previous_html='',max_tokens=16384,backend=True,
                project_context=design_context(self.startup, first.backend_modules), report=True)
            self.client.post(self.route('lab_generate'), {'kind':'django','prompt':'Сделай синим','source_version':first.pk})
            generate.assert_called_with('Сделай синим',previous_html=HTML,max_tokens=16384,backend=True,
                project_context=design_context(self.startup, first.backend_modules, source=first), report=True)
            self.assertEqual(LabSiteVersion.objects.filter(kind='django').count(), 2)

    def test_export_only_trusted_source_and_raw_frontend(self):
        version = self.scaffold()
        response = self.client.get(self.route('lab_download',version.pk))
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(b''.join(response.streaming_content))) as archive:
            names = archive.namelist()
            self.assertIn('django_site/core/migrations/0001_initial.py', names)
            self.assertIn('django_site/config/asgi.py', names)
            self.assertIn('django_site/core/tests.py', names)
            self.assertEqual(archive.read('django_site/prototype.html').decode(), HTML)
            self.assertEqual(json.loads(archive.read('django_site/site.json'))['name'], 'Наш сайт')
            self.assertFalse(any(name.endswith(('.sqlite3','.env','.local-secret')) for name in names))
            self.assertFalse(any('..' in name or '__pycache__' in name for name in names))
        with self.assertRaises(BuilderError):
            project_files(self.static)

    def test_foreign_owner_anonymous_csrf_and_invalid_mode(self):
        version = self.scaffold()
        self.client.force_login(self.other)
        with patch('founder.lab_views.start_runtime') as run:
            self.assertEqual(self.client.get(self.route('lab_download',version.pk)).status_code,404)
            self.assertEqual(self.client.post(self.route('lab_run',version.pk)).status_code,404)
            self.assertEqual(self.client.post(self.route('lab_backend_create')).status_code,404)
            self.assertEqual(self.client.post(self.route('lab_stop')).status_code,404)
            run.assert_not_called()
        self.assertEqual(Client().get(self.route('lab_download',version.pk)).status_code,302)
        csrf=Client(enforce_csrf_checks=True); csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.route('lab_backend_create')).status_code,403)
        self.client.force_login(self.owner)
        with patch('founder.lab_views.generate_site') as generate:
            self.assertEqual(self.client.post(self.route('lab_generate'), {'kind':'arbitrary-python','prompt':'x'}).status_code,400)
            generate.assert_not_called()

    def test_launch_only_locally_and_with_post(self):
        version=self.scaffold()
        self.assertEqual(self.client.get(self.route('lab_run',version.pk)).status_code,405)
        with patch('founder.lab_views.start_runtime',return_value='http://localhost:9000/') as run:
            response=self.client.post(self.route('lab_run',version.pk), HTTP_HOST='localhost:8000')
            self.assertEqual(response.url,'http://localhost:9000/')
            run.assert_called_once()
        with override_settings(ALLOWED_HOSTS=['demo.example'], LAB_BACKEND_RUNTIME_ENABLED=True):
            with patch('founder.lab_views.start_runtime') as run:
                self.client.post(self.route('lab_run',version.pk), HTTP_HOST='demo.example')
                run.assert_not_called()
        with override_settings(LAB_BACKEND_RUNTIME_ENABLED=False):
            with patch('founder.lab_views.start_runtime') as run:
                self.client.post(self.route('lab_run',version.pk), HTTP_HOST='localhost:8000')
                run.assert_not_called()

    def test_embedded_launch_returns_json_and_fixed_owned_navigation_context(self):
        version = self.scaffold()
        with patch('founder.lab_views.start_runtime', return_value='http://127.0.0.1:9000/') as run:
            response = self.client.post(self.route('lab_run', version.pk),
                HTTP_HOST='127.0.0.1:8000', HTTP_ACCEPT='application/json')
            self.assertEqual(response.json(), {'url': 'http://127.0.0.1:9000/'})
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertEqual(run.call_args.kwargs, {
                'launcher_origin': 'http://127.0.0.1:8000',
                'return_path': self.route('lab') + f'?version={version.pk}#backend-title',
            })
        with patch('founder.lab_views.start_runtime', side_effect=BuilderError('Не удалось запустить')):
            response = self.client.post(self.route('lab_run', version.pk),
                HTTP_HOST='localhost:8000', HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['error'], 'Не удалось запустить')
        with override_settings(LAB_BACKEND_RUNTIME_ENABLED=False):
            response = self.client.post(self.route('lab_run', version.pk),
                HTTP_HOST='localhost:8000', HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 403)

    def test_preview_opens_real_site_without_owner_setup_and_stop_pauses_autostart(self):
        version = self.scaffold()
        with patch('founder.lab_views.start_runtime', return_value='http://127.0.0.1:9000/setup/owner-secret/'):
            response = self.client.post(self.route('lab_run', version.pk), {'preview': '1'},
                HTTP_HOST='127.0.0.1:8000', HTTP_ACCEPT='application/json')
        self.assertEqual(response.json(), {'url': 'http://127.0.0.1:9000/'})
        with patch('founder.lab_views.stop_runtime') as stop:
            response = self.client.post(self.route('lab_stop'), {'source_version': version.pk}, HTTP_HOST='localhost:8000')
        stop.assert_called_once_with(self.startup.pk)
        self.assertEqual(response.url, self.route('lab') + f'?version={version.pk}&paused=1')
        with patch('founder.lab_views.start_runtime') as run:
            page = self.client.get(response.url, HTTP_HOST='localhost:8000')
        run.assert_not_called()  # GET only renders; startup is a CSRF-protected POST.
        self.assertContains(page, 'data-autostart="0"')
        self.assertContains(page, 'Сайт остановлен.')
        self.assertNotContains(page, f'src="{self.route("lab_preview", version.pk)}"')

    def test_remote_lab_keeps_static_sandbox_without_launching_local_servers(self):
        version = self.scaffold()
        with override_settings(ALLOWED_HOSTS=['demo.example']):
            page = self.client.get(self.route('lab') + f'?version={version.pk}', HTTP_HOST='demo.example')
        self.assertContains(page, f'src="{self.route("lab_preview", version.pk)}"')
        self.assertContains(page, 'sandbox="allow-scripts"')
        self.assertNotContains(page, 'id="lab-preview-run"')

    def test_new_scaffold_escapes_startup_text(self):
        self.startup.name='<script>alert(1)</script>'; self.startup.save(update_fields=['name'])
        self.client.post(self.route('lab_backend_create'))
        version=LabSiteVersion.objects.get(kind='django')
        self.assertNotIn('<script>',version.html)
        self.assertIn('&lt;script&gt;',version.html)

    def test_module_selection_dependencies_immutable_version_and_export(self):
        source=self.scaffold()
        with patch('founder.lab_views.generate_site') as paid:
            self.client.post(self.route('lab_backend_create'),{'source_version':source.pk,'modules_selected':'1','modules':['favorites','booking','orders','uploads']})
            paid.assert_not_called()
        selected=LabSiteVersion.objects.filter(kind='django').first()
        self.assertNotEqual(source.pk,selected.pk)
        self.assertIn('catalog',selected.backend_modules)
        self.assertEqual(selected.output_tokens,0)
        exported=json.loads(project_files(selected)['site.json'])
        self.assertIn('orders',exported['modules'])
        self.assertIn('core/migrations/0002_ready_modules.py',project_files(selected))
        self.client.post(self.route('lab_backend_create'),{'source_version':selected.pk,'modules_selected':'1','modules':['invented-python']})
        self.assertEqual(LabSiteVersion.objects.filter(kind='django').count(),2)

    def test_module_command_is_free_and_mixed_requests_are_not_silently_dropped(self):
        with patch('founder.lab_views.generate_site') as paid:
            self.client.post(self.route('lab_generate'),{'source_version':self.static.pk,'kind':'static',
                'prompt':'сделай регистрацию пожалуйста так чтобы можно было и зарегистрироваться и войти и сбросить пароль'})
            version=LabSiteVersion.objects.get(kind='django')
            self.assertEqual(version.html,self.static.html)
            paid.assert_not_called()
            self.client.post(self.route('lab_generate'),{'source_version':version.pk,'kind':'django','prompt':'добавь каталог и избранное'})
            paid.assert_not_called()
            self.assertIn('favorites',LabSiteVersion.objects.filter(kind='django').first().backend_modules)
        from founder.services.backend_modules import module_command
        for prompt in ['добавь каталог и сделай фон красным','убери регистрацию','добавь оплату в долларах','сделай вход через Google']:
            self.assertIsNone(module_command(prompt),prompt)

    def test_free_theme_keeps_enabled_modules(self):
        version=self.scaffold(); version.backend_modules=['catalog','leads']; version.save(update_fields=['backend_modules'])
        self.client.post(self.route('lab_customize'),{'source_version':version.pk,'palette':'blue'})
        self.assertEqual(LabSiteVersion.objects.first().backend_modules,['catalog','leads'])

    def test_compact_prompt_inherits_backend_without_kind_selector(self):
        source = self.scaffold()
        source.backend_modules = ['catalog', 'booking']
        source.save(update_fields=['backend_modules'])
        with patch('founder.lab_views.generate_site', return_value=CodeResult(HTML, 'Qwen', 2, 3)) as generate:
            self.client.post(self.route('lab_generate'), {'source_version': source.pk, 'prompt': 'Добавь большой блок с описанием магазина'})
            generate.assert_called_once_with('Добавь большой блок с описанием магазина', previous_html=HTML, max_tokens=16384, backend=True,
                project_context=design_context(self.startup, source.backend_modules, source=source), report=True)
        latest = LabSiteVersion.objects.first()
        self.assertEqual(latest.kind, 'django')
        self.assertEqual(latest.backend_modules, ['catalog', 'booking'])

    def test_first_design_replaces_scaffold_and_later_edits_use_small_patches(self):
        from founder.services.lab_design import design_pending
        self.client.post(self.route('lab_backend_create'), {'modules_selected': '1', 'modules': ['catalog']})
        scaffold = LabSiteVersion.objects.get(kind='django')
        self.assertTrue(design_pending(scaffold))
        self.client.post(self.route('lab_customize'), {'source_version': scaffold.pk, 'palette': 'blue'})
        themed = LabSiteVersion.objects.first()
        self.assertTrue(design_pending(themed))
        self.startup.target_customer = 'Покупатели парфюмерии'
        self.startup.save(update_fields=['target_customer'])
        with patch('founder.services.site_generator.generate_code', return_value=CodeResult(json.dumps({'html':HTML,'report':{'summary':'Создал дизайн.', 'completed':['Собрал главную страницу.'], 'not_done':[]}}), 'Qwen', 12, 34)) as paid:
            response = self.client.post(self.route('lab_generate'), {
                'source_version': themed.pk, 'prompt': 'Нужен магазин духов',
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(paid.call_count, 1)
        self.assertEqual(paid.call_args.kwargs['max_tokens'], 16384)
        self.assertEqual(paid.call_args.kwargs['temperature'], 0.6)
        self.assertIn('Покупатели парфюмерии', str(paid.call_args.args[1]))
        self.assertIn('Наш сайт', str(paid.call_args.args[1]))
        self.assertNotIn(themed.html, str(paid.call_args.args[1]))
        designed = LabSiteVersion.objects.first()
        self.assertFalse(design_pending(designed))
        self.assertEqual(designed.backend_modules, scaffold.backend_modules)
        change = json.dumps({'changes': [{'target': 'full', 'find': '{{ user.password }}', 'replace': 'Готово'}], 'report':{'summary':'Изменил заголовок.', 'completed':['Заголовок стал «Готово».'], 'not_done':[]}})
        with patch('founder.services.site_generator.generate_code', return_value=CodeResult(change, 'Qwen', 2, 3)) as paid:
            response = self.client.post(self.route('lab_generate'), {'source_version': designed.pk, 'prompt': 'Измени заголовок'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(paid.call_args.kwargs['max_tokens'], 2048)
        self.assertEqual(paid.call_args.kwargs['temperature'], 0.2)
        self.assertEqual(LabSiteVersion.objects.first().edit_method, 'patch')

    def test_modules_added_to_existing_design_do_not_reset_that_design(self):
        source = self.scaffold()
        self.assertFalse(design_pending(source))
        with patch('founder.lab_views.generate_site', return_value=CodeResult(HTML, 'Qwen', 12, 34)) as paid:
            self.client.post(self.route('lab_generate'), {'source_version': source.pk, 'prompt': 'Улучшить композицию'})
        self.assertEqual(paid.call_args.kwargs['previous_html'], HTML)

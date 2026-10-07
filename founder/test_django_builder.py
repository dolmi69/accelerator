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

HTML = '<!doctype html><html><head></head><body><h1>{{ user.password }}</h1></body></html>'


@override_settings(LAB_BACKEND_RUNTIME_ENABLED=True)
class BuilderTests(TestCase):
    def setUp(self):
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
        self.client.post(self.route('lab_backend_create'), {'source_version':version.pk})
        self.assertEqual(LabSiteVersion.objects.filter(kind='django').count(), 1)
        page = self.client.get(self.route('lab') + f'?version={version.pk}', HTTP_HOST='localhost:8000')
        self.assertContains(page, 'Запустить на весь экран')
        self.assertContains(page, 'id="lab-viewer"')
        self.assertContains(page, 'Скачать Django-проект')
        self.assertContains(page, 'Возможности сайта')

    def test_base_features_are_checked_and_can_be_removed_without_ai(self):
        from founder.services.backend_modules import DEFAULT_BASE_FEATURES
        page = self.client.get(self.route('lab') + f'?version={self.static.pk}')
        self.assertContains(page, 'Основы маркетплейса')
        for key in DEFAULT_BASE_FEATURES:
            self.assertRegex(page.content.decode(), rf'value="{key}"[^>]*checked')
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

    def test_backend_generation_and_revision_keep_kind_and_context(self):
        with patch('founder.lab_views.generate_site', return_value=CodeResult(HTML,'Qwen',12,34)) as generate:
            self.client.post(self.route('lab_generate'), {'kind':'django','prompt':'Сайт для клуба'})
            first = LabSiteVersion.objects.get(kind='django')
            generate.assert_called_with('Сайт для клуба',previous_html='',max_tokens=6144,backend=True)
            self.client.post(self.route('lab_generate'), {'kind':'django','prompt':'Сделай синим','source_version':first.pk})
            generate.assert_called_with('Сделай синим',previous_html=HTML,max_tokens=6144,backend=True)
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
            generate.assert_called_once_with('Добавь большой блок с описанием магазина', previous_html=HTML, max_tokens=6144, backend=True)
        latest = LabSiteVersion.objects.first()
        self.assertEqual(latest.kind, 'django')
        self.assertEqual(latest.backend_modules, ['catalog', 'booking'])

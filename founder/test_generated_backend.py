"""Backend generation contracts and real isolated SQLite persistence."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from zipfile import ZipFile
from urllib.parse import urlsplit

import httpx
from django.test import TestCase, override_settings
from django.urls import reverse
from founder.models import User, StartupProfile, LabSiteVersion
from founder.services.generated_backend import generate_backend, validate_files, export_backend
from founder.services.generated_runtime import available, start, stop_unlocked
from founder.services.qwen import CodeResult, QwenOutputError

HTML = '<!doctype html><html><head><title>Записи</title></head><body><h1>Записи</h1></body></html>'
APP = '''import os, sqlite3, json
DB = os.environ['LAB_DATABASE']
with sqlite3.connect(DB) as db:
    db.execute('CREATE TABLE IF NOT EXISTS items (name TEXT)')
def application(environ, start_response):
    with sqlite3.connect(DB) as db:
        if environ['REQUEST_METHOD'] == 'POST':
            data = json.load(environ['wsgi.input'])
            db.execute('INSERT INTO items(name) VALUES (?)', (data['name'],))
        rows = db.execute('SELECT name FROM items').fetchall()
    start_response('200 OK', [('Content-Type','application/json')])
    return [json.dumps(rows).encode()]
'''
REPORT = {'summary': 'Создал сервер для записей.', 'completed': ['SQLite и API'], 'not_done': []}
FILES = {'index.html': HTML, 'app.py': APP}

@override_settings(LAB_RAW_AI_TEST_MODE=True, LAB_BACKEND_STAGED_GENERATION=False)
class GeneratedBackendTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='backend-test')
        self.startup = StartupProfile.objects.create(owner=self.user, name='Записи')
        self.client.force_login(self.user)

    def test_explicit_backend_generation_stores_server_without_tools(self):
        response_text = json.dumps({'files': FILES, 'report': REPORT})
        with patch('founder.services.generated_backend.generate_code', return_value=CodeResult(response_text, 'test', 10, 20)) as api, patch('founder.lab_views.generate_site') as static:
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]), {'prompt': 'Создай сайт с записями', 'kind': 'generated'})
        self.assertEqual(response.status_code, 302)
        version = self.startup.lab_versions.get()
        self.assertEqual(version.kind, 'generated')
        self.assertEqual(version.backend_files, FILES)
        self.assertEqual(version.backend_modules, [])
        static.assert_not_called()
        self.assertIn('app.py', api.call_args.args[0])
        page = self.client.get(reverse('lab', args=[self.startup.pk]))
        self.assertContains(page, 'Скачать сайт с бэкендом')
        self.assertNotContains(page, 'data-lab-tools-open')
        self.assertNotIn('Данные не сохраняются', str(version.bruno_report))

    def test_edit_sends_full_previous_server(self):
        source = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files=FILES, model='test')
        with patch('founder.services.generated_backend.generate_code', return_value=CodeResult(json.dumps({'files': FILES, 'report': REPORT}), 'test', 10, 20)) as api:
            result = generate_backend('Добавь кнопку', source=source)
        self.assertEqual(json.loads(api.call_args.args[1][0]['content'])['previous_files']['app.py'], APP)
        self.assertEqual(result.backend_files, FILES)

    def test_traversal_syntax_secrets_and_missing_server_rejected(self):
        for files in ({**FILES, '../.env': 'x'}, {**FILES, 'app.py': 'broken syntax :'}, {'index.html': HTML}):
            with self.assertRaises(QwenOutputError):
                validate_files(files)
        with patch.dict('os.environ', {'OFOX_API_KEY': 'unit-test-secret'}):
            with self.assertRaises(QwenOutputError):
                validate_files({**FILES, 'app.py': APP + '\n# unit-test-secret'})

    def test_failed_generation_preserves_version(self):
        source = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files=FILES, model='test')
        with patch('founder.services.generated_backend.generate_code', return_value=CodeResult('{}', 'test', 10, 20)):
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]), {'prompt': 'Измени сайт', 'source_version': source.pk})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.startup.lab_versions.count(), 1)
        source.refresh_from_db()
        self.assertEqual(source.backend_files, FILES)

    def test_export_contains_model_code_no_db(self):
        version = SimpleNamespace(backend_files=FILES)
        with ZipFile(export_backend(version)) as archive:
            self.assertEqual(archive.read('site/app.py').decode(), APP)
            self.assertIn('site/start.py', archive.namelist())
            self.assertFalse(any('.sqlite' in name or '.env' in name for name in archive.namelist()))

    def start_checked(self, version, directory):
        try:
            return start(version, launcher_origin='http://127.0.0.1:8000')
        except Exception:
            logfile = Path(directory)/'.runtime/lab-backends'/str(self.startup.pk)/'generated.log'
            if logfile.exists():
                print(logfile.read_text())
            raise

    @skipUnless(available(), 'macOS isolated runtime')
    @override_settings(LAB_BACKEND_RUNTIME_ENABLED=True)
    def test_real_crud_restart_version_clone_and_origin(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            version = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files=FILES, model='test')
            try:
                url = self.start_checked(version, directory)
                self.assertEqual(urlsplit(url).hostname, 'localhost')
                with httpx.Client(trust_env=False) as client:
                    self.assertEqual(client.post(url+'api/items', json={'name': 'persisted'}).status_code, 403)
                    reply = client.post(url+'api/items', headers={'Origin': url.rstrip('/')}, json={'name': 'persisted'})
                    self.assertEqual(reply.json(), [['persisted']])
                    self.assertIn("connect-src 'self'", reply.headers['Content-Security-Policy'])
                    self.assertEqual(client.get(url+'api/items', headers={'Host':'evil.example'}).status_code, 403)
                stop_unlocked(self.startup.pk)
                url = self.start_checked(version, directory)
                self.assertEqual(httpx.get(url+'api/items', trust_env=False).json(), [['persisted']])
                child = LabSiteVersion.objects.create(startup=self.startup, source=version, kind='generated', html=HTML, backend_files=FILES, model='test')
                url = start(child, launcher_origin='http://127.0.0.1:8000')
                self.assertEqual(httpx.get(url+'api/items', trust_env=False).json(), [['persisted']])
                parent_db = Path(directory)/'.runtime/lab-backends'/str(self.startup.pk)/'generated-projects'/str(version.pk)/'data/data.sqlite3'
                with sqlite3.connect(parent_db) as db:
                    self.assertEqual(db.execute('SELECT name FROM items').fetchall(), [('persisted',)])
            finally:
                stop_unlocked(self.startup.pk)

    @skipUnless(available(), 'macOS isolated runtime')
    @override_settings(LAB_BACKEND_RUNTIME_ENABLED=True)
    def test_os_sandbox_blocks_host_files_outbound_connections_forks(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            secret = Path(directory)/'platform-secret.txt'
            secret.write_text('not-for-generated-app')
            app = '''import os,json,socket
checks={}
for name,fn in [('read',lambda:open(%r).read()),('write',lambda:open(%r,'w')),('network',lambda:socket.create_connection(('127.0.0.1',8000),timeout=.1)),('fork',lambda:os.fork())]:
    try:
        fn();checks[name]='allowed'
    except (OSError,PermissionError):checks[name]='blocked'
def application(environ,start_response):
    start_response('200 OK',[('Content-Type','application/json')]);return [json.dumps(checks).encode()]
''' % (str(secret), str(secret))
            version = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files={'index.html':HTML,'app.py':app}, model='test')
            try:
                url = self.start_checked(version, directory)
                self.assertEqual(httpx.get(url+'probe', trust_env=False).json(), dict.fromkeys(['read','write','network','fork'],'blocked'))
                self.assertEqual(secret.read_text(),'not-for-generated-app')
            finally:
                stop_unlocked(self.startup.pk)

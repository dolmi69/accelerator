"""Backend generation contracts and real isolated SQLite persistence."""
import base64
import io
import json
from pathlib import Path
import socket
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
from founder.services.generated_worker import site_cookie
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
    headers = [('Content-Type', 'application/json')]
    if environ['PATH_INFO'] == '/api/session':
        headers.extend([
            ('Set-Cookie', 'lab_session=fixture; Path=/; SameSite=Lax'),
            ('Set-Cookie', 'sessionid=ignored; Path=/'),
        ])
    start_response('200 OK', headers)
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

    def test_edit_preserves_previous_server_and_changes_only_planned_html(self):
        source = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files=FILES, model='test')
        response = {'changes':[{'file':'index.html','op':'replace','old':'<h1>Записи</h1>','new':'<h1>Записи</h1><button>Добавить</button>'}],'report':REPORT}
        with patch('founder.services.backend_edits.generate_code', return_value=CodeResult(json.dumps(response), 'test', 10, 20)) as api:
            result = generate_backend('Добавь кнопку', source=source, context={'edit_files':['index.html']})
        payload = json.loads(api.call_args.args[1][0]['content'])
        self.assertEqual(payload['previous_files'], {'index.html': HTML})
        self.assertEqual(payload['read_only_excerpts']['app.py'], APP)
        self.assertEqual(result.backend_files['app.py'], APP)
        self.assertIn('<button>Добавить</button>', result.text)

    def test_traversal_syntax_secrets_and_missing_server_rejected(self):
        for files in ({**FILES, '../.env': 'x'}, {**FILES, 'app.py': 'broken syntax :'}, {'index.html': HTML}):
            with self.assertRaises(QwenOutputError):
                validate_files(files)
        with patch.dict('os.environ', {'OFOX_API_KEY': 'unit-test-secret'}):
            with self.assertRaises(QwenOutputError):
                validate_files({**FILES, 'app.py': APP + '\n# unit-test-secret'})

    def test_failed_generation_preserves_version(self):
        source = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files=FILES, model='test')
        with patch('founder.services.backend_edits.generate_code', return_value=CodeResult('{}', 'test', 10, 20)):
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]), {'prompt': 'Измени сайт', 'source_version': source.pk})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.startup.lab_versions.count(), 1)
        source.refresh_from_db()
        self.assertEqual(source.backend_files, FILES)

    def test_export_contains_model_code_no_db(self):
        version = SimpleNamespace(backend_files=FILES)
        with ZipFile(export_backend(version)) as archive:
            self.assertEqual(archive.read('site/app.py').decode(), APP)
            worker_source = Path(__file__).with_name('services') / 'generated_worker.py'
            self.assertEqual(archive.read('site/start.py'), worker_source.read_bytes())
            self.assertFalse(any('.sqlite' in name or '.env' in name for name in archive.namelist()))

    @override_settings(LAB_BACKEND_RUNTIME_ENABLED=True, ALLOWED_HOSTS=['127.0.0.1', 'demo.example'])
    def test_generated_site_can_open_in_its_own_tab(self):
        version = LabSiteVersion.objects.create(startup=self.startup, kind='generated',
            html=HTML, backend_files=FILES, model='test')
        lab_url = reverse('lab', args=[self.startup.pk])
        run_url = reverse('lab_run', args=[self.startup.pk, version.pk])
        page = self.client.get(lab_url, HTTP_HOST='127.0.0.1:8000')
        self.assertContains(page, 'formtarget="_blank" data-lab-open-site', count=1)
        self.assertContains(page, 'name="preview" value="1"')
        self.assertContains(page, f'action="{run_url}"')
        host = f'lab-project-{self.startup.pk}.localhost'
        self.assertContains(page, f'data-runtime-host="{host}"')
        with patch('founder.lab_views.start_runtime', return_value=f'http://{host}:9000/'):
            response = self.client.post(run_url, {'preview': '1'}, HTTP_HOST='127.0.0.1:8000')
        self.assertRedirects(response, f'http://{host}:9000/', fetch_redirect_response=False)
        self.assertNotContains(self.client.get(lab_url, HTTP_HOST='demo.example'), 'data-lab-open-site')

    def test_preview_session_sets_and_expires_partitioned_cookies(self):
        for max_age in (3600, 0):
            value = f'lab_session=token; Path=/; SameSite=Lax; Max-Age={max_age}; HttpOnly'
            self.assertEqual(site_cookie(value, partitioned=True),
                f'lab_session=token; Path=/; Max-Age={max_age}; HttpOnly; SameSite=None; Secure; Partitioned')
            self.assertEqual(site_cookie(value),
                f'lab_session=token; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax')
        self.assertIsNone(site_cookie('lab_session=token; Domain=localhost', partitioned=True))
        self.assertIsNone(site_cookie('sessionid=token', partitioned=True))

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
                self.assertEqual(urlsplit(url).hostname, f'lab-project-{self.startup.pk}.localhost')
                parsed = urlsplit(url)
                local_api = f'http://127.0.0.1:{parsed.port}/api/items'
                headers = {'Host': parsed.netloc, 'Origin': url.rstrip('/')}
                with httpx.Client(trust_env=False) as client:
                    self.assertEqual(client.post(local_api, headers={'Host': parsed.netloc}, json={'name': 'persisted'}).status_code, 403)
                    reply = client.post(local_api, headers=headers, json={'name': 'persisted'})
                    self.assertEqual(reply.json(), [['persisted']])
                    self.assertIn("connect-src 'self'", reply.headers['Content-Security-Policy'])
                    self.assertEqual(client.get(local_api, headers={'Host':'evil.example'}).status_code, 403)
                response = self.runtime_get(url, 'api/session')
                self.assertEqual(response.headers.get_list('Set-Cookie'), [
                    'lab_session=fixture; Path=/; HttpOnly; SameSite=None; Secure; Partitioned',
                ])
                with socket.create_connection(('127.0.0.1', parsed.port), timeout=2) as incomplete:
                    incomplete.sendall(b'GET / HTTP/1.1\r\n')
                    self.assertEqual(self.runtime_get(url, 'api/items', timeout=2).json(), [['persisted']])
                stop_unlocked(self.startup.pk)
                url = self.start_checked(version, directory)
                self.assertEqual(self.runtime_get(url, 'api/items').json(), [['persisted']])
                child = LabSiteVersion.objects.create(startup=self.startup, source=version, kind='generated', html=HTML, backend_files=FILES, model='test')
                url = start(child, launcher_origin='http://127.0.0.1:8000')
                self.assertEqual(self.runtime_get(url, 'api/items').json(), [['persisted']])
                skipped = LabSiteVersion.objects.create(startup=self.startup, source=child, kind='generated', html=HTML, backend_files=FILES, model='manual-editor')
                latest = LabSiteVersion.objects.create(startup=self.startup, source=skipped, kind='generated', html=HTML, backend_files=FILES, model='manual-editor')
                url = start(latest, launcher_origin='http://127.0.0.1:8000')
                self.assertEqual(self.runtime_get(url, 'api/items').json(), [['persisted']])
                parent_db = Path(directory)/'.runtime/lab-backends'/str(self.startup.pk)/'generated-projects'/str(version.pk)/'data/data.sqlite3'
                with sqlite3.connect(parent_db) as db:
                    self.assertEqual(db.execute('SELECT name FROM items').fetchall(), [('persisted',)])
            finally:
                stop_unlocked(self.startup.pk)

    def runtime_get(self, url, path, *, timeout=5):
        parsed = urlsplit(url)
        return httpx.get(f'http://127.0.0.1:{parsed.port}/{path}', headers={'Host': parsed.netloc},
            timeout=timeout, trust_env=False)

    @skipUnless(available(), 'macOS isolated runtime')
    @override_settings(LAB_BACKEND_RUNTIME_ENABLED=True)
    def test_preview_accepts_and_serves_five_mib_uploads(self):
        app = """
import base64
import json

def application(environ, start_response):
    if environ['PATH_INFO'] == '/api/oversize':
        picture = b'x' * (6 * 1024 * 1024 + 1)
    else:
        picture = base64.b64decode(json.load(environ['wsgi.input'])['photo'])
    start_response('200 OK', [('Content-Type', 'image/png')])
    return [picture]
"""
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            version = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML,
                backend_files={'index.html': HTML, 'app.py': app}, model='test')
            try:
                url = self.start_checked(version, directory)
                state_file = Path(directory)/'.runtime/lab-backends'/str(self.startup.pk)/'generated-state.json'
                state = json.loads(state_file.read_text())
                state_file.write_text(json.dumps({**state, 'worker_revision': 'old'}))
                url = self.start_checked(version, directory)
                self.assertNotEqual(json.loads(state_file.read_text())['pid'], state['pid'])
                parsed = urlsplit(url)
                photo = b'x' * (5 * 1024 * 1024)
                with httpx.Client(trust_env=False, base_url=f'http://127.0.0.1:{parsed.port}',
                        headers={'Host': parsed.netloc, 'Origin': url.rstrip('/')}) as client:
                    page = client.get('/')
                    csp = page.headers['Content-Security-Policy']
                    self.assertIn("img-src 'self' data: blob:;", csp)
                    self.assertIn("script-src 'self' 'unsafe-inline';", csp)
                    response = client.post('/api/photo', json={'photo': base64.b64encode(photo).decode('ascii')})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, photo)
                    self.assertEqual(client.get('/api/oversize').status_code, 500)
                with socket.create_connection(('127.0.0.1', parsed.port), timeout=3) as request:
                    request.sendall((f'POST /api/photo HTTP/1.1\r\nHost: {parsed.netloc}\r\n'
                                     f'Origin: {url.rstrip("/")}\r\nContent-Length: 8388609\r\n\r\n').encode())
                    with request.makefile('rb') as response:
                        self.assertIn(b'413', response.readline())
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
                self.assertEqual(self.runtime_get(url, 'probe').json(), dict.fromkeys(['read','write','network','fork'],'blocked'))
                self.assertEqual(secret.read_text(),'not-for-generated-app')
            finally:
                stop_unlocked(self.startup.pk)

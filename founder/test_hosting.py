"""Releases, guest access and real isolated persistence, without paid AI calls."""
import json
import os
from pathlib import Path
import tempfile
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from founder.models import User, StartupProfile, LabSiteVersion, HostedSite, ProjectMember
from founder.services import hosting_runtime
from founder.services.django_builder import BuilderError
from founder.services.generated_runtime import available
from founder.test_generated_backend import FILES, HTML
from founder.hosting import site_url, base_url


@override_settings(DEBUG=True, BRUNOHOST_BASE_URL='http://localhost:8000', BRUNOHOST_ENGINE='disabled')
class HostingTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(BRUNOHOST_ROOT=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create_user(username='hosting-owner')
        self.startup = StartupProfile.objects.create(owner=self.user, name='Hosted demo')
        self.version = LabSiteVersion.objects.create(startup=self.startup, prompt='test', html=HTML, model='test')
        self.url = reverse('lab_hosting', args=[self.startup.pk])
        self.client.force_login(self.user)

    def publish(self, version=None, **extra):
        return self.client.post(self.url, {'version': (version or self.version).pk, **extra})

    def guest(self, path='/', method='get', **extra):
        site = self.startup.hosted_site
        host = f'{site.slug}.localhost:8000'
        return getattr(Client(), method)(path, HTTP_HOST=host, **extra)

    def test_static_publish_is_guest_accessible_on_separate_origin(self):
        self.assertEqual(self.publish().status_code, 302)
        site = self.startup.hosted_site
        self.assertRegex(site.slug, '^h[a-z0-9]{13}$')
        response = self.guest()
        self.assertContains(response, '<h1>Записи</h1>', html=True)
        self.assertFalse(response.cookies)
        self.assertIn("connect-src 'self'", response['Content-Security-Policy'])
        self.assertEqual(site_url(site), f'http://{site.slug}.localhost:8000/')
        self.assertFalse(hasattr(response.wsgi_request, 'user'))
        self.assertFalse(hasattr(response.wsgi_request, 'session'))

    def test_drafts_do_not_publish_and_update_keeps_address(self):
        self.publish()
        original = self.startup.hosted_site.slug
        draft = LabSiteVersion.objects.create(startup=self.startup, html=HTML.replace('Записи', 'Новая версия'), prompt='edit', model='test')
        self.assertNotContains(self.guest(), 'Новая версия')
        self.publish(draft)
        self.assertContains(self.guest(), 'Новая версия')
        self.startup.hosted_site.refresh_from_db()
        self.assertEqual(self.startup.hosted_site.slug, original)

    def test_unpublish_and_republish_keep_slug(self):
        self.publish()
        slug = self.startup.hosted_site.slug
        self.publish(action='stop')
        self.assertEqual(self.guest().status_code, 404)
        self.publish()
        self.startup.hosted_site.refresh_from_db()
        self.assertEqual(self.startup.hosted_site.slug, slug)
        self.assertEqual(self.guest().status_code, 200)

    def test_foreign_project_and_viewer_cannot_publish(self):
        foreign = User.objects.create_user(username='hosting-foreign', email='foreign@example.test')
        self.client.force_login(foreign)
        self.assertEqual(self.publish().status_code, 404)
        ProjectMember.objects.create(startup=self.startup, user=foreign, role=ProjectMember.Role.VIEWER, status=ProjectMember.Status.ACTIVE)
        self.assertEqual(self.publish().status_code, 404)
        self.assertFalse(HostedSite.objects.exists())

    def test_wrong_version_and_invalid_uuid_rejected(self):
        other = StartupProfile.objects.create(owner=self.user, name='Other')
        version = LabSiteVersion.objects.create(startup=other, html=HTML, prompt='test', model='test')
        self.assertEqual(self.publish(version).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'version': 'invalid'}).status_code, 404)
        self.assertFalse(HostedSite.objects.exists())

    def test_management_requires_post_login_and_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.publish().status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, {'version': self.version.pk}).status_code, 403)
        self.assertFalse(HostedSite.objects.exists())

    def test_unknown_host_never_opens_platform(self):
        for host in ['habcdefghijklm0.localhost:8000', 'unknown.localhost:8000', 'x.y.localhost:8000']:
            response = Client().get('/admin/', HTTP_HOST=host)
            self.assertEqual(response.status_code, 404)
            self.assertFalse(response.cookies)
        self.publish()
        self.assertEqual(self.guest('/admin/').status_code, 404)
        self.assertEqual(self.guest('/auth/csrf/').status_code, 404)
        self.assertEqual(self.guest('/__lab_health').status_code, 404)

    def test_unsupported_backend_does_not_silently_publish_html(self):
        self.version.kind = 'django'
        self.version.save()
        self.publish()
        self.assertFalse(HostedSite.objects.exists())
        self.version.kind = 'generated'
        self.version.backend_files = FILES
        self.version.save()
        self.publish()
        self.assertFalse(HostedSite.objects.exists())

    def test_no_hosting_domain_leaves_release_unpublished(self):
        with override_settings(BRUNOHOST_BASE_URL=''):
            self.publish()
        self.assertFalse(HostedSite.objects.exists())

    def test_hosting_url_validates_https_and_no_credentials_or_path(self):
        for value in ['https://user:password@hosting.test', 'https://hosting.test/path', 'http://hosting.test', 'https://hosting.test:bad']:
            with override_settings(BRUNOHOST_BASE_URL=value):
                self.assertIsNone(base_url())

    def test_head_body_and_static_methods(self):
        self.publish()
        response = self.guest(method='head')
        self.assertEqual(response.content, b'')
        self.assertEqual(int(response['Content-Length']), len(HTML.encode()))
        self.assertEqual(self.guest(method='post', HTTP_ORIGIN=site_url(self.startup.hosted_site).rstrip('/')).status_code, 405)
        self.assertEqual(self.guest(method='post', HTTP_ORIGIN='http://localhost:8000').status_code, 403)
        self.assertEqual(self.guest('/assets/../app.py').status_code, 404)

    def test_disabled_owner_and_deleted_version_are_not_served(self):
        self.publish()
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.guest().status_code, 404)
        self.user.is_active = True
        self.user.save()
        self.version.delete()
        self.assertEqual(self.guest().status_code, 404)

    def test_lab_shows_compact_hosting_controls(self):
        page = self.client.get(reverse('lab', args=[self.startup.pk]))
        self.assertContains(page, 'Опубликовать сайт')
        self.publish()
        page = self.client.get(reverse('lab', args=[self.startup.pk]))
        self.assertContains(page, 'Эта версия опубликована')
        self.assertContains(page, self.startup.hosted_site.slug)
        self.assertContains(page, 'Снять с публикации')

    def test_failed_backend_update_preserves_previous_release(self):
        self.publish()
        old = self.startup.hosted_site.version_id
        generated = LabSiteVersion.objects.create(startup=self.startup, kind='generated', backend_files=FILES, html=HTML, prompt='server', model='test')
        with patch.object(hosting_runtime, 'enabled', return_value=True), patch.object(hosting_runtime, 'ensure', side_effect=BuilderError('Failed')):
            self.publish(generated)
        self.startup.hosted_site.refresh_from_db()
        self.assertEqual(self.startup.hosted_site.version_id, old)
        self.assertContains(self.guest(), 'Записи')

    def test_platform_credentials_are_not_publishable(self):
        with patch.dict(os.environ, {'OFOX_API_KEY': 'test-platform-secret-not-real'}):
            self.version.html += 'test-platform-secret-not-real'
            self.version.save()
            self.publish()
        self.assertFalse(HostedSite.objects.exists())


@skipUnless(available(), 'Requires macOS OS sandbox')
@override_settings(DEBUG=True, BRUNOHOST_BASE_URL='http://localhost:8000', BRUNOHOST_ENGINE='sandbox')
class HostedRuntimeTests(TestCase):
    setUp = HostingTests.setUp
    publish = HostingTests.publish
    guest = HostingTests.guest

    def test_proxy_filters_cookies_and_auth_and_os_blocks_platform_files(self):
        app = '''import json, os
from pathlib import Path
def application(environ, start_response):
    try:
        Path(PLATFORM_ENV).read_text()
        blocked = False
    except OSError:
        blocked = True
    start_response('200 OK', [('Content-Type','application/json'),
        ('Set-Cookie','sessionid=stolen; Path=/'),
        ('Set-Cookie','lab_other=bad; Domain=localhost; Path=/'),
        ('Set-Cookie','lab_session=visitor; Path=/; Max-Age=3600')])
    return [json.dumps({'blocked':blocked,'cookie':environ.get('HTTP_COOKIE'),
        'auth':environ.get('HTTP_AUTHORIZATION'), 'secret_env':os.getenv('OFOX_API_KEY')}).encode()]
'''
        app = 'PLATFORM_ENV = ' + repr(str(settings.BASE_DIR / '.env')) + '\n' + app
        self.version.kind = 'generated'
        self.version.backend_files = {**FILES, 'app.py': app}
        self.version.save()
        self.publish()
        site = HostedSite.objects.get(startup=self.startup)
        self.addCleanup(hosting_runtime.stop, site)
        response = self.guest('/api/security', HTTP_COOKIE='sessionid=platform; csrftoken=secret; lab_session=visitor', HTTP_AUTHORIZATION='Bearer platform-token')
        self.assertEqual(response.json(), {'blocked':True, 'cookie':'lab_session=visitor', 'auth':None, 'secret_env':None})
        self.assertEqual(set(response.cookies), {'lab_session'})
        self.assertTrue(response.cookies['lab_session']['httponly'])
        self.assertEqual(response.cookies['lab_session']['samesite'], 'Lax')
        self.assertFalse(response.cookies['lab_session']['domain'])

    def test_failed_server_update_restores_database_and_previous_code(self):
        self.version.kind = 'generated'
        self.version.backend_files = FILES
        self.version.save()
        self.publish()
        site = HostedSite.objects.get(startup=self.startup)
        self.addCleanup(hosting_runtime.stop, site)
        origin = site_url(site).rstrip('/')
        self.guest('/api/items', method='post', data=json.dumps({'name':'Keep me'}), content_type='application/json', HTTP_ORIGIN=origin)
        broken = '''import os, sqlite3
with sqlite3.connect(os.environ['LAB_DATABASE']) as db:
    db.execute('DELETE FROM items')
raise RuntimeError('broken update')
def application(environ, start_response):
    return []
'''
        update = LabSiteVersion.objects.create(startup=self.startup, kind='generated', backend_files={**FILES, 'app.py':broken}, html=HTML, prompt='broken', model='test')
        self.publish(update)
        site.refresh_from_db()
        self.assertEqual(site.version_id, self.version.pk)
        self.assertEqual(self.guest('/api/items').json(), [['Keep me']])

    def test_real_guest_api_persists_updates_and_blocks_platform_reads(self):
        self.version.kind = 'generated'
        self.version.backend_files = FILES
        self.version.save()
        response = self.publish()
        self.assertEqual(response.status_code, 302)
        site = HostedSite.objects.get(startup=self.startup)
        self.addCleanup(hosting_runtime.stop, site)
        self.assertEqual(self.guest('/api/items').json(), [])
        origin = site_url(site).rstrip('/')
        response = self.guest('/api/items', method='post', data=json.dumps({'name':'Visitor record'}), content_type='application/json', HTTP_ORIGIN=origin)
        self.assertEqual(response.json(), [['Visitor record']])
        self.assertEqual(self.guest('/api/items', method='post', data='{}', content_type='application/json', HTTP_ORIGIN='http://attacker.localhost:8000').status_code, 403)
        updated = LabSiteVersion.objects.create(startup=self.startup, kind='generated', backend_files={**FILES, 'index.html': HTML.replace('Записи', 'Обновлено')}, html=HTML, prompt='update', model='test')
        self.publish(updated)
        self.assertEqual(self.guest('/api/items').json(), [['Visitor record']])
        self.assertContains(self.guest(), 'Обновлено')
        hosting_runtime.stop(site)
        self.assertEqual(self.guest('/api/items').json(), [['Visitor record']])
        self.publish(action='stop')
        self.assertEqual(self.guest().status_code, 404)


@override_settings(DEBUG=True, BRUNOHOST_BASE_URL='http://localhost:8000', BRUNOHOST_ENGINE='disabled')
class HostingLifecycleTests(TestCase):
    setUp = HostingTests.setUp
    publish = HostingTests.publish

    def test_deleted_project_stops_worker_only_after_commit(self):
        self.publish()
        with patch.object(hosting_runtime, 'stop') as stop:
            with self.captureOnCommitCallbacks(execute=True):
                self.startup.delete()
                stop.assert_not_called()
            stop.assert_called_once()


class HostedTransportTests(TestCase):
    @skipUnless(os.name == 'posix', 'Linux executor command')
    def test_pending_recovery_copy_survives_launcher_restart(self):
        import sqlite3
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root, override_settings(BRUNOHOST_ROOT=Path(root), BRUNOHOST_ENGINE='docker'):
            site = SimpleNamespace(slug='h' + 'b' * 13, version_id='recovered', version=SimpleNamespace(backend_files=FILES))
            folder = hosting_runtime.directory(site)
            data = folder / 'app' / 'data'
            data.mkdir(parents=True)
            database = data / 'data.sqlite3'
            backup = folder / 'start-backup.sqlite3'
            for file, value in [(database, 'Failed import'), (backup, 'Recovered visitor')]:
                with sqlite3.connect(file) as connection:
                    connection.execute('CREATE TABLE items(name TEXT)')
                    connection.execute('INSERT INTO items VALUES (?)', [value])
            with patch.object(hosting_runtime, 'enabled', return_value=True), \
                    patch.object(hosting_runtime, 'healthy', side_effect=[False, True]), \
                    patch('founder.services.hosting_runtime.subprocess.run', return_value=SimpleNamespace(returncode=0)):
                hosting_runtime.ensure(site)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute('SELECT name FROM items').fetchall(), [('Recovered visitor',)])
            self.assertFalse(backup.exists())

    @skipUnless(os.name == 'posix', 'POSIX links required')
    def test_privileged_backup_rejects_links_and_special_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / 'data.sqlite3'
            target = root / 'platform.sqlite3'
            target.write_bytes(b'untouched-platform-data')
            database.symlink_to(target)
            with self.assertRaises(BuilderError):
                hosting_runtime._checked_database(database)
            database.unlink()
            os.link(target, database)
            with self.assertRaises(BuilderError):
                hosting_runtime._checked_database(database)
            database.unlink()
            database.write_bytes(b'own-database')
            sidecar = root / 'data.sqlite3-wal'
            sidecar.symlink_to(target)
            with self.assertRaises(BuilderError):
                hosting_runtime._checked_database(database)
            sidecar.unlink()
            os.mkfifo(sidecar)
            with self.assertRaises(BuilderError):
                hosting_runtime._checked_database(database)
            self.assertEqual(target.read_bytes(), b'untouched-platform-data')

    @skipUnless(os.name == 'posix', 'Linux executor command')
    def test_docker_command_has_no_network_or_platform_mounts(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root, override_settings(BRUNOHOST_ROOT=Path(root), BRUNOHOST_ENGINE='docker'):
            site = SimpleNamespace(slug='h' + 'a' * 13, version_id='test-version',
                                   version=SimpleNamespace(backend_files=FILES))
            with patch.object(hosting_runtime, 'enabled', return_value=True), \
                    patch.object(hosting_runtime, 'healthy', side_effect=[False, True]), \
                    patch('founder.services.hosting_runtime.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
                state = hosting_runtime.ensure(site)
            command = run.call_args.args[0]
            self.assertIn('--network', command)
            self.assertEqual(command[command.index('--network') + 1], 'none')
            for flag in ['--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--memory=256m', '--pids-limit=32']:
                self.assertIn(flag, command)
            self.assertNotIn('--publish', command)
            self.assertNotIn('--env-file', command)
            self.assertNotIn('/var/run/docker.sock', ' '.join(command))
            self.assertNotIn(str(settings.BASE_DIR / '.env'), ' '.join(command))
            self.assertTrue(state['socket'].endswith('/socket/http.sock'))

    @skipUnless(os.name == 'posix', 'Unix sockets required')
    def test_trusted_worker_unix_socket_transport_without_docker(self):
        import shutil
        import subprocess
        import sys
        import time
        import httpx
        with tempfile.TemporaryDirectory(prefix='bh-') as folder:
            root = Path(folder)
            for name, content in FILES.items():
                (root / name).write_text(content)
            shutil.copyfile(Path(hosting_runtime.__file__).with_name('generated_worker.py'), root / 'start.py')
            uds = str(root / 'http.sock')
            # These are fixed test fixtures, never user/model-supplied code.
            process = subprocess.Popen([sys.executable, '-I', '-S', str(root / 'start.py'), '--socket', uds,
                '--host', 'worker', '--version', 'test', '--project', 'test'], cwd=root,
                env={'PATH':'/usr/bin:/bin'}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                state = {'socket':uds, 'host':'worker:8099'}
                with hosting_runtime.client(state) as connection:
                    for _ in range(50):
                        try:
                            response = connection.get('http://worker:8099/__lab_health', headers={'Host':'worker:8099'})
                            if response.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(.05)
                    else:
                        self.fail('Unix-socket worker did not start')
                    self.assertEqual(response.json()['project_id'], 'test')
                    response = connection.post('http://worker:8099/api/items', headers={'Host':'worker:8099','Origin':'http://worker:8099'}, json={'name':'UDS works'})
                    self.assertEqual(response.json(), [['UDS works']])
            finally:
                process.terminate()
                process.wait(timeout=5)

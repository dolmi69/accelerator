"""One live prototype per account, safe project switching and stale requests."""
import json
import signal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from founder.models import LabSiteVersion, StartupProfile, User
from founder.services import django_builder as builder


class RuntimeSwitchTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = override_settings(BASE_DIR=Path(self.temp.name), LAB_BACKEND_RUNTIME_ENABLED=True)
        config.enable()
        self.addCleanup(config.disable)
        self.owner = User.objects.create_user(username='runtime-owner', email='runtime@example.test')
        self.other = User.objects.create_user(username='runtime-other', email='other-runtime@example.test')
        self.first = StartupProfile.objects.create(owner=self.owner, name='First')
        self.second = StartupProfile.objects.create(owner=self.owner, name='Second')
        self.foreign = StartupProfile.objects.create(owner=self.other, name='Foreign')
        self.alive, self.killed = set(), []
        health = patch.object(builder, '_healthy', side_effect=lambda state, _: bool(state and state['pid'] in self.alive))
        health.start()
        self.addCleanup(health.stop)
        kill = patch.object(builder.os, 'kill', side_effect=self.kill)
        kill.start()
        self.addCleanup(kill.stop)
        self.client.force_login(self.owner)

    def kill(self, pid, sig):
        if pid not in self.alive:
            raise ProcessLookupError()
        if sig == signal.SIGTERM:
            self.alive.remove(pid)
            self.killed.append(pid)

    def running(self, startup, version=None, build_hash='test'):
        directory = builder._directory(startup.pk)
        project = directory / 'project'
        project.mkdir(parents=True)
        (project / 'db.sqlite3').write_bytes(b'users and messages')
        pid = 20000 + len(self.alive)
        self.alive.add(pid)
        state = {'pid': pid, 'port': 9000 + len(self.alive),
                 'version_id': str(version.pk) if version else 'test', 'build_hash': build_hash}
        (directory / 'runtime.json').write_text(json.dumps(state))
        return directory, state

    def test_switch_stops_previous_site_and_preserves_data_and_other_accounts(self):
        directory, first = self.running(self.first)
        _, foreign = self.running(self.foreign)
        selected = builder.select_runtime_project(self.second)
        self.assertEqual(selected['project_id'], str(self.second.pk))
        self.assertEqual(self.killed, [first['pid']])
        self.assertFalse((directory / 'runtime.json').exists())
        self.assertEqual((directory / 'project/db.sqlite3').read_bytes(), b'users and messages')
        self.assertIn(foreign['pid'], self.alive)

    def test_same_project_keeps_selection_and_live_site(self):
        selected = builder.select_runtime_project(self.first)
        _, state = self.running(self.first)
        again = builder.select_runtime_project(self.first)
        self.assertEqual(again, selected)
        self.assertIn(state['pid'], self.alive)
        self.assertEqual(self.killed, [])

    def test_late_run_cannot_restart_project_after_switch(self):
        selected = builder.select_runtime_project(self.first)
        version = LabSiteVersion.objects.create(startup=self.first, prompt='Site', kind='django',
            html='<html><body>First</body></html>', model='local')
        builder.select_runtime_project(self.second)
        with patch.object(builder, 'project_files', return_value={}), patch.object(builder.subprocess, 'Popen') as spawn:
            with self.assertRaises(builder.RuntimeSelectionChanged):
                builder.start_runtime(version, selection_token=selected['token'])
            spawn.assert_not_called()

    def test_cached_launch_also_cleans_previous_running_projects(self):
        import hashlib
        version = LabSiteVersion.objects.create(startup=self.second, prompt='Site', kind='django', html='site', model='local')
        directory, first = self.running(self.first)
        _, current = self.running(self.second, version, hashlib.sha256().hexdigest())
        with patch.object(builder, 'project_files', return_value={}), patch.object(builder.subprocess, 'Popen') as spawn:
            url = builder.start_runtime(version)
        self.assertIn(str(current['port']), url)
        spawn.assert_not_called()
        self.assertFalse((directory / 'runtime.json').exists())
        self.assertIn(current['pid'], self.alive)
        self.assertEqual(self.killed, [first['pid']])

    def test_selection_is_owned_local_post_and_csrf_protected(self):
        route = reverse('lab_activate', args=[self.second.pk])
        self.assertEqual(self.client.get(route, HTTP_HOST='localhost:8000').status_code, 405)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        self.assertEqual(protected.post(route, HTTP_HOST='localhost:8000').status_code, 403)
        response = self.client.post(route, HTTP_HOST='localhost:8000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['project_id'], str(self.second.pk))
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(self.client.post(reverse('lab_activate', args=[self.foreign.pk]),
            HTTP_HOST='localhost:8000').status_code, 404)
        with override_settings(ALLOWED_HOSTS=['example.test']):
            self.assertEqual(self.client.post(route, HTTP_HOST='example.test').status_code, 403)

    def test_dashboard_of_another_project_selects_it_via_post_not_get(self):
        directory, first = self.running(self.first)
        page = self.client.get(reverse('dashboard', args=[self.second.pk]), HTTP_HOST='localhost:8000')
        self.assertContains(page, 'id="lab-workspace-session"')
        self.assertContains(page, reverse('lab_activate', args=[self.second.pk]))
        self.assertIn(first['pid'], self.alive)
        self.client.post(reverse('lab_activate', args=[self.second.pk]), HTTP_HOST='localhost:8000')
        self.assertFalse((directory / 'runtime.json').exists())

    def test_run_forwards_selection_token_and_reports_conflict(self):
        version = LabSiteVersion.objects.create(startup=self.first, prompt='Site', kind='django', html='site', model='local')
        with patch('founder.lab_views.start_runtime', side_effect=builder.RuntimeSelectionChanged('Проект изменился')) as run:
            response = self.client.post(reverse('lab_run', args=[self.first.pk, version.pk]),
                {'selection_token': 'old-token'}, HTTP_HOST='localhost:8000', HTTP_ACCEPT='application/json')
        self.assertEqual(run.call_args.kwargs['selection_token'], 'old-token')
        self.assertEqual(response.status_code, 409)

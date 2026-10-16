"""Publication access and isolated execution, without backend telemetry."""
from pathlib import Path
import tempfile
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from django.test import TestCase, override_settings
from django.urls import reverse
from founder.models import LabPublication, LabSiteVersion, LabTestSession, StartupProfile, User
from founder.services.generated_runtime import available, start, stop_unlocked
from founder.services.project_cards import get_card, save_card, card_values
from founder.test_generated_backend import FILES, HTML


@override_settings(LAB_BACKEND_RUNTIME_ENABLED=True)
class BackendPublicationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='backend-publisher', email='publisher@backend.test')
        self.visitor = User.objects.create_user(username='backend-visitor', email='visitor@backend.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Backend')
        self.version = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files=FILES, model='test')
        card = get_card(self.startup)
        save_card(card, {**card_values(card), 'name': 'Backend', 'summary': 'Public description'}, card.revision, publish=True)
        self.publication = LabPublication.objects.create(startup=self.startup, version=self.version, visibility='public')
        self.client.force_login(self.visitor)

    def url(self, name, version=None):
        args = [self.startup.pk]
        if name in {'lab_public_preview', 'lab_public_backend_start'}:
            args.append((version or self.version).pk)
        return reverse(name, args=args)

    def test_card_uses_trusted_launcher_not_model_html_and_no_telemetry(self):
        card = self.client.get(self.url('card_detail'))
        self.assertContains(card, 'Открыть рабочий сайт')
        self.assertContains(card, 'allow-same-origin allow-forms')
        launch = self.client.get(self.url('lab_public_preview'), HTTP_HOST='127.0.0.1:8000')
        self.assertContains(launch, 'Загружаем сайт…')
        self.assertNotContains(launch, 'Для входа создайте аккаунт')
        self.assertNotContains(launch, '<h1>Записи</h1>')
        self.assertNotContains(launch, 'lab-tracker.js')
        self.assertFalse(LabTestSession.objects.exists())
        self.assertRedirects(self.client.get(self.url('lab_trial')), self.url('lab_public_preview'), fetch_redirect_response=False)
        self.assertEqual(self.client.post(self.url('lab_test_start'), {'version': self.version.pk}).status_code, 409)

    def test_private_or_unpublished_card_blocks_visitors(self):
        self.publication.visibility = 'private'
        self.publication.save()
        self.assertEqual(self.client.get(self.url('lab_public_preview')).status_code, 404)
        self.assertEqual(self.client.post(self.url('lab_public_backend_start')).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url('lab_public_preview')).status_code, 200)

    @patch('founder.services.generated_runtime.start')
    def test_launch_checks_version_method_and_local_host(self, launch):
        launch.return_value = f'http://lab-{self.version.pk}.localhost:12345/'
        url = self.url('lab_public_backend_start')
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, HTTP_HOST='127.0.0.1:8000')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(launch.call_args.kwargs['publication'])
        self.assertFalse(LabTestSession.objects.exists())
        draft = LabSiteVersion.objects.create(startup=self.startup, kind='generated', html=HTML, backend_files=FILES, model='test')
        self.assertEqual(self.client.post(self.url('lab_public_backend_start', draft), HTTP_HOST='127.0.0.1:8000').status_code, 404)
        self.assertEqual(launch.call_count, 1)

    @override_settings(ALLOWED_HOSTS=['testserver', 'deployed.example'])
    @patch('founder.services.generated_runtime.start')
    def test_remote_deployment_never_sends_visitors_to_their_localhost(self, launch):
        response = self.client.post(self.url('lab_public_backend_start'), HTTP_HOST='deployed.example')
        self.assertEqual(response.status_code, 503)
        launch.assert_not_called()

    @patch('founder.services.generated_runtime.stop_unlocked')
    def test_hiding_publication_stops_public_runtime_only(self, stop):
        self.client.force_login(self.owner)
        response = self.client.post(self.url('lab_publish'), {'action': 'hide'})
        self.assertEqual(response.status_code, 302)
        stop.assert_called_once_with(f'public-{self.startup.pk}')
        self.assertFalse(LabPublication.objects.exists())

    def test_owner_can_choose_generated_site_from_laboratory(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url('lab')), 'Добавить в карточку')
        response = self.client.post(self.url('lab_publish'), {'version': self.version.pk, 'visibility': 'private'})
        self.assertEqual(response.status_code, 302)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.version_id, self.version.pk)
        self.assertEqual(self.publication.visibility, 'private')

    @skipUnless(available(), 'macOS isolated runtime')
    def test_real_public_database_is_separate_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)), patch('founder.services.django_builder._select_project') as select:
            runtime_id = f'public-{self.startup.pk}'
            try:
                # Seed a private editor DB that must not reach the public app.
                private = Path(directory)/'.runtime/lab-backends'/str(self.startup.pk)/'generated-projects'/str(self.version.pk)/'data'
                private.mkdir(parents=True)
                (private/'data.sqlite3').write_bytes(b'private editor database')
                url = start(self.version, launcher_origin='http://127.0.0.1:8000', publication=True)
                parsed = urlsplit(url)
                self.assertEqual(parsed.hostname, f'lab-{self.version.pk}.localhost')
                internal = f'http://127.0.0.1:{parsed.port}/api/items'
                headers = {'Host': parsed.netloc, 'Origin': url.rstrip('/')}
                with httpx.Client(trust_env=False) as client:
                    self.assertEqual(client.get(internal, headers=headers).json(), [])
                    self.assertEqual(client.post(internal, headers=headers, json={'name': 'visitor item'}).json(), [['visitor item']])
                stop_unlocked(runtime_id)
                url = start(self.version, launcher_origin='http://127.0.0.1:8000', publication=True)
                parsed = urlsplit(url)
                response = httpx.get(f'http://127.0.0.1:{parsed.port}/api/items', headers={'Host': parsed.netloc}, trust_env=False)
                self.assertEqual(response.json(), [['visitor item']])
                self.assertEqual((private/'data.sqlite3').read_bytes(), b'private editor database')
                select.assert_not_called()
            finally:
                stop_unlocked(runtime_id)

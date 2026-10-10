from pathlib import Path
from tempfile import TemporaryDirectory
from django.conf import settings
from django.test import TestCase, override_settings
from .design import integrate_navigation


class AppDesignTests(TestCase):
    def test_home_has_one_isolated_page_and_server_allowed_routes(self):
        response = self.client.get('/')
        self.assertContains(response, 'sandbox="allow-scripts"')
        self.assertContains(response, 'lab-app-routes')
        self.assertNotContains(response, 'lab-module-header')
        self.assertContains(response, 'app-shell.js')

    def test_prototype_gets_real_links_inside_existing_card_without_template_execution(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'prototype.html').write_text('<html><head></head><body><div class="container"><h1>{{ user.password }}</h1><button>Calculate</button></div></body></html>')
            with override_settings(BASE_DIR=path):
                response = self.client.get('/prototype/')
        self.assertContains(response, '<div class="container"><div class="lab-app-navigation">')
        self.assertContains(response, 'data-app-route="/login/"')
        self.assertContains(response, '{{ user.password }}')
        self.assertContains(response, 'data-app-bridge')
        self.assertIn('sandbox allow-scripts', response['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin', response['Content-Security-Policy'])

    def test_disabled_registration_not_in_server_allowed_routes(self):
        with override_settings(SITE={**settings.SITE, 'modules':[]}):
            home = self.client.get('/')
            self.assertNotContains(home, '"/register/"')
            self.assertEqual(self.client.get('/register/').status_code, 404)

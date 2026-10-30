"""Temporary static hosting must remain opaque and bypass all platform auth."""
from django.test import Client, TestCase, override_settings
from founder.test_hosting import HostingTests
from founder.models import LabSiteVersion
from founder.hosting import site_url
from founder.services.builder_starter import build_document


@override_settings(DEBUG=True, BRUNOHOST_BASE_URL='http://localhost:8000', BRUNOHOST_PATH_MODE=True)
class PathHostingTests(TestCase):
    setUp = HostingTests.setUp
    publish = HostingTests.publish
    # Reuse setup only, avoiding tests for the separate-domain mode.
    def test_path_static_is_sandboxed_and_auth_is_never_loaded(self):
        self.publish()
        site = self.startup.hosted_site
        response = Client().get(f'/sites/{site.slug}/', HTTP_HOST='localhost:8000')
        self.assertEqual(response.status_code, 200)
        csp = response['Content-Security-Policy']
        self.assertIn('sandbox allow-scripts', csp)
        self.assertNotIn('allow-same-origin', csp)
        self.assertIn("connect-src 'none'", csp)
        self.assertIn("form-action 'none'", csp)
        self.assertFalse(hasattr(response.wsgi_request, 'session'))
        self.assertFalse(hasattr(response.wsgi_request, 'user'))
        self.assertFalse(response.cookies)
        self.assertContains(response, 'Сделано в BrunoLab')
        self.assertEqual(site_url(site), f'http://localhost:8000/sites/{site.slug}/')
        self.assertEqual(Client().get(f'/sites/{site.slug}/admin/', HTTP_HOST='localhost:8000').status_code, 404)
        self.assertEqual(Client().post(f'/sites/{site.slug}/', HTTP_HOST='localhost:8000').status_code, 405)

    def test_builder_pages_use_scoped_links_and_unknown_sites_stay_404(self):
        version = LabSiteVersion.objects.create(startup=self.startup, prompt='manual', model='visual-builder',
                                               html='<h1>Demo</h1>', builder_document=build_document())
        self.publish(version)
        slug = self.startup.hosted_site.slug
        guest = Client()
        response = guest.get(f'/sites/{slug}/', HTTP_HOST='localhost:8000')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'/sites/{slug}/features/')
        self.assertEqual(guest.get(f'/sites/{slug}/features/', HTTP_HOST='localhost:8000').status_code, 200)
        self.assertEqual(guest.get('/sites/h0000000000000/', HTTP_HOST='localhost:8000').status_code, 404)
        self.publish(action='stop')
        self.assertEqual(guest.get(f'/sites/{slug}/', HTTP_HOST='localhost:8000').status_code, 404)

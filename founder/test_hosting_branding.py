from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
from founder.hosting import add_branding

@override_settings(DEBUG=True, BRUNOHOST_PLATFORM_URL='')
class BrandingTests(SimpleTestCase):
    def test_api_and_assets_are_never_modified(self):
        for content_type,body in [('application/json',b'{"ok":true}'),('text/javascript',b'const n=1;'),('text/css',b'body{}')]:
            response=HttpResponse(body,content_type=content_type)
            self.assertIs(add_branding(response),response)
            self.assertEqual(response.content,body)

    def test_no_duplicate_and_local_destination(self):
        response=HttpResponse('<html><body>Page</body></html>',content_type='text/html')
        add_branding(response)
        first=response.content
        self.assertIn(b'http://127.0.0.1:8000/',first)
        add_branding(response)
        self.assertEqual(response.content,first)
        self.assertEqual(first.count(b'id="brunohost-branding"'),1)

    def test_error_and_redirect_responses_are_untouched(self):
        for status in [302,404,500]:
            response=HttpResponse('<html>Error</html>',status=status,content_type='text/html')
            add_branding(response)
            self.assertNotIn(b'brunohost-branding',response.content)

    @override_settings(DEBUG=False,TELEGRAM_MINI_APP_URL='https://brunolab.example/')
    def test_production_destination_comes_from_configuration(self):
        response=add_branding(HttpResponse('<html><body>Page</body></html>',content_type='text/html'))
        self.assertIn(b'href="https://brunolab.example/"',response.content)

    @override_settings(BRUNOHOST_PLATFORM_URL='javascript:alert(1)')
    def test_invalid_target_cannot_inject_a_script_url(self):
        response=add_branding(HttpResponse('<html><body>Page</body></html>',content_type='text/html'))
        self.assertNotIn(b'href="javascript:',response.content)

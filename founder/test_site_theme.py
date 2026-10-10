from django.test import SimpleTestCase, TestCase
from founder.blueprints.django_basic.core.design import theme_from_html, integrate_navigation
from founder.models import StartupProfile, LabSiteVersion, User
from founder.services.django_builder import project_files

HTML = '''<!doctype html><html><head><style>:root{--primary-color:#4f46e5;--text-main:#1f2937;--bg-gradient:linear-gradient(135deg,#667eea 0%,#764ba2 100%);--card-bg:rgba(255,255,255,0.95)}body{background:var(--bg-gradient);font-family:'Segoe UI',sans-serif}button{border-radius:12px}</style></head><body><div class="container"><h1>Calculator</h1><script>const untouched=1;</script></div></body></html>'''


class IntegrationTests(SimpleTestCase):
    def test_theme_matches_existing_calculator_and_rejects_css_payloads(self):
        tokens = theme_from_html(HTML)
        self.assertEqual(tokens['accent'], '#4f46e5')
        self.assertEqual(tokens['surface'], 'rgba(255,255,255,0.95)')
        self.assertEqual(tokens['background'], 'linear-gradient(135deg,#667eea 0%,#764ba2 100%)')
        hostile = theme_from_html('<style>:root{--primary-color:url(https://evil.test);--bg-gradient:url(https://evil.test)}body{font-family:x;background:url(https://evil.test)}</style>')
        self.assertNotIn('evil', str(hostile))

    def test_mount_inside_existing_card_and_explicit_header_slot(self):
        result = integrate_navigation(HTML, '<nav>Registration</nav>', '', '')
        self.assertIn('<div class="container"><nav>Registration</nav><h1>', result)
        self.assertIn('<script>const untouched=1;</script>', result)
        slot = '<html><head></head><body><header><div data-app-navigation></div></header></body></html>'
        mounted = integrate_navigation(slot, '<nav>Login</nav>', '', '')
        self.assertIn('<div data-app-navigation><nav>Login</nav></div>', mounted)
        tricky = '<html><head><script>const tag="</head>";</script></head><body>Demo</body></html>'
        self.assertIn('const tag="</head>";</script><style data-app-theme>', integrate_navigation(tricky, '<nav>Login</nav>', '', ''))


class ExportThemeTests(TestCase):
    def test_exports_shared_theme_and_navigation_without_changing_user_html(self):
        startup = StartupProfile.objects.create(owner=User.objects.create_user(username='theme'), name='Calculator')
        version = LabSiteVersion.objects.create(startup=startup, prompt='Demo', kind='django', html=HTML)
        files = project_files(version)
        self.assertIn('--app-accent:#4f46e5', files['static/site-theme.css'].decode())
        self.assertEqual(files['prototype.html'].decode(), HTML)
        self.assertIn('static/app-shell.js', files)
        self.assertIn('core/design.py', files)
        self.assertNotIn('<header', files['templates/home.html'].decode())

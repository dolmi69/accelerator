"""Literal brand changes must preserve spelling and the working application."""
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from founder.models import User, StartupProfile, LabSiteVersion
from founder.services.site_names import name_request, rename_site
from founder.test_generated_backend import FILES

HTML = '''<!doctype html><html><head><title>smeta — инструмент</title>
<meta name="description" content="smeta — строительная смета.">
<script>const identifier = "smeta";</script></head><body>
<a class="brand" href="/" aria-label="smeta — главная"><span>s.</span><span>smeta</span></a>
<h1>Всё по полочкам</h1><p>Это строительная смета.</p></body></html>'''


@override_settings(LAB_RAW_AI_TEST_MODE=True, LAB_UNRESTRICTED_TEST_MODE=True)
class SiteNameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='brand-owner')
        self.project = StartupProfile.objects.create(owner=self.user, name='Brand')
        self.source = LabSiteVersion.objects.create(startup=self.project, kind='generated', prompt='поменяй название на smeta',
            html=HTML, backend_files={**FILES, 'index.html':HTML, 'assets/app.js':'const smeta = 1;', 'assets/style.css':'.smeta { color: red; }'}, model='test')
        self.client.force_login(self.user)

    def test_reported_followup_preserves_cyrillic_and_never_calls_ai(self):
        with patch('founder.services.lab_intent.decide') as planner, patch('founder.lab_views._generate_design') as code:
            response = self.client.post(reverse('lab_generate', args=[self.project.pk]),
                {'source_version':self.source.pk,'prompt':'а теперь давай обратно на "смета"','automatic':'1'})
        self.assertEqual(response.status_code, 302)
        planner.assert_not_called()
        code.assert_not_called()
        version = self.project.lab_versions.first()
        self.assertNotEqual(version.pk, self.source.pk)
        self.assertIn('<title>смета — инструмент</title>', version.html)
        self.assertIn('<span>смета</span>', version.html)
        self.assertIn('aria-label="смета — главная"', version.html)
        self.assertIn('content="смета — строительная смета."', version.html)
        self.assertIn('const identifier = "smeta";', version.html)
        for name in ['app.py','assets/app.js','assets/style.css']:
            self.assertEqual(version.backend_files[name], self.source.backend_files[name])
        self.assertEqual(version.backend_files['index.html'], version.html)
        self.assertEqual(version.model, 'local-editor')
        self.assertEqual(version.input_tokens + version.output_tokens, 0)
        self.assertIn('«смета»', version.bruno_report['items'][0])
        self.assertEqual(version.source_id, self.source.pk)

    def test_mixed_requests_are_not_silently_reduced_to_rename(self):
        for prompt in ['измени название на smeta и добавь оплату', 'а теперь обратно на "смета" и добавь страницу', 'переименуй app.py в server.py']:
            self.assertIsNone(name_request(prompt))

    def test_implicit_followup_requires_previous_rename(self):
        self.source.prompt = 'Добавь оплату'
        self.assertIsNone(rename_site(self.source, 'а теперь давай обратно на "смета"'))

    def test_explicit_name_preserves_case_and_ordinary_nouns(self):
        change = rename_site(self.source, 'поменяй название на «SMETA Pro»')
        self.assertIn('<span>SMETA Pro</span>', change['html'])
        self.assertIn('Это строительная смета.', change['html'])

    def test_stale_old_name_is_not_guessed(self):
        self.assertIsNone(rename_site(self.source, 'измени название с wrong на смета'))

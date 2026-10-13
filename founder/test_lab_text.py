"""Text edits cover both generated HTML and the reviewed Django shell."""
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from founder.models import LabSiteVersion, StartupProfile, User
from founder.services.backend_modules import normalize_modules
from founder.services.django_builder import project_files
from founder.services.site_editor import replace_visible_text, text_command


class LabTextTests(TestCase):
    def setUp(self):
        from founder.test_lab_support import install_lab_mocks
        install_lab_mocks(self)
        self.owner = User.objects.create_user(username='text_owner')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Text demo')
        self.source = LabSiteVersion.objects.create(startup=self.startup, prompt='Initial',
            kind='django', model='Qwen', backend_modules=normalize_modules(),
            html='<!doctype html><html><head></head><body><h1>Demo</h1></body></html>')
        self.client.force_login(self.owner)

    def rename(self, source, prompt):
        with patch('founder.lab_views.generate_site') as paid:
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
                {'source_version': source.pk, 'kind': source.kind, 'prompt': prompt})
        paid.assert_not_called()
        return response

    def test_registration_in_shell_gets_new_version_without_ai(self):
        response = self.rename(self.source, 'замени регристрацию на registration')
        self.assertEqual(response.status_code, 302)
        version = self.startup.lab_versions.first()
        self.assertNotEqual(version.pk, self.source.pk)
        self.assertEqual(version.source_id, self.source.pk)
        self.assertEqual(version.input_tokens, 0)
        self.assertEqual(version.output_tokens, 0)
        files = project_files(version)
        base = files['templates/_app_navigation.html'].decode()
        self.assertIn('>registration</a>', base)
        self.assertIn("{% url 'register' %}", base)
        self.assertIn('>Регистрация</a>', project_files(self.source)['templates/_app_navigation.html'].decode())
        self.assertEqual(version.backend_modules, self.source.backend_modules)

    def test_second_rename_and_theme_edit_preserve_shell_label(self):
        self.rename(self.source, 'замени регистрацию на registration')
        first = self.startup.lab_versions.first()
        self.rename(first, 'замени registration на Sign up')
        second = self.startup.lab_versions.first()
        self.client.post(reverse('lab_customize', args=[self.startup.pk]),
            {'source_version': second.pk, 'title': 'New title', 'palette': 'blue', 'font': 'system', 'radius': '8'})
        latest = self.startup.lab_versions.first()
        self.assertIn('>Sign up</a>', project_files(latest)['templates/_app_navigation.html'].decode())

    def test_missing_label_is_error_not_paid_noop(self):
        response = self.rename(self.source, 'замени несуществующий текст на Новый')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.startup.lab_versions.count(), 1)
        self.assertContains(response, 'не найдена', status_code=400)

    def test_literal_edit_preserves_code_links_and_escapes_template_syntax(self):
        html = '<a href="/Регистрация/">Регистрация</a><script>const x="Регистрация";</script><style>.Регистрация{}</style>{{ Регистрация }}'
        result = replace_visible_text(html, {'Регистрация': '{{ user.password }}<img>'})
        self.assertIn('href="/Регистрация/"', result)
        self.assertIn('>Регистрация</script>', replace_visible_text('<script>Регистрация</script>', {'Регистрация':'X'}))
        self.assertIn('const x="Регистрация"', result)
        self.assertIn('{{ Регистрация }}', result)
        self.assertIn('&#123;&#123; user.password &#125;&#125;&lt;img&gt;</a>', result)

    def test_mixed_task_not_claimed_as_simple_rename(self):
        self.assertIsNone(text_command('замени регистрацию на registration и добавь оплату'))
        self.assertEqual(text_command('Замени "Регистрация" на "Registration".'), ('Регистрация', 'Registration'))

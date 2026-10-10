"""Browser flow and access boundaries of the private website laboratory."""

from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from founder.models import LabSiteVersion, StartupProfile, User
from founder.services.qwen import CodeResult, QwenError
from founder.services.lab_design import design_context


HTML = '<!doctype html><html><head><title>Test</title></head><body><button>OK</button></body></html>'


@override_settings(LAB_REQUESTS_PER_DAY=10, QWEN_CODE_MAX_TOKENS=16384, LAB_CREATE_MAX_TOKENS=16384)
class LabTests(TestCase):
    def setUp(self):
        from founder.test_lab_support import install_lab_mocks
        install_lab_mocks(self)
        self.user = User.objects.create_user(username="lab_owner", email="lab@example.test")
        self.other = User.objects.create_user(username="lab_other", email="lab2@example.test")
        self.startup = StartupProfile.objects.create(owner=self.user, name="My site")
        self.foreign = StartupProfile.objects.create(owner=self.other, name="Other site")
        self.client.force_login(self.user)

    def route(self, name, startup=None, *parts):
        return reverse(name, args=[startup or self.startup.pk, *parts])

    def test_ai_failure_releases_lease_without_response_middleware(self):
        from founder.services.request_limits import acquire_ai_lease, release_ai_lease
        from founder.security_middleware import RequestProtectionMiddleware
        with patch.object(RequestProtectionMiddleware, 'process_response', lambda self, request, response: response), \
             patch('founder.lab_views.generate_site', side_effect=QwenError('Ошибка API')):
            response = self.client.post(self.route('lab_generate'), {'prompt': 'Сделай новый дизайн'})
        self.assertEqual(response.status_code, 503)
        token = acquire_ai_lease(self.user.pk)
        release_ai_lease(self.user.pk, token)

    def test_create_revise_and_preview_privately(self):
        with patch("founder.lab_views.generate_site", return_value=CodeResult(
            HTML, "Qwen/Qwen3-Coder-Next", 41, 83,
        )) as generate:
            response = self.client.post(self.route("lab_generate"), {"prompt": "Сделай страницу"})
            self.assertRedirects(response, self.route("lab"))
            first = LabSiteVersion.objects.get()
            self.assertEqual(first.startup, self.startup)
            self.assertIsNone(first.source)
            self.assertEqual((first.input_tokens, first.output_tokens), (41, 83))
            generate.assert_called_with("Сделай страницу", previous_html="", max_tokens=16384,
                project_context=design_context(self.startup, []), report=True)

            response = self.client.post(self.route("lab_generate"), {
                "prompt": "Добавь кнопку", "source_version": str(first.pk),
            })
            self.assertRedirects(response, self.route("lab"))
            second = LabSiteVersion.objects.exclude(pk=first.pk).get()
            self.assertEqual(second.source, first)
            generate.assert_called_with("Добавь кнопку", previous_html=HTML, max_tokens=16384,
                project_context=design_context(self.startup, [], source=first), report=True)

        page = self.client.get(self.route("lab") + f"?version={first.pk}")
        self.assertContains(page, 'sandbox="allow-scripts"')
        self.assertContains(page, self.route("lab_preview", self.startup.pk, first.pk))
        self.assertContains(page, "Добавь кнопку")

        preview = self.client.get(self.route("lab_preview", self.startup.pk, first.pk))
        self.assertIn('<button>OK</button>', preview.content.decode())
        self.assertIn('cofounder:lab-exit', preview.content.decode())
        self.assertIn("sandbox allow-scripts", preview["Content-Security-Policy"])
        self.assertIn("connect-src 'none'", preview["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", preview["Content-Security-Policy"])
        self.assertEqual(preview["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(preview["Cache-Control"], "no-store")

    def test_new_site_does_not_use_old_html(self):
        old = LabSiteVersion.objects.create(startup=self.startup, prompt="Старый", html=HTML, model="Qwen")
        with patch("founder.lab_views.generate_site", return_value=CodeResult(HTML, "Qwen", None, None)) as generate:
            self.client.post(self.route("lab_generate"), {
                "prompt": "Новый", "source_version": str(old.pk), "start_new": "1",
            })
        generate.assert_called_with("Новый", previous_html="", max_tokens=16384,
            project_context=design_context(self.startup, []), report=True)

    def test_access_and_bad_input_cannot_spend(self):
        foreign_version = LabSiteVersion.objects.create(
            startup=self.foreign, prompt="Секрет", html=HTML, model="Qwen",
        )
        with patch("founder.lab_views.generate_site") as generate:
            self.assertEqual(self.client.get(self.route("lab", self.foreign.pk)).status_code, 404)
            self.assertEqual(self.client.post(self.route("lab_generate", self.foreign.pk), {
                "prompt": "чужой проект",
            }).status_code, 404)
            self.assertEqual(self.client.get(self.route("lab_preview", self.foreign.pk, foreign_version.pk)).status_code, 404)
            self.assertEqual(self.client.get(self.route("lab_preview", self.startup.pk, foreign_version.pk)).status_code, 404)
            self.assertEqual(self.client.post(self.route("lab_generate"), {
                "prompt": "Правка", "source_version": str(foreign_version.pk),
            }).status_code, 404)
            self.assertEqual(self.client.get(self.route("lab") + "?version=bad").status_code, 404)
            self.assertEqual(self.client.post(self.route("lab_generate"), {"prompt": " "}).status_code, 400)
            generate.assert_not_called()

        anonymous = Client()
        self.assertEqual(anonymous.get(self.route("lab_preview", self.startup.pk, foreign_version.pk)).status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.route("lab_generate"), {"prompt": "No token"}).status_code, 403)

    def test_provider_failure_does_not_save_version(self):
        with patch("founder.lab_views.generate_site", side_effect=QwenError("Время ожидания вышло")):
            response = self.client.post(self.route("lab_generate"), {"prompt": "Сайт о сне"})
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Время ожидания вышло", status_code=503)
        self.assertFalse(LabSiteVersion.objects.exists())

    def test_compact_laboratory_sections_and_grouped_tools(self):
        version = LabSiteVersion.objects.create(startup=self.startup, prompt='Проверка', html=HTML, model='Qwen')
        page = self.client.get(self.route('lab') + f'?version={version.pk}')
        self.assertContains(page, 'Разработка прототипа')
        self.assertContains(page, 'Глобализация')
        self.assertContains(page, '<details class="lab-tools" id="lab-tools">')
        self.assertContains(page, '<details class="lab-history" id="lab-history">')
        self.assertNotContains(page, 'id_kind')
        self.assertNotContains(page, 'id_edit_scope')
        self.assertNotContains(page, 'id_rebuild')
        self.assertNotContains(page, 'name="start_new"')
        self.assertNotContains(page, 'ПРОТОТИП В КАРТОЧКЕ')
        self.assertContains(page, 'form="lab-form"')
        self.assertEqual(page.context['lab_section'], 'development')
        keys = [field.data['value'] for group in page.context['module_groups'] for field in group['fields']]
        self.assertEqual(keys, ['registration', 'password_reset', 'chat', 'notifications', 'leads', 'catalog', 'pages', 'uploads', 'favorites', 'reviews', 'booking', 'orders', 'payments'])
        global_page = self.client.get(self.route('lab') + f'?version={version.pk}&section=globalization')
        self.assertEqual(global_page.context['lab_section'], 'globalization')
        self.assertContains(global_page, 'data-lab-panel="development" hidden')
        self.assertContains(global_page, 'data-lab-panel="globalization"  aria-labelledby')

    def test_large_design_has_scope_control_inside_existing_tools(self):
        version = LabSiteVersion.objects.create(startup=self.startup, prompt='Large',
            html=HTML.replace('<button>OK</button>', '<section id="hero">'+('content '*2500)+'</section><section id="contact">Контакты</section>'), model='Qwen')
        page = self.client.get(self.route('lab') + f'?version={version.pk}')
        self.assertNotContains(page, 'Область AI-правки')
        self.assertNotContains(page, 'id_edit_scope')
        self.assertNotContains(page, '<option value="full"')
        self.assertNotContains(page, 'id_rebuild')

    @override_settings(LAB_REQUESTS_PER_DAY=1)
    def test_paid_generation_has_daily_cap(self):
        with patch("founder.lab_views.generate_site", return_value=CodeResult(HTML, "Qwen", 4, 8)) as generate:
            self.assertEqual(self.client.post(self.route("lab_generate"), {"prompt": "Первая"}).status_code, 302)
            self.assertEqual(self.client.post(self.route("lab_generate"), {"prompt": "Вторая"}).status_code, 429)
            self.assertEqual(generate.call_count, 1)

    @override_settings(LAB_REQUESTS_PER_DAY=0)
    def test_disabled_personal_cap_ignores_previously_used_quota(self):
        from founder.services.request_limits import consume_limit
        consume_limit(f"lab-day:{self.user.pk}", 1, 86400)
        with patch("founder.lab_views.generate_site", return_value=CodeResult(HTML, "Qwen", 4, 8)) as generate:
            for prompt in ("Первая", "Вторая"):
                self.assertEqual(self.client.post(self.route("lab_generate"), {"prompt": prompt}).status_code, 302)
            self.assertEqual(generate.call_count, 2)

    @override_settings(LAB_GLOBAL_REQUESTS_PER_DAY=1)
    def test_global_cap_covers_different_accounts(self):
        other_client = Client()
        other_client.force_login(self.other)
        with patch("founder.lab_views.generate_site", return_value=CodeResult(HTML, "Qwen", 4, 8)) as generate:
            self.assertEqual(self.client.post(self.route("lab_generate"), {"prompt": "Первая"}).status_code, 302)
            self.assertEqual(other_client.post(self.route("lab_generate", self.foreign.pk), {
                "prompt": "Вторая",
            }).status_code, 429)
            self.assertEqual(generate.call_count, 1)

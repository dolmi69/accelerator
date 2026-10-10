"""Reports describe stored results rather than repeating an unverified request."""
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from founder.models import StartupProfile, LabSiteVersion, User
from founder.services.backend_modules import normalize_modules
from founder.services.lab_reply import version_reply
from founder.services.qwen import CodeResult, QwenError, QwenOutputError

HTML = '<!doctype html><html><head><title>Demo</title></head><body><button>Buy</button></body></html>'


class LabReplyTests(TestCase):
    def setUp(self):
        from founder.test_lab_support import install_lab_mocks
        install_lab_mocks(self)
        self.owner = User.objects.create_user(username='reply_owner')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Demo')
        self.source = LabSiteVersion.objects.create(startup=self.startup, html=HTML, prompt='Initial', model='Qwen', input_tokens=10, output_tokens=20)
        self.client.force_login(self.owner)

    def child(self, **kwargs):
        return LabSiteVersion.objects.create(startup=self.startup, source=self.source,
            html=kwargs.pop('html', HTML), prompt='Сделай всё идеально', model=kwargs.pop('model', 'Qwen'), **kwargs)

    def test_actual_ai_text_change_and_token_total_without_extra_call(self):
        version = self.child(html=HTML.replace('Buy','Try'), input_tokens=40, output_tokens=30, edit_method='patch')
        with patch('founder.lab_views.generate_site') as ai:
            response = self.client.get(reverse('lab', args=[self.startup.pk]), {'version':version.pk, 'paused':1})
            ai.assert_not_called()
        report = response.context['lab_reply']
        self.assertIn('Текст «Buy» → «Try».', report['items'])
        self.assertEqual(report['usage'], 'AI: 70 токенов')
        self.assertContains(response, 'lab-reply')
        self.assertContains(response, 'Бруно')
        self.assertNotIn('идеально', str(report))

    def test_backend_install_and_rename_show_zero_tokens(self):
        version = self.child(kind='django', backend_modules=normalize_modules(['chat']),
            model='django-modules-v2', edit_method='local', input_tokens=0, output_tokens=0)
        report = version_reply(version)
        self.assertIn('Чаты между пользователями', str(report['items']))
        self.assertEqual(report['usage'], 'Без AI · 0 токенов')
        renamed = LabSiteVersion.objects.create(startup=self.startup, source=version, html=HTML, prompt='Rename', kind='django',
            backend_modules=version.backend_modules, model='local-editor', edit_method='local', input_tokens=0, output_tokens=0,
            presentation={'text_replacements':{'Регистрация':'Registration'}})
        self.assertEqual(version_reply(renamed)['items'], ['Надпись «Регистрация» → «Registration».'])

    def test_noop_does_not_claim_success_and_unknown_usage_is_not_zero(self):
        version = self.child(edit_method='patch')
        report = version_reply(version)
        self.assertEqual(report['title'], 'Изменений пока нет')
        self.assertEqual(report['usage'], 'Расход AI уточняется')

    def test_failed_request_has_error_report_not_previous_success(self):
        with patch('founder.lab_views.generate_site', side_effect=QwenError('Не удалось получить ответ')):
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
                {'source_version':self.source.pk,'prompt':'Создай другой дизайн'})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.context['lab_reply']['failed'])
        self.assertIn('Не удалось получить ответ', response.context['lab_reply']['items'])
        self.assertNotEqual(response.context['lab_reply']['title'], 'Готово, вот что я сделал')

    def test_html_in_report_is_escaped(self):
        version = self.child(model='local-editor', edit_method='local', input_tokens=0, output_tokens=0,
            presentation={'text_replacements':{'Buy':'<img src=x onerror=alert(1)>'}})
        response = self.client.get(reverse('lab', args=[self.startup.pk]), {'version':version.pk, 'paused':1})
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')

    def test_already_enabled_modules_do_not_repeat_old_success(self):
        self.source.kind = 'django'
        self.source.backend_modules = normalize_modules(['chat'])
        self.source.save()
        response = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
            {'source_version': self.source.pk, 'prompt': 'добавь чаты'}, follow=True)
        self.assertEqual(response.context['lab_reply']['title'], 'Всё уже настроено')
        self.assertEqual(response.context['lab_reply']['usage'], 'Без AI · 0 токенов')

    def test_cached_result_reports_zero_new_tokens(self):
        cached = self.child(html=HTML.replace('Buy', 'Try'), input_tokens=40, output_tokens=30,
                            generation_key='cached-reply')
        with patch('founder.lab_views.generation_key', return_value='cached-reply'), patch('founder.lab_views.generate_site') as ai:
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
                {'source_version': self.source.pk, 'prompt': 'Сделай другой дизайн'}, follow=True)
            ai.assert_not_called()
        self.assertEqual(response.context['selected'].pk, cached.pk)
        self.assertEqual(response.context['lab_reply']['title'], 'Открыл сохранённый результат')
        self.assertIn('0 токенов', response.context['lab_reply']['usage'])

    def test_incomplete_design_does_not_claim_success_or_zero_ai_cost(self):
        with patch('founder.lab_views.generate_site', side_effect=QwenOutputError('Обрезанный ответ')):
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
                {'source_version': self.source.pk, 'prompt': 'Создай новый дизайн', 'kind': 'django'}, follow=True)
        self.assertTrue(response.context['lab_reply']['failed'])
        self.assertEqual(response.context['lab_reply']['title'], 'Дизайн пока не завершён')
        self.assertNotIn('0 токенов', response.context['lab_reply']['usage'])
        version = response.context['selected']
        self.assertEqual(version.bruno_report['title'], 'Дизайн пока не завершён')
        self.assertTrue(version.bruno_report['failed'])

    def test_generated_report_is_stored_with_prompt_and_shown_below_preview(self):
        with patch('founder.lab_views.generate_site', return_value=CodeResult(
            HTML.replace('Buy', 'Try'), 'Qwen', 40, 30, edit_method='patch',
        )):
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
                {'source_version': self.source.pk, 'prompt': 'Переделай кнопку'}, follow=True)
        version = response.context['selected']
        version.refresh_from_db()
        self.assertIn('Текст «Buy» → «Try».', version.bruno_report['items'])
        self.assertEqual(version.bruno_report['usage'], 'AI: 70 токенов')
        body = response.content.decode()
        self.assertLess(body.index('id="lab-site-frame"'), body.index('id="lab-reply"'))
        self.assertIn(f'id="lab-report-{version.pk}"', body)
        self.assertEqual(response.context['versions'][0].history_reply, version.bruno_report)
        self.assertContains(response, 'Запрос и отчёт Бруно')
        # Reading a saved report must not recalculate it or make an AI call.
        self.source.bruno_report = version_reply(self.source)
        self.source.save(update_fields=['bruno_report'])
        with patch('founder.services.lab_reply.version_reply', side_effect=AssertionError('Recalculated')):
            reopened = self.client.get(reverse('lab', args=[self.startup.pk]), {'version': version.pk})
        self.assertEqual(reopened.context['lab_reply'], version.bruno_report)

    def test_free_edits_and_modules_store_reports_without_ai(self):
        with patch('founder.lab_views.generate_site') as ai:
            renamed = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
                {'source_version': self.source.pk, 'prompt': 'замени Buy на Try'}, follow=True)
            version = renamed.context['selected']
            self.assertTrue(version.bruno_report)
            self.assertEqual(version.bruno_report['usage'], 'Без AI · 0 токенов')
            installed = self.client.post(reverse('lab_backend_create', args=[self.startup.pk]),
                {'source_version': version.pk, 'modules_selected': '1', 'modules': ['chat']}, follow=True)
            self.assertIn('Чаты между пользователями', str(installed.context['selected'].bruno_report))
            ai.assert_not_called()

    def test_old_selected_version_is_in_history_and_reports_are_private(self):
        for _ in range(21):
            self.child()
        response = self.client.get(reverse('lab', args=[self.startup.pk]), {'version': self.source.pk})
        self.assertContains(response, f'id="lab-report-{self.source.pk}"')
        self.assertEqual(response.context['versions'][-1].history_reply, version_reply(self.source))
        other = User.objects.create_user(username='report_outsider', email='outsider@example.test')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('lab', args=[self.startup.pk])).status_code, 404)

    def test_cache_notice_does_not_rewrite_saved_report_cost(self):
        from founder.lab_views import _create_version
        cached = _create_version(startup=self.startup, source=self.source, prompt='Другой дизайн',
            html=HTML.replace('Buy', 'Try'), model='Qwen', input_tokens=40, output_tokens=30,
            generation_key='saved-report-cache')
        original = cached.bruno_report.copy()
        with patch('founder.lab_views.generation_key', return_value='saved-report-cache'):
            response = self.client.post(reverse('lab_generate', args=[self.startup.pk]),
                {'source_version': self.source.pk, 'prompt': 'Другой дизайн'}, follow=True)
        self.assertIn('0 токенов', response.context['lab_reply']['usage'])
        cached.refresh_from_db()
        self.assertEqual(cached.bruno_report, original)
        self.assertEqual(response.context['versions'][0].history_reply['usage'], 'AI: 70 токенов')

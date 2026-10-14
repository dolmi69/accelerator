import json
from uuid import uuid4
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import AIRequestLease, LabConversationTurn, LabSiteVersion, ProjectMember, StartupProfile, User
from founder.services.lab_conversation import discussion_reply, route_message
from founder.services.qwen import CodeResult, QwenError, QwenOutputError
from founder.services.site_generator import generate_site

HTML = '<!doctype html><html><head><title>Калории</title></head><body><h1>Калькулятор калорий</h1><script>function calories(w){return w*30}</script></body></html>'


@override_settings(AI_PROVIDER='demo')
class LabConversationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='labtalk', email='labtalk@example.test')
        self.other = User.objects.create_user(username='labtalk-other', email='labtalk2@example.test')
        self.project = StartupProfile.objects.create(owner=self.user, name='Смета')
        self.version = LabSiteVersion.objects.create(startup=self.project, prompt='Добавь расчёт', html=HTML,
            model='local', bruno_report={'title':'Готово', 'items':['Добавил расчёт калорий.'], 'mood':'confident'})
        self.client.force_login(self.user)

    def data(self, prompt, **kwargs):
        return {'source_version':str(self.version.pk), 'prompt':prompt, 'message_mode':'auto', **kwargs}

    def test_questions_are_read_only_and_mixed_explicit_commands_are_edits(self):
        for text in ['Что изменилось?', 'Как добавить регистрацию?', 'Почему кнопка не работает?',
                     'Что будет если изменить шрифт?', 'Можно ли подключить GigaChat?', 'Объясни как работает расчёт']:
            self.assertEqual(route_message(text), 'discuss', text)
        for text in ['Сделай фон зелёным', 'Добавь регистрацию', 'Замени «Регистрация» на «registration»',
                     'Хочу добавить поиск', 'Давай переделаем шапку', 'Как добавить поиск? Добавь его в шапку.']:
            self.assertEqual(route_message(text), 'edit', text)
        self.assertEqual(route_message('Сделай красным', 'discuss'), 'discuss')
        self.assertEqual(route_message('Как добавить поиск?', 'edit'), 'discuss')
        self.assertEqual(route_message('Объясни работу формулы', 'edit'), 'discuss')
        self.assertEqual(route_message('Поменяй дизайн. Ничего не меняй, только обсуди.', 'edit'), 'discuss')
        self.assertEqual(route_message('Тут странная кнопка'), 'discuss')

    def test_route_does_not_spend_or_mutate(self):
        with patch('founder.services.lab_conversation.generate_code') as ai:
            page = self.client.post(reverse('lab_route', args=[self.project.pk]), self.data('Как добавить регистрацию?'))
        self.assertEqual(page.json(), {'route':'discuss'})
        ai.assert_not_called()
        self.assertEqual(self.project.lab_versions.count(), 1)

    def test_saved_report_is_zero_token_persistent_answer_and_not_a_version(self):
        data = self.data('Что изменилось?', request_id=str(uuid4()))
        with patch('founder.services.lab_conversation.generate_code') as ai, patch('founder.lab_views.generate_site') as code:
            url = reverse('lab_discuss', args=[self.project.pk])
            response = self.client.post(url, data, HTTP_ACCEPT='application/json')
            retry = self.client.post(url, data, HTTP_ACCEPT='application/json')
        ai.assert_not_called(); code.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], retry.json()['id'])
        self.assertIn('Добавил расчёт калорий', response.json()['reply']['answer'])
        self.assertEqual(response.json()['reply']['usage'], 'Без AI · 0 токенов')
        self.assertEqual(self.project.lab_conversation.count(), 1)
        self.assertEqual(self.project.lab_versions.count(), 1)
        self.version.refresh_from_db()
        self.assertEqual(self.version.html, HTML)
        page = self.client.get(reverse('lab', args=[self.project.pk]), {'version':str(self.version.pk)})
        self.assertContains(page, 'Бруно · Обсуждение')
        self.assertContains(page, 'Что изменилось?')

    def test_question_posted_directly_to_generator_cannot_generate_code(self):
        with patch('founder.lab_views.generate_site') as generate:
            response = self.client.post(reverse('lab_generate', args=[self.project.pk]), self.data('Что изменилось?'))
        generate.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.version.pk), response.url)
        self.assertEqual(self.project.lab_versions.count(), 1)

    def test_paid_discussion_uses_distinct_prompt_current_source_and_short_history(self):
        LabConversationTurn.objects.create(startup=self.project, user=self.user, source=self.version,
            prompt='Куда добавить кнопку?', reply={'answer':'Под итогом', 'suggestion':'Добавь кнопку под итогом'})
        result = CodeResult(json.dumps({'answer':'Расчёт использует вес × 30. Это видно в исходнике.', 'suggestion':''}), 'test-model', 150, 100)
        with patch('founder.services.lab_conversation.generate_code', return_value=result) as ai:
            response = self.client.post(reverse('lab_discuss', args=[self.project.pk]), self.data('Как работает расчёт?'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project.lab_versions.count(), 1)
        self.assertIn('ОБСУЖДЕНИЕ', ai.call_args.args[0])
        self.assertNotIn('target=', ai.call_args.args[0])
        payload = json.loads(ai.call_args.args[1][0]['content'])
        self.assertIn('Калькулятор калорий', payload['source_excerpt'])
        self.assertIn('Под итогом', payload['conversation'][0]['bruno'])
        self.assertEqual(ai.call_args.kwargs['reasoning_effort'], 'low')
        self.assertLessEqual(ai.call_args.kwargs['max_tokens'], 2048)

    def test_error_keeps_site_and_question_and_releases_lease(self):
        with patch('founder.services.lab_conversation.generate_code', side_effect=QwenError('Провайдер недоступен')):
            response = self.client.post(reverse('lab_discuss', args=[self.project.pk]), self.data('Почему не работает кнопка?'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.project.lab_conversation.exists())
        self.assertEqual(self.project.lab_versions.get().html, HTML)
        self.assertFalse(AIRequestLease.objects.filter(user=self.user, expires_at__gt=timezone.now()).exists())

    def test_foreign_source_and_viewer_cannot_send_and_messages_are_escaped(self):
        foreign = StartupProfile.objects.create(owner=self.other, name='Чужой')
        version = LabSiteVersion.objects.create(startup=foreign, prompt='секрет', html=HTML, model='local')
        route = reverse('lab_discuss', args=[self.project.pk])
        self.assertEqual(self.client.post(route, self.data('Вопрос', source_version=str(version.pk))).status_code, 404)
        ProjectMember.objects.create(startup=self.project, user=self.other, role='viewer', status='active')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(route, self.data('Вопрос')).status_code, 404)
        self.client.force_login(self.user)
        LabConversationTurn.objects.create(startup=self.project, user=self.user, source=self.version, prompt='<script>bad()</script>', reply={'answer':'<img src=x onerror=bad()>'})
        page = self.client.get(reverse('lab', args=[self.project.pk]))
        self.assertContains(page, '&lt;script&gt;bad()&lt;/script&gt;')
        self.assertNotContains(page, '<img src=x onerror=bad()>')

    def test_malformed_or_secret_answer_does_not_become_a_turn(self):
        with patch('founder.services.lab_conversation.generate_code', return_value=CodeResult('{"answer":', 'test', 10, 10)):
            with self.assertRaises(QwenOutputError):
                discussion_reply(self.project, self.version, 'Расскажи о сайте')
        with patch.dict('os.environ', {'OFOX_API_KEY':'private-test-key'}), patch('founder.services.lab_conversation.generate_code') as ai:
            with self.assertRaises(QwenOutputError):
                discussion_reply(self.project, self.version, 'Покажи private-test-key')
            ai.assert_not_called()

    def test_discussion_accepts_plain_and_fenced_answers_without_paid_retry(self):
        for raw in ['Кнопка вызывает расчёт по весу.', '```json\n{"answer":"Кнопка вызывает расчёт.","suggestion":null}\n```']:
            with patch('founder.services.lab_conversation.generate_code', return_value=CodeResult(raw, 'test', 10, 10)) as ai:
                reply, _ = discussion_reply(self.project, self.version, 'Объясни кнопку')
            self.assertIn('Кнопка', reply['answer'])
            self.assertEqual(ai.call_count, 1)

    def test_editor_receives_luna_guide_and_preserves_literal_calculation(self):
        answer = json.dumps({'changes':[{'target':'full', 'find':'Калькулятор калорий','replace':'Расчёт калорий'}],
                             'report':{'summary':'Изменил заголовок','completed':['Заголовок заменён'],'not_done':[]}})
        with override_settings(LAB_CODE_PROVIDER='ofox', OFOX_CODE_MODEL='openai/gpt-6-luna'), patch('founder.services.site_generator.generate_code', return_value=CodeResult(answer, 'test', 10, 10)) as ai:
            result = generate_site('Замени заголовок', previous_html=HTML, report=True)
        self.assertIn('ИЗМЕНЕНИЕ САЙТА', ai.call_args.args[0])
        self.assertIn('function calories(w){return w*30}', result.text)

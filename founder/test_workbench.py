import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import BrunoTask, ChatMessage, ChatSession, EvidenceEntry, PitchReport, StartupMemory, StartupMetrics, StartupProfile, User
from founder.services.ai import AIServiceError, system_prompt
from founder.services.memory import remember_user_message, relevant_memories
from founder.services.pitch import finish_pitch
from founder.services.profile import evidence_display
from founder.services.radar_assessment import _assessment_context, _verified_evidence
from founder.services.workbench import generate_tasks


@override_settings(AI_PROVIDER='demo')
class WorkbenchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='workbench', email='workbench@example.test')
        self.other = User.objects.create_user(username='outsider', email='other@example.test')
        self.startup = StartupProfile.objects.create(owner=self.user, name='Stockroom', one_line_pitch='Учёт остатков для магазинов')
        self.foreign = StartupProfile.objects.create(owner=self.other, name='Private')
        self.client.force_login(self.user)

    def entry_data(self, task=None):
        return dict(task=str(task.pk) if task else '', axis=task.axis if task else 'market', claim='Клиенты готовы платить', observation='Пять разговоров за неделю; никто не согласился оплатить пилот.', observed_on=str(timezone.localdate()), outcome='refuted', source='Заметки о пяти разговорах', source_url='', next_question='Слишком высокая цена или слабая проблема?')

    def test_tasks_are_limited_and_repeated_requests_do_not_duplicate(self):
        items = generate_tasks(self.startup)
        self.assertEqual(len(items), 3)
        self.assertEqual(generate_tasks(self.startup), [])
        self.assertEqual(self.startup.bruno_tasks.count(), 3)
        page = self.client.get(reverse('tasks', args=[self.startup.pk]))
        self.assertContains(page, items[0].title)

    def test_negative_result_completes_task_but_does_not_award_points(self):
        task = generate_tasks(self.startup)[0]
        url = reverse('evidence_create', args=[self.startup.pk])
        result = self.client.post(url, self.entry_data(task))
        self.assertEqual(result.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, 'done')
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(EvidenceEntry.objects.get().outcome, 'refuted')
        self.assertFalse(StartupMetrics.objects.exists())
        self.assertEqual(len(generate_tasks(self.startup)), 1)

    def test_form_rejects_foreign_task_and_wrong_axis(self):
        foreign_task = generate_tasks(self.foreign)[0]
        response = self.client.post(reverse('evidence_create', args=[self.startup.pk]), self.entry_data(foreign_task))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EvidenceEntry.objects.exists())
        task = generate_tasks(self.startup)[0]
        data = self.entry_data(task)
        data['axis'] = 'pitch' if task.axis != 'pitch' else 'product'
        response = self.client.post(reverse('evidence_create', args=[self.startup.pk]), data)
        self.assertEqual(response.status_code, 400)
        task.refresh_from_db()
        self.assertEqual(task.status, 'todo')

    def test_owner_checks_cover_all_new_pages_and_actions(self):
        task = generate_tasks(self.foreign)[0]
        entry = EvidenceEntry.objects.create(startup=self.foreign, axis='market', claim='Private evidence', observation='secret')
        for name in ['tasks', 'evidence_list', 'investor']:
            self.assertEqual(self.client.get(reverse(name, args=[self.foreign.pk])).status_code, 404)
        for name in ['tasks_generate', 'pitch_create']:
            self.assertEqual(self.client.post(reverse(name, args=[self.foreign.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('task_skip', args=[self.startup.pk, task.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('evidence_edit', args=[self.startup.pk, entry.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('evidence_create', args=[self.startup.pk])+'?task=invalid').status_code, 404)

    def test_evidence_is_editable_and_citable_by_the_radar(self):
        self.client.post(reverse('evidence_create', args=[self.startup.pk]), self.entry_data())
        entry = self.startup.evidence_entries.get()
        data = self.entry_data()
        data['observation'] = 'Уточнение: один покупатель оплатил 1000 рублей 20 сентября.'
        self.client.post(reverse('evidence_edit', args=[self.startup.pk, entry.pk]), data)
        context, sources = _assessment_context(self.startup, include_sources=True)
        ref = f'evidence:{entry.pk}'
        self.assertIn(data['observation'], context)
        self.assertIn(ref, sources)
        raw = json.dumps({axis: {'evidence': {'status': 'stated', 'source_id': ref, 'quote': data['observation']}} for axis in ['product', 'market', 'finance', 'team', 'pitch']})
        proof = _verified_evidence(raw, sources)
        snapshot = StartupMetrics(assessment_evidence=proof)
        self.assertEqual(evidence_display(self.startup, snapshot, 'market')['url'], reverse('evidence_edit', args=[self.startup.pk, entry.pk]))
        self.assertContains(self.client.get(reverse('evidence_list', args=[self.startup.pk])), 'Уточнение: один покупатель')

    def test_investor_answers_never_become_business_facts_including_old_memories(self):
        self.client.post(reverse('pitch_create', args=[self.startup.pk]))
        session = self.startup.chat_sessions.get(mode='pitch')
        self.assertIn(self.startup.name, session.messages.get().content)
        message = ChatMessage.objects.create(session=session, role='user', content='Тренировка: выручка 999999999 рублей')
        self.assertIsNone(remember_user_message(message))
        StartupMemory.objects.create(startup=self.startup, source_message=message, content=message.content)
        self.assertEqual(relevant_memories(self.startup, 'выручка'), [])
        self.assertNotIn('999999999', _assessment_context(self.startup))
        prompt = system_prompt(session, [])
        self.assertIn('цикл продажи', prompt)
        self.assertIn(self.startup.name, prompt)
        report = finish_pitch(session)
        self.assertEqual(finish_pitch(session), report)
        self.assertFalse(StartupMetrics.objects.exists())
        self.assertEqual(self.client.post(reverse('chat_send', args=[self.startup.pk, session.pk]), {'content': 'ещё'}).status_code, 409)
        self.assertContains(self.client.get(reverse('investor', args=[self.startup.pk])), 'Разбор:')

    @override_settings(AI_PROVIDER='gigachat')
    @patch('founder.services.workbench.complete_text')
    def test_invalid_ai_tasks_create_nothing(self, complete):
        complete.return_value = json.dumps({'tasks': [{'axis':'market', 'title':'Тест'}]})
        with self.assertRaises(AIServiceError):
            generate_tasks(self.startup)
        self.assertFalse(BrunoTask.objects.exists())

    @override_settings(AI_PROVIDER='gigachat')
    @patch('founder.services.pitch.complete_text')
    def test_report_schema_accepts_no_mistakes_and_rejects_invalid_score(self, complete):
        session = ChatSession.objects.create(startup=self.startup, mode='pitch')
        ChatMessage.objects.create(session=session, role='user', content='Пока продаж нет, это гипотеза.')
        complete.return_value = json.dumps({'score': True, 'summary': 'Разбор', 'mistakes': []})
        with self.assertRaises(AIServiceError):
            finish_pitch(session)
        self.assertFalse(PitchReport.objects.exists())
        complete.return_value = json.dumps({'score': 50, 'summary': 'Для полного разбора нужен более длинный разговор.', 'mistakes': []})
        report = finish_pitch(session)
        self.assertEqual(report.mistakes, [])
        self.assertIn('json_schema', complete.call_args.kwargs)

    @override_settings(AI_PROVIDER='gigachat')
    @patch('founder.services.pitch.complete_text')
    def test_report_excludes_pending_question_and_rejects_invented_quotes(self, complete):
        session = ChatSession.objects.create(startup=self.startup, mode='pitch')
        ChatMessage.objects.create(session=session, role='assistant', content='Кто покупатель?')
        ChatMessage.objects.create(session=session, role='user', content='Владелец магазина. Пока это гипотеза.')
        ChatMessage.objects.create(session=session, role='assistant', content='UNANSWERED: сколько стоит привлечение?')
        mistake = dict(title='Уточнить сегмент', detail='Сегмент широкий.', recommendation='Назовите тип магазина.', quote='Несуществующая цитата')
        complete.return_value = json.dumps({'score': 55, 'summary': 'Предварительный разбор одного ответа.', 'mistakes': [mistake]})
        with self.assertRaises(AIServiceError):
            finish_pitch(session)
        self.assertNotIn('UNANSWERED', complete.call_args.args[1])
        self.assertFalse(PitchReport.objects.exists())
        mistake['quote'] = 'Владелец магазина.'
        complete.return_value = json.dumps({'score': 55, 'summary': 'Предварительный разбор одного ответа.', 'mistakes': [mistake]})
        self.assertEqual(finish_pitch(session).mistakes[0]['quote'], 'Владелец магазина')

    def test_report_quote_tolerates_punctuation_but_preserves_original_words(self):
        from founder.services.pitch import _source_quote
        source = 'Пилот за 1500 рублей в месяц: хотим проверить спрос.'
        self.assertEqual(_source_quote('Пилот за 1500 рублей в месяц.', [source]), 'Пилот за 1500 рублей в месяц')
        with self.assertRaises(ValueError):
            _source_quote('Пилот за 2500 рублей в месяц.', [source])

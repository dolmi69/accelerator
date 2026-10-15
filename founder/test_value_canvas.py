import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatSession, ProjectMember, ProjectPicture, StartupProfile, User, ValueCanvas
from founder.services import value_canvas
from founder.services.mentor import picture_context

SEGMENTS = {'segments': [
    {'title': 'Первокурсники в общежитиях', 'description': 'Готовят себе сами, нет времени',
     'context': 'Студенты вузов', 'holder': 'Сам студент, платит из стипендии', 'grade': 'a',
     'reason': 'Проблема каждый день'},
    {'title': 'Аспиранты', 'description': 'Работают в лаборатории допоздна', 'context': 'Вузы',
     'holder': 'Аспирант', 'grade': 'C', 'reason': 'Мало денег'},
    {'title': 'Сотрудники офисов', 'description': 'Обедают за столом', 'context': 'Бизнес-центры',
     'holder': 'Сотрудник или работодатель', 'grade': 'Z', 'reason': 'Неясно'},
    {'title': 'Фантазия', 'description': 'Сэкономят 47% бюджета', 'context': 'Любые',
     'holder': 'Все', 'grade': 'A', 'reason': 'Рост 300%'},
]}
BRAINSTORM = {
    'situations': [
        {'doing': 'Готовится к сессии ночью', 'want': 'Не тратить время на готовку', 'promise': 'Еда к двери за полчаса'},
        {'doing': 'Ест лапшу третий день', 'want': 'Почувствовать заботу', 'promise': 'Домашняя еда'},
    ],
    'jobs': [
        {'kind': 'functional', 'question': 'Как быстро поесть?', 'value': 'Готовый обед рядом'},
        {'kind': 'social', 'question': 'Что скажут соседи?', 'value': 'Можно заказать на компанию'},
        {'kind': 'magic', 'question': 'Нестандартное', 'value': 'Отбросить'},
    ],
}
VALUE = {
    'formula': 'Наш продукт помогает первокурсникам справиться с нехваткой времени с помощью доставки и получить домашнюю еду',
    'statements': ['Мы это домашняя кухня для студентов, которые хотят есть нормально',
                   'Мы это столовая в общежитии для тех, кто хочет экономить',
                   'Мы это кухня для 10000 студентов'],
    'analogies': ['Мама на расстоянии одного клика'],
}


def reply(data):
    return patch('founder.services.value_canvas.complete_text', return_value=json.dumps(data, ensure_ascii=False))


@override_settings(AI_PROVIDER='gigachat')
class ValueCanvasTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('value_owner', email='value_owner@example.test')
        self.viewer = User.objects.create_user('value_viewer', email='value_viewer@example.test')
        self.stranger = User.objects.create_user('value_stranger', email='value_stranger@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Обеды в общагу',
                                                     problem='Студентам некогда готовить')
        ProjectMember.objects.create(startup=self.startup, user=self.viewer, status='active', role='viewer')
        self.client.force_login(self.owner)
        self.page = reverse('value', args=[self.startup.pk])

    def url(self, name):
        return reverse(name, args=[self.startup.pk])

    def test_segments_from_bruno_keep_founder_in_charge(self):
        with reply(SEGMENTS) as model:
            response = self.client.post(self.url('value_segments'))
        self.assertRedirects(response, self.page + '#abcdx', fetch_redirect_response=False)
        payload = model.call_args.args[1]
        self.assertIn('Студентам некогда готовить', payload)
        canvas = ValueCanvas.objects.get(startup=self.startup)
        titles = [item['title'] for item in canvas.segments]
        self.assertEqual(titles, ['Первокурсники в общежитиях', 'Аспиранты', 'Сотрудники офисов'])  # Invented % dropped.
        self.assertEqual([item['grade'] for item in canvas.segments], ['A', 'C', 'X'])  # Unknown letter -> X.
        page = self.client.get(self.page)
        self.assertContains(page, 'Бруно предложил A: Проблема каждый день')
        # Founder changes the letters, removes one and adds his own.
        self.client.post(self.url('value_segments_save'), {
            'grade_0': 'B', 'grade_1': 'A', 'grade_2': 'D', 'delete_2': '1',
            'new_title': 'Родители студентов', 'new_grade': 'A', 'new_holder': 'Родители платят'})
        canvas.refresh_from_db()
        self.assertEqual([(item['title'], item['grade']) for item in canvas.segments],
                         [('Первокурсники в общежитиях', 'B'), ('Аспиранты', 'A'), ('Родители студентов', 'A')])
        with reply(SEGMENTS):
            self.client.post(self.url('value_segments'))
        canvas.refresh_from_db()
        self.assertEqual(len(canvas.segments), 4)  # Only the new title is added, no duplicates.

    def test_brainstorm_needs_segment_a_and_saves_choices(self):
        with reply(BRAINSTORM) as model:
            self.client.post(self.url('value_brainstorm'))
        model.assert_not_called()
        self.assertContains(self.client.get(self.page), 'Шаг откроется, когда хотя бы один сегмент')
        with reply(SEGMENTS):
            self.client.post(self.url('value_segments'))
        with reply(BRAINSTORM) as model:
            self.client.post(self.url('value_brainstorm'))
        payload = json.loads(model.call_args.args[1])
        self.assertEqual([item['title'] for item in payload['сегменты A']], ['Первокурсники в общежитиях'])
        canvas = ValueCanvas.objects.get(startup=self.startup)
        self.assertEqual(len(canvas.situations), 2)
        self.assertEqual([job['kind'] for job in canvas.jobs], ['functional', 'social'])
        page = self.client.get(self.page)
        self.assertContains(page, 'Готовится к сессии ночью')
        self.assertContains(page, 'Функциональная')
        self.client.post(self.url('value_brainstorm_save'), {'situations': ['1'], 'jobs': ['0']})
        canvas.refresh_from_db()
        self.assertEqual([item['chosen'] for item in canvas.situations], [False, True])
        self.assertEqual([item['chosen'] for item in canvas.jobs], [True, False])

    def test_formulate_value_from_picture_button_and_save(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        ProjectPicture.objects.create(startup=self.startup, facts={'customer': {'text': 'Первокурсники', 'status': 'fact'}})
        chat = self.client.get(reverse('chat_detail', args=[self.startup.pk, session.pk]))
        self.assertContains(chat, 'Сформулировать ценность')
        self.assertContains(chat, f'action="{self.url("value_generate")}"')
        with reply(VALUE) as model:
            response = self.client.post(self.url('value_generate'))
        self.assertRedirects(response, self.page + '#value-step', fetch_redirect_response=False)
        self.assertIn('Первокурсники', model.call_args.args[1])
        canvas = ValueCanvas.objects.get(startup=self.startup)
        self.assertEqual(len(canvas.options['formula']), 1)
        self.assertEqual(len(canvas.options['statements']), 2)  # «10000 студентов» is an invented number.
        self.assertEqual(canvas.options['analogies'], ['Мама на расстоянии одного клика'])
        page = self.client.get(self.page)
        self.assertContains(page, 'Мама на расстоянии одного клика')
        chosen = canvas.options['statements'][0]
        self.client.post(self.url('value_save'), {'value': chosen})
        canvas.refresh_from_db()
        self.assertEqual((canvas.value, canvas.value_source), (chosen, 'bruno'))
        self.client.post(self.url('value_save'), {'value': chosen + ' и не стоять в очереди'})
        canvas.refresh_from_db()
        self.assertEqual(canvas.value_source, 'founder')
        self.assertContains(self.client.get(reverse('chat_detail', args=[self.startup.pk, session.pk])),
                            'и не стоять в очереди')

    def test_value_and_ideal_clients_reach_bruno_context(self):
        ValueCanvas.objects.create(startup=self.startup, value='Мы это домашняя кухня для студентов',
                                   segments=[{'title': 'Первокурсники', 'grade': 'A'}, {'title': 'Офисы', 'grade': 'D'}])
        context = picture_context(self.startup)
        self.assertIn('Ценностное предложение (выбрал основатель): Мы это домашняя кухня для студентов', context)
        self.assertIn('Идеальные клиенты (сегмент A по ABCDX): Первокурсники', context)
        self.assertNotIn('Офисы', context)

    def test_empty_value_and_demo_mode(self):
        self.client.post(self.url('value_save'), {'value': '   '})
        self.assertFalse(ValueCanvas.objects.get(startup=self.startup).value)
        with override_settings(AI_PROVIDER='demo'), reply(VALUE) as model:
            response = self.client.post(self.url('value_generate'), follow=True)
        model.assert_not_called()
        self.assertContains(response, 'нужен подключённый AI')

    def test_broken_model_answer_changes_nothing(self):
        with patch('founder.services.value_canvas.complete_text', return_value='не JSON'):
            response = self.client.post(self.url('value_segments'), follow=True)
        self.assertContains(response, 'не по формату')
        self.assertEqual(ValueCanvas.objects.get(startup=self.startup).segments, [])

    def test_access(self):
        self.client.force_login(self.viewer)
        page = self.client.get(self.page)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Предложить сегменты')
        with reply(SEGMENTS) as model:
            self.assertEqual(self.client.post(self.url('value_segments')).status_code, 404)
        model.assert_not_called()
        self.assertEqual(self.client.post(self.url('value_save'), {'value': 'x'}).status_code, 404)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.page).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.page).status_code, 302)

    def test_number_guard(self):
        self.assertTrue(value_canvas._no_new_numbers('Обед за 300 рублей', 'цена 300 рублей'))
        self.assertFalse(value_canvas._no_new_numbers('Экономия 40%', 'цена 300 рублей'))
        self.assertTrue(value_canvas._no_new_numbers('Без чисел', ''))

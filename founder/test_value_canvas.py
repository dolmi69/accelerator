import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import ChatSession, ProjectMember, ProjectPicture, StartupProfile, User, ValueCanvas
from founder.services import value_canvas
from founder.services.mentor import picture_context

# Ответы модели в плоском формате (seg1_title…, sit1_doing…, vp_*, jtbd_*): вложенный JSON GigaChat ломает.
SEGMENT_LIST = [
    {'title': 'Первокурсники в общежитиях', 'description': 'Готовят себе сами, нет времени',
     'context': 'Студенты вузов', 'holder': 'Сам студент, платит из стипендии', 'grade': 'a',
     'reason': 'Проблема каждый день'},
    {'title': 'Аспиранты', 'description': 'Работают в лаборатории допоздна', 'context': 'Вузы',
     'holder': 'Аспирант', 'grade': 'C', 'reason': 'Мало денег'},
    {'title': 'Сотрудники офисов', 'description': 'Обедают за столом', 'context': 'Бизнес-центры',
     'holder': 'Сотрудник или работодатель', 'grade': 'Z', 'reason': 'Неясно'},
    {'title': 'Фантазия', 'description': 'Сэкономят 47% бюджета', 'context': 'Любые',
     'holder': 'Все', 'grade': 'A', 'reason': 'Рост 300%'},
]


def flat_segments(items):
    return {f'seg{number}_{key}': value for number, item in enumerate(items, 1) for key, value in item.items()}


SEGMENTS = flat_segments(SEGMENT_LIST)
BRAINSTORM = {
    'sit1_doing': 'Готовится к сессии ночью', 'sit1_want': 'Не тратить время на готовку',
    'sit1_promise': 'Еда к двери за полчаса',
    'sit2_doing': 'Ест лапшу третий день', 'sit2_want': 'Почувствовать заботу', 'sit2_promise': 'Домашняя еда',
    'jtbd_functional_question': 'Как быстро поесть?', 'jtbd_functional_value': 'Готовый обед рядом',
    'jtbd_social_question': 'Что скажут соседи?', 'jtbd_social_value': 'Можно заказать на компанию',
    'jtbd_magic_question': 'Нестандартное', 'jtbd_magic_value': 'Отбросить',
}
VALUE = {
    'vp_audience': 'первокурсникам', 'vp_problem': 'нехваткой времени', 'vp_method': 'доставки обедов',
    'vp_result': 'домашнюю еду',
    'vp_statement1': 'Мы это домашняя кухня для студентов, которые хотят есть нормально',
    'vp_statement2': 'Мы это столовая в общежитии для тех, кто хочет экономить',
    'vp_statement3': 'Мы это кухня для 10000 студентов',
    'vp_analogy1': 'Мама на расстоянии одного клика',
    'vp_role': 'первокурсник в общежитии', 'vp_want': 'нормально питаться во время учёбы.',
    'vp_because': 'после пар нет сил и времени готовить', 'vp_harm': 'time, money, fame',
    'vp_harm_text': 'Тратит вечер на готовку или переплачивает за доставку',
    'jtbd_functional': 'Быстро и сытно поесть между парами', 'jtbd_social': 'Не стесняться своей еды перед соседями',
    'jtbd_emotional': 'Почувствовать заботу вдали от дома', 'jtbd_spiritual': 'Отбросить',
    'vp_question': '',
}
FULL_PICTURE = {'customer': {'text': 'Первокурсники', 'status': 'fact'},
                'problem': {'text': 'Нет времени готовить', 'status': 'fact'},
                'solution': {'text': 'Доставка домашних обедов в общежитие', 'status': 'guess'}}


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
        self.assertContains(self.client.get(self.page), 'Брейншторм откроется, когда хотя бы один сегмент')
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
        ProjectPicture.objects.create(startup=self.startup, facts=FULL_PICTURE)
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

    def test_value_gives_problem_template_and_jtbd_from_picture(self):
        ProjectPicture.objects.create(startup=self.startup, facts=FULL_PICTURE)
        with reply(VALUE):
            self.client.post(self.url('value_generate'))
        canvas = ValueCanvas.objects.get(startup=self.startup)
        problem = canvas.options['problem']
        self.assertEqual(problem['text'], 'Первокурсник в общежитии хочет нормально питаться во время учёбы, '
                                          'но не может, потому что после пар нет сил и времени готовить')
        self.assertEqual(problem['harm'], ['money', 'time'])  # Only known kinds, fixed order.
        self.assertEqual(set(canvas.options['jobs']), {'functional', 'social', 'emotional'})
        page = self.client.get(self.page)
        for text in ('Проблема', 'деньги', 'время', 'Тратит вечер на готовку', 'Социальная',
                     'Почувствовать заботу вдали от дома', 'Сравнение «X для Y»'):
            self.assertContains(page, text)
        # The problem goes to the project picture as a guess, the jobs to step 2 as chosen.
        self.client.post(self.url('value_problem_adopt'))
        picture = ProjectPicture.objects.get(startup=self.startup)
        self.assertEqual(picture.facts['problem'], {'text': problem['text'], 'status': 'guess'})
        self.assertEqual(picture.facts['customer']['text'], 'Первокурсники')
        self.assertEqual(canvas.options['formula'], ['Наш продукт помогает первокурсникам справиться с нехваткой '
                                                     'времени с помощью доставки обедов и получить домашнюю еду'])
        self.assertTrue(canvas.options['formula_complete'])
        self.assertEqual(canvas.options['question'], '')
        self.client.post(self.url('value_jobs_adopt'))
        self.client.post(self.url('value_jobs_adopt'))
        canvas.refresh_from_db()
        self.assertEqual([(job['kind'], job['chosen']) for job in canvas.jobs],
                         [('functional', True), ('social', True), ('emotional', True)])
        self.assertContains(self.client.get(self.page), 'Быстро и сытно поесть между парами')  # Visible without segment A.
        self.assertIn('Почувствовать заботу вдали от дома', picture_context(self.startup) + str(canvas.jobs))

    def test_adopt_without_suggestions_is_refused(self):
        response = self.client.post(self.url('value_problem_adopt'), follow=True)
        self.assertContains(response, 'Сначала нажмите «Сформулировать ценность»')
        self.assertFalse(ProjectPicture.objects.filter(startup=self.startup).exists())
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(self.url('value_jobs_adopt')).status_code, 404)

    def test_invented_numbers_drop_problem_and_jobs(self):
        ProjectPicture.objects.create(startup=self.startup, facts=FULL_PICTURE)
        data = {**VALUE, 'vp_harm_text': 'Теряет 5000 рублей в месяц', 'jtbd_functional': 'Поесть за 15 минут',
                'jtbd_social': 'Угостить соседа', 'jtbd_emotional': 'Спокойствие'}
        with reply(data):
            self.client.post(self.url('value_generate'))
        canvas = ValueCanvas.objects.get(startup=self.startup)
        self.assertEqual(canvas.options['problem']['harm_text'], 'неизвестно')
        self.assertEqual(set(canvas.options['jobs']), {'social', 'emotional'})

    def test_what_is_not_in_picture_stays_unknown_with_one_question(self):
        # Picture knows the customer (and the problem from the questionnaire), but not the solution.
        ProjectPicture.objects.create(startup=self.startup, facts={'customer': {'text': 'Первокурсники', 'status': 'fact'}})
        data = {**VALUE, 'vp_question': 'Хорошо. Как именно вы будете кормить студентов? И сколько это стоит?'}
        with reply(data) as model:
            self.client.post(self.url('value_generate'))
        payload = json.loads(model.call_args.args[1])
        self.assertEqual(payload['картина проекта']['solution'], 'неизвестно')
        options = ValueCanvas.objects.get(startup=self.startup).options
        self.assertEqual(options['formula'], ['Наш продукт помогает первокурсникам справиться с нехваткой времени '
                                              'с помощью [неизвестно] и получить [неизвестно]'])
        self.assertFalse(options['formula_complete'])
        self.assertEqual((options['analogies'], options['statements']), ([], []))  # Would be invented.
        self.assertEqual(options['question'], 'Как именно вы будете кормить студентов?')  # Exactly one question.
        self.assertEqual(options['unknown'], ['solution'])
        page = self.client.get(self.page)
        self.assertContains(page, 'Бруно спрашивает')
        self.assertContains(page, 'неизвестно: решение')
        self.assertContains(page, 'есть пробелы, ответьте на вопрос Бруно')
        response = self.client.post(self.url('value_save'), {'value': options['formula'][0]}, follow=True)
        self.assertContains(response, 'осталось «неизвестно»')
        self.assertFalse(ValueCanvas.objects.get(startup=self.startup).value)
        # The answer goes to the project picture, and the next run has no gaps.
        self.client.post(self.url('value_answer'), {'answer': 'Доставка домашних обедов в общежитие'})
        picture = ProjectPicture.objects.get(startup=self.startup)
        self.assertEqual(picture.facts['solution'], {'text': 'Доставка домашних обедов в общежитие', 'status': 'guess'})
        with reply(VALUE):
            self.client.post(self.url('value_generate'))
        options = ValueCanvas.objects.get(startup=self.startup).options
        self.assertTrue(options['formula_complete'])
        self.assertEqual(options['question'], '')

    def test_everyone_is_not_a_segment(self):
        ProjectPicture.objects.create(startup=self.startup, facts={**FULL_PICTURE, 'customer': {'text': 'Все люди', 'status': 'guess'}})
        with reply(VALUE) as model:
            self.client.post(self.url('value_generate'))
        self.assertIn('это не сегмент', model.call_args.args[1])
        options = ValueCanvas.objects.get(startup=self.startup).options
        self.assertTrue(options['formula'][0].startswith('Наш продукт помогает [неизвестно] справиться'))
        self.assertTrue(options['problem']['text'].startswith('[неизвестно] хочет [неизвестно]'))
        self.assertEqual(options['jobs'], {})
        self.assertIn('«Все» — не сегмент', options['question'])
        self.assertContains(self.client.get(self.page), 'Взять в картину проекта', count=0)
        # A model answer «все, кто…» is not accepted as the audience either.
        ProjectPicture.objects.filter(startup=self.startup).update(facts=FULL_PICTURE)
        with reply({**VALUE, 'vp_audience': 'всем, кто хочет есть'}):
            self.client.post(self.url('value_generate'))
        self.assertIn('помогает [неизвестно]', ValueCanvas.objects.get(startup=self.startup).options['formula'][0])
        # Answers and manual segments «все» are refused.
        ProjectPicture.objects.filter(startup=self.startup).update(facts={})
        with reply(VALUE):
            self.client.post(self.url('value_generate'))
        response = self.client.post(self.url('value_answer'), {'answer': 'Любой человек'}, follow=True)
        self.assertContains(response, '«Все» — не сегмент')
        response = self.client.post(self.url('value_segments_save'), {'new_title': 'Все', 'new_grade': 'A'}, follow=True)
        self.assertContains(response, '«Все» — не сегмент')
        self.assertEqual(ValueCanvas.objects.get(startup=self.startup).segments, [])
        with reply(flat_segments([{**SEGMENT_LIST[0], 'title': 'Все люди'}, SEGMENT_LIST[1]])):
            self.client.post(self.url('value_segments'))
        self.assertEqual([item['title'] for item in ValueCanvas.objects.get(startup=self.startup).segments], ['Аспиранты'])

    def test_is_everyone(self):
        for text in ('все', 'Все люди', 'для всех', 'любой человек', 'Каждому', 'все, кто хочет есть',
                     'широкая аудитория', 'массовый рынок', 'люди'):
            self.assertTrue(value_canvas.is_everyone(text), text)
        for text in ('все студенты', 'каждый первокурсник', 'Владельцы кофеен', ''):
            self.assertFalse(value_canvas.is_everyone(text), text)

    def test_chosen_value_goes_to_project_pitch_problem_and_card(self):
        from founder.services.project_cards import card_values, get_card, save_card
        self.startup.one_line_pitch = 'Старый питч'
        self.startup.save()
        card = get_card(self.startup)  # Draft created from the profile: tagline = old pitch.
        save_card(card, {**card_values(card), 'problem': 'Проблема, которую основатель написал сам'}, card.revision)
        ProjectPicture.objects.create(startup=self.startup, facts=FULL_PICTURE)
        with reply(VALUE):
            self.client.post(self.url('value_generate'))
        canvas = ValueCanvas.objects.get(startup=self.startup)
        chosen = canvas.options['statements'][0]
        response = self.client.post(self.url('value_save'), {'value': chosen}, follow=True)
        self.assertContains(response, 'записано в питч проекта, проблему проекта и черновик карточки')
        self.startup.refresh_from_db()
        self.assertEqual(self.startup.one_line_pitch, chosen)
        self.assertEqual(self.startup.problem, canvas.options['problem']['text'])
        card.refresh_from_db()
        self.assertEqual(card.tagline, chosen)  # Untouched field follows the profile.
        self.assertEqual(card.problem, 'Проблема, которую основатель написал сам')  # Founder's own edit is kept.
        self.assertIsNone(card.published_at)  # Nothing is published automatically.
        # Pitch and sharks read the project pitch from the profile.
        from founder.services.onboarding import startup_profile_context
        self.assertIn(chosen, startup_profile_context(self.startup))

    def test_incomplete_problem_is_not_written_and_long_value_fits_pitch(self):
        ProjectPicture.objects.create(startup=self.startup, facts={**FULL_PICTURE, 'customer': {'text': 'все', 'status': 'guess'}})
        with reply(VALUE):
            self.client.post(self.url('value_generate'))
        long_value = 'Мы это домашняя кухня для первокурсников ' + 'которые хотят есть нормально ' * 12
        self.client.post(self.url('value_save'), {'value': long_value})
        self.startup.refresh_from_db()
        self.assertEqual(self.startup.problem, 'Студентам некогда готовить')  # Problem had «неизвестно».
        self.assertLessEqual(len(self.startup.one_line_pitch), 300)
        self.assertTrue(self.startup.one_line_pitch.startswith('Мы это домашняя кухня'))
        pitch = self.startup.one_line_pitch
        self.assertTrue(pitch.endswith('…'))
        self.assertEqual(long_value[len(pitch) - 1], ' ')  # Cut at a word boundary, not mid-word.
        self.assertFalse(ValueCanvas.objects.get(startup=self.startup).value.endswith(' '))

    def test_prompts_ask_for_flat_keys(self):
        for prompt in (value_canvas.SEGMENTS_PROMPT, value_canvas.BRAINSTORM_PROMPT, value_canvas.VALUE_PROMPT):
            self.assertIn('Никаких вложенных объектов', prompt)
        self.assertIn('seg1_title', value_canvas.SEGMENTS_PROMPT)
        self.assertIn('sit1_doing', value_canvas.BRAINSTORM_PROMPT)
        self.assertIn('jtbd_emotional_value', value_canvas.BRAINSTORM_PROMPT)
        for key in ('vp_audience', 'vp_analogy1', 'vp_role', 'vp_harm', 'jtbd_functional', 'vp_question'):
            self.assertIn(key, value_canvas.VALUE_PROMPT)

    def test_flat_keys_found_when_gigachat_nests_them_anyway(self):
        ProjectPicture.objects.create(startup=self.startup, facts=FULL_PICTURE)
        nested = {'result': {'formula': {k: VALUE[k] for k in ('vp_audience', 'vp_problem', 'vp_method', 'vp_result')},
                             'problem': {'VP_ROLE': VALUE['vp_role'], 'vp_want': VALUE['vp_want'],
                                         'vp_because': VALUE['vp_because'], 'vp_harm': ['время', 'денег']},
                             'vp_analogies': ['Мама на расстоянии одного клика', 'Столовая, но в комнате'],
                             'jtbd': [{'jtbd_functional': VALUE['jtbd_functional']}]}}
        with reply(nested):
            self.client.post(self.url('value_generate'))
        options = ValueCanvas.objects.get(startup=self.startup).options
        self.assertTrue(options['formula_complete'])
        self.assertEqual(options['analogies'], ['Мама на расстоянии одного клика', 'Столовая, но в комнате'])
        self.assertEqual(options['problem']['harm'], ['money', 'time'])  # Russian words in any case.
        self.assertTrue(options['problem']['complete'])
        self.assertEqual(options['jobs'], {'functional': VALUE['jtbd_functional']})
        # Segments and brainstorm, also nested by the model.
        with reply({'segments': SEGMENTS}):
            self.client.post(self.url('value_segments'))
        canvas = ValueCanvas.objects.get(startup=self.startup)
        self.assertEqual([item['title'] for item in canvas.segments][:2], ['Первокурсники в общежитиях', 'Аспиранты'])
        with reply({'brainstorm': BRAINSTORM}):
            self.client.post(self.url('value_brainstorm'))
        canvas.refresh_from_db()
        self.assertEqual(len(canvas.situations), 2)
        self.assertEqual([job['kind'] for job in canvas.jobs], ['functional', 'social'])

    def test_empty_or_broken_reply_is_an_error_not_an_empty_value(self):
        ProjectPicture.objects.create(startup=self.startup, facts=FULL_PICTURE)
        with reply({'answer': {'nothing': {}}}):
            response = self.client.post(self.url('value_generate'), follow=True)
        self.assertContains(response, 'не сформулировал ценность')
        self.assertEqual(ValueCanvas.objects.get(startup=self.startup).options, {})

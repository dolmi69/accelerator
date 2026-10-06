from django.test import TestCase

from founder.models import ChatSession, StartupProfile, User
from founder.services.ai import system_prompt
from founder.services.bruno import (
    contradiction, conversation_notes, polish_stream, style_issues, tidy_reply, tidy_stream,
    wants_long_answer,
)


class BrunoPersonaTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            username="anna", email="anna@example.com", password="StrongPass987!",
            display_name="Анна Петрова",
        )
        self.startup = StartupProfile.objects.create(owner=user, name="Orbit")

    def test_cofounder_prompt_has_persona_examples_and_name(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        prompt = system_prompt(session, [], [{"role": "user", "content": "привет"}])
        self.assertIn("Ты Бруно", prompt)
        self.assertIn("Примеры тона", prompt)
        self.assertIn("Основателя зовут Анна", prompt)
        self.assertIn("Составить таблицу", prompt)

    def test_notes_track_covered_axes_and_asked_questions(self):
        messages = [
            {"role": "assistant", "content": "Кто твой главный клиент сегодня?"},
            {"role": "user", "content": "Клиенты — студенты, подписка 300 руб в месяц"},
        ]
        notes = conversation_notes(messages)
        self.assertIn("Рынок", notes.split("Ещё не обсуждали")[0])
        self.assertIn("Финансы", notes.split("Ещё не обсуждали")[0])
        self.assertIn("Команда", notes.split("Ещё не обсуждали")[1])
        self.assertIn("Кто твой главный клиент сегодня?", notes)

    def test_notes_suggest_table_once_when_picture_is_full(self):
        answers = ["продукт — приложение", "клиенты студенты", "цена 300 руб",
                   "команда из двух разработчиков", "проблема в поиске", "ещё детали"]
        messages = []
        for answer in answers:
            messages += [{"role": "assistant", "content": "Расскажи ещё?"},
                         {"role": "user", "content": answer}]
        self.assertIn("Составить таблицу", conversation_notes(messages))
        messages.insert(0, {"role": "assistant", "content": "Нажми «Составить таблицу»."})
        self.assertNotIn("можно один раз предложить", conversation_notes(messages))

    def test_tidy_removes_canned_openers_and_ai_disclaimers(self):
        self.assertEqual(tidy_reply("Отличный вопрос! Кто платит?"), "Кто платит?")
        self.assertEqual(tidy_reply("Конечно, давай разберём."), "Давай разберём.")
        self.assertEqual(
            tidy_reply("Как языковая модель, я не могу знать рынок. Давай проверим спрос."),
            "Давай проверим спрос.",
        )
        self.assertEqual(tidy_reply("Привет!"), "Привет!")

    def test_tidy_stream_cleans_head_and_keeps_the_rest(self):
        chunks = ["Отличн", "ая идея! Смотри, ", "главное сейчас — ", "найти первых ", "клиентов. ", "Кто они?"]
        self.assertEqual("".join(tidy_stream(chunks)),
                         "Смотри, главное сейчас — найти первых клиентов. Кто они?")
        self.assertEqual("".join(tidy_stream(["Привет", "!"])), "Привет!")


class BrunoV2Tests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="ivan", email="ivan@example.com")
        self.startup = StartupProfile.objects.create(owner=user, name="Cup", stage="traction")
        self.session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)

    def test_long_answer_detection_ignores_plain_mentions(self):
        self.assertTrue(wants_long_answer("Сделай полный анализ моего проекта"))
        self.assertTrue(wants_long_answer("что мне делать дальше?"))
        self.assertTrue(wants_long_answer("Распиши план на месяц"))
        self.assertFalse(wants_long_answer("Планируем брать 4900 в месяц"))
        self.assertFalse(wants_long_answer("клиенты студенты"))

    def test_prompt_has_writing_rules_stage_advice_and_long_form_only_on_request(self):
        short = system_prompt(self.session, [], [{"role": "user", "content": "у нас 12 клиентов"}])
        self.assertIn("Как звучать по-человечески", short)
        self.assertIn("Стадия «Первые результаты»", short)
        self.assertNotIn("Стадия «Идея»", short)
        self.assertNotIn("развёрнутый ответ (разбор", short)
        long = system_prompt(self.session, [], [{"role": "user", "content": "Что мне делать дальше?"}])
        self.assertIn("развёрнутый ответ (разбор", long)

    def test_pitch_mode_never_switches_to_long_form(self):
        pitch = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.PITCH)
        prompt = system_prompt(pitch, [], [{"role": "user", "content": "Сделай анализ"}])
        self.assertIn("инвестора", prompt)
        self.assertNotIn("развёрнутый ответ (разбор", prompt)

    def test_tidy_stream_removes_bold_split_between_chunks(self):
        chunks = ["Главный риск: *", "*маржа", "*", "* на чашку. Сколько подписчиков пьют каждый день?"]
        self.assertEqual("".join(tidy_stream(chunks)),
                         "Главный риск: маржа на чашку. Сколько подписчиков пьют каждый день?")

    def test_style_issues_flags_slop_and_accepts_clean_reply(self):
        issues = style_issues("Отличный вопрос! Важно отметить — это не просто сервис — это экосистема. Как? Когда?")
        self.assertTrue(any("вопросов" in item for item in issues))
        self.assertTrue(any("штампы" in item for item in issues))
        self.assertTrue(any("тире" in item for item in issues))
        self.assertEqual(style_issues("Сорок человек на старте уже неплохо. Сколько вернулись?"), [])

    def test_notes_ask_for_value_after_question_only_replies(self):
        messages = [
            {"role": "assistant", "content": "Кто твой клиент?"},
            {"role": "user", "content": "офисные сотрудники в Москве"},
            {"role": "assistant", "content": "Сколько они тратят на кофе в день?"},
            {"role": "user", "content": "рублей 300"},
        ]
        self.assertIn("сначала дай основателю пользу", conversation_notes(messages))

    def test_contradiction_between_facts_is_flagged_but_plans_are_not(self):
        messages = [
            {"role": "user", "content": "У нас пока нет ни одного клиента, мы только собрали дрон"},
            {"role": "assistant", "content": "Кому покажешь дрон первым?"},
            {"role": "user", "content": "Кстати, у нас уже 12 платящих фермеров с прошлого сезона"},
        ]
        self.assertIn("нет ни одного клиента", contradiction(messages))
        self.assertIn("ВНИМАНИЕ, противоречие", conversation_notes(messages))
        messages[-1] = {"role": "user", "content": "Хотим 12 платящих клиентов к лету"}
        self.assertIsNone(contradiction(messages))

    def test_polish_keeps_one_self_contained_question_and_streams_sentences(self):
        chunks = ["Хорошее начало, но нужна ясность. Почему именно ", "такая цена? Какие преимущества ",
                  "получает клиент за эти деньги?"]
        self.assertEqual("".join(polish_stream(chunks)),
                         "Хорошее начало, но нужна ясность. Какие преимущества получает клиент за эти деньги?")
        # Короткое уточнение остаётся вместе с вопросом, вопросы внутри текста тоже.
        self.assertEqual("".join(polish_stream(["Кто клиент? Студенты или школьники?"])),
                         "Кто клиент? Студенты или школьники?")
        self.assertEqual("".join(polish_stream(["Зачем? Чтобы проверить спрос. Кому позвонишь первым?"])),
                         "Зачем? Чтобы проверить спрос. Кому позвонишь первым?")
        long_form = "1. Позвони клиентам.\n2. Что они скажут? Запиши ответы дословно?\n"
        self.assertEqual("".join(polish_stream([long_form], single_question=False)), long_form)

    def test_polish_neutralises_gendered_and_canned_phrases(self):
        self.assertEqual("".join(polish_stream(["Важно понимать спрос. Готов начать?"])),
                         "Надо понять спрос. Начнём?")
        self.assertEqual("".join(polish_stream(["Ты готов попробовать с пятью клиентами?"])),
                         "Попробуем с пятью клиентами?")

from django.test import TestCase

from founder.models import ChatSession, StartupProfile, User
from founder.services.ai import system_prompt
from founder.services.bruno import conversation_notes, tidy_reply, tidy_stream


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
        self.assertIn("Примеры хорошего тона", prompt)
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

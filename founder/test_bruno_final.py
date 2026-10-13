import json
from io import StringIO
from unittest.mock import patch

import httpx
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import (BrunoTask, ChatMessage, ChatSession, MessageFeedback, StartupMemory,
                            StartupProfile, User)
from founder.services import gigachat
from founder.services.ai import system_prompt
from founder.services.bruno import (conversation_notes, disengaged_streak, founder_gender, looks_like_evidence,
                                    project_status)
from founder.services.memory import relevant_memories, remember_user_message
from founder.services.model_json import load_model_json
from founder.services.pitch import normalise_report
from founder.services.workbench import _task_items, _valid_tasks


def user_turns(*texts):
    messages = []
    for text in texts:
        messages += [{"role": "assistant", "content": "Расскажи подробнее про клиентов?"},
                     {"role": "user", "content": text}]
    return messages


class BehaviourNotesTests(TestCase):
    def test_founder_gender_is_read_only_from_founder_words(self):
        self.assertEqual(founder_gender(user_turns("Я сама сшила первые сумки")), "female")
        self.assertEqual(founder_gender(user_turns("Я уже решил продавать на ярмарках")), "male")
        self.assertIsNone(founder_gender(user_turns("Мы делаем запись для клиник")))
        # Бруно всегда на «вы»: пол основателя в обращении больше не нужен.
        notes = conversation_notes(user_turns("Мы делаем запись для клиник"))
        self.assertIn("строго на «вы»", notes)
        self.assertIn("строго на «вы»", conversation_notes(user_turns("Я сама сшила первые сумки")))

    def test_one_word_streak_asks_for_summary_instead_of_new_tasks(self):
        messages = user_turns("Делаем бота для подготовки к сессии", "ок", "понял", "угу")
        self.assertEqual(disengaged_streak(messages), 3)
        self.assertIn("односложно уже 3 раз", conversation_notes(messages))
        self.assertEqual(disengaged_streak(user_turns("ок", "сколько стоит 1 клиент?")), 0)

    def test_evidence_detection_and_single_diary_hint(self):
        self.assertTrue(looks_like_evidence("Неявки упали с 18% до 9% за два месяца, это по их журналу"))
        self.assertFalse(looks_like_evidence("Хотим брать 4900 в месяц за клинику"))
        self.assertFalse(looks_like_evidence("Команда из двух человек"))
        messages = user_turns("Пятеро из 30 владельцев согласились на пилот")
        self.assertIn("Записать в дневник", conversation_notes(messages))
        messages.insert(0, {"role": "assistant", "content": "Нажми «Записать в дневник»."})
        self.assertNotIn("похоже на результат проверки", conversation_notes(messages))


class ProjectStatusTests(TestCase):
    def test_prompt_knows_open_tasks_and_today(self):
        user = User.objects.create_user(username="status", email="status@example.test")
        startup = StartupProfile.objects.create(owner=user, name="Cup")
        BrunoTask.objects.create(startup=startup, axis="finance", title="Пересчитать цену",
                                 instructions="Посчитайте затраты.", success_criterion="Есть расчёт.")
        self.assertIn("«Пересчитать цену»", project_status(startup))
        session = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.COFOUNDER)
        prompt = system_prompt(session, [], [{"role": "user", "content": "привет"}])
        self.assertIn("Задания в работе", prompt)
        self.assertIn("Сегодня ", prompt)
        empty = StartupProfile.objects.create(owner=user, name="Empty")
        self.assertEqual(project_status(empty), "")


class StemMemoryTests(TestCase):
    def test_other_word_forms_find_the_old_message(self):
        user = User.objects.create_user(username="memory", email="memory@example.test")
        startup = StartupProfile.objects.create(owner=user, name="Dental")
        session = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.COFOUNDER)
        message = ChatMessage.objects.create(session=session, role="user",
                                             content="Конверсия звонков главврачам 1 из 30.")
        remember_user_message(message)
        found = relevant_memories(startup, "Какую конверсию звонков я называл?")
        self.assertEqual([memory.content for memory in found], [message.content])
        self.assertEqual(StartupMemory.objects.count(), 1)


class GigaChatFailoverTests(TestCase):
    def setUp(self):
        gigachat._route_state["primary_down_until"] = 0.0
        self.addCleanup(gigachat._route_state.update, {"primary_down_until": 0.0})

    @override_settings(GIGACHAT_MODEL="GigaChat-3-Pro")
    def test_unreachable_primary_switches_to_fallback_model_and_remembers(self):
        seen = []

        def handler(request):
            seen.append((request.url.host, json.loads(request.content)["model"]))
            if request.url.host == "api.giga.chat":
                raise httpx.ConnectTimeout("handshake timed out", request=request)
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Ок"}}]}\n\ndata: [DONE]\n\n')

        with patch.object(gigachat, "API_URL", "https://api.giga.chat/v1"), \
                patch.object(gigachat, "FALLBACK_URL", "https://gigachat.devices.sberbank.ru/api/v1"), \
                patch.object(gigachat, "_headers", return_value={}), \
                patch.object(gigachat, "_client", side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handler))):
            self.assertEqual("".join(gigachat.stream_chat("Промпт", [])), "Ок")
            self.assertEqual(gigachat.active_model(), gigachat.FALLBACK_MODEL)
            seen.clear()
            self.assertEqual("".join(gigachat.stream_chat("Промпт", [])), "Ок")
        self.assertEqual(seen, [("gigachat.devices.sberbank.ru", gigachat.FALLBACK_MODEL)])


class TolerantFormatsTests(TestCase):
    def test_fallback_task_shape_becomes_valid_tasks(self):
        raw = ('```json\n{"market": {"action": "Позвоните пяти клиникам и спросите о неявках.", '
               '"criterion": "Записаны пять ответов."}, "team": {"action": "Посмотреть вебинар", "criterion": ""}}\n```')
        items = _valid_tasks(_task_items(load_model_json(raw), ["market", "team"]), ["market", "team"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["axis"], "market")
        self.assertEqual(items[0]["success_criterion"], "Записаны пять ответов.")
        self.assertTrue(items[0]["title"].startswith("Позвоните пяти клиникам"))

    def test_fallback_report_shape_is_normalised_and_invented_quotes_dropped(self):
        answers = ["Будем брать 4900 в месяц"]
        payload = {"score": "55", "summary": {"strengths": ["Есть цена."], "gaps": ["Нет каналов."]},
                   "mistakes": [
                       {"quote": "Будем брать 4900 в месяц", "comment": "Цена без расчёта.",
                        "recommendation": "Назовите, из чего складывается цена."},
                       {"quote": "Мы уже продали 100 подписок", "comment": "Выдумка.", "recommendation": "—"},
                   ]}
        score, summary, mistakes = normalise_report(payload, answers)
        self.assertEqual(score, 55)
        self.assertIn("Сильные стороны: Есть цена.", summary)
        self.assertEqual([item["quote"] for item in mistakes], ["Будем брать 4900 в месяц"])
        payload["mistakes"] = payload["mistakes"][1:]
        with self.assertRaises(ValueError):
            normalise_report(payload, answers)


@override_settings(AI_PROVIDER="demo")
class ChatActionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="actions", email="actions@example.test")
        self.startup = StartupProfile.objects.create(owner=self.user, name="ДентаСлот")
        self.session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        self.greeting = ChatMessage.objects.create(session=self.session, role="assistant", provider="system",
                                                   content="Привет!")
        self.fact = ChatMessage.objects.create(session=self.session, role="user",
                                               content="Неявки упали с 18% до 9% за два месяца по журналу клиники")
        self.reply = ChatMessage.objects.create(session=self.session, role="assistant", provider="gigachat",
                                                content="Это доказательство, сохрани его.")
        self.client.force_login(self.user)

    def feedback_url(self, message):
        return reverse("message_feedback", args=[self.startup.pk, self.session.pk, message.pk])

    def test_feedback_is_saved_and_shown(self):
        response = self.client.post(self.feedback_url(self.reply), {"rating": "down", "comment": "Слишком общо"})
        self.assertRedirects(response, reverse("chat_detail", args=[self.startup.pk, self.session.pk])
                             + f"#message-{self.reply.pk}", fetch_redirect_response=False)
        feedback = MessageFeedback.objects.get()
        self.assertEqual((feedback.rating, feedback.comment), (-1, "Слишком общо"))
        self.client.post(self.feedback_url(self.reply), {"rating": "up", "comment": "игнор"})
        feedback.refresh_from_db()
        self.assertEqual((feedback.rating, feedback.comment), (1, ""))
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, self.session.pk]))
        self.assertContains(page, 'aria-pressed="true"')
        self.assertContains(page, f"?message={self.fact.pk}")

    def test_feedback_rejects_system_messages_strangers_and_bad_values(self):
        self.assertEqual(self.client.post(self.feedback_url(self.greeting), {"rating": "up"}).status_code, 404)
        self.assertEqual(self.client.post(self.feedback_url(self.reply), {"rating": "maybe"}).status_code, 400)
        stranger = User.objects.create_user(username="stranger", email="stranger@example.test")
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(self.feedback_url(self.reply), {"rating": "up"}).status_code, 404)
        self.assertFalse(MessageFeedback.objects.exists())

    def test_diary_form_is_prefilled_from_owned_message_only(self):
        url = reverse("evidence_create", args=[self.startup.pk])
        page = self.client.get(url, {"message": self.fact.pk})
        self.assertContains(page, "Неявки упали с 18% до 9%")
        self.assertEqual(self.client.get(url, {"message": self.reply.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {"message": "not-a-uuid"}).status_code, 404)

    def test_feedback_export_command(self):
        MessageFeedback.objects.create(message=self.reply, rating=-1, comment="Мимо")
        out = StringIO()
        call_command("bruno_feedback", stdout=out)
        self.assertIn("👎 1", out.getvalue())


class GenderAndPlanTests(TestCase):
    def test_past_tense_follows_known_gender_or_stays_neutral(self):
        from founder.services.bruno import polish_stream
        # «Ты» переводится в «вы», а форма на «вы» не зависит от пола.
        self.assertEqual("".join(polish_stream(["Какие доказательства ты показал?"])),
                         "Какие доказательства вы показали?")
        for gender in ("female", "male", None):
            self.assertEqual("".join(polish_stream(["Ты уже решил, кому продавать?"], gender=gender)),
                             "Вы уже решили, кому продавать?")

    def test_word_plan_alone_does_not_switch_to_long_answer(self):
        from founder.services.bruno import wants_long_answer
        self.assertFalse(wants_long_answer("Хочу бота, который составляет план подготовки к сессии"))
        self.assertTrue(wants_long_answer("Составь мне план"))
        self.assertTrue(wants_long_answer("Распиши план на месяц"))

    def test_generic_closer_is_dropped_in_long_answers_but_specific_question_stays(self):
        from founder.services.bruno import polish_stream
        self.assertEqual("".join(polish_stream(["1. Позвоните.\n2. Запишите.\nЧерез неделю увидим. Начнем?"],
                                               single_question=False)).strip(),
                         "1. Позвоните.\n2. Запишите.\nЧерез неделю увидим.")
        self.assertTrue("".join(polish_stream(["1. Позвоните.\nС какого шага начнём?"], single_question=False))
                        .endswith("С какого шага начнём?"))

"""Панель акул: очередь, реплики соседей, голосование и условия в задания."""
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import (
    BrunoTask, ChatMessage, ChatSession, PanelVerdict, PitchReport, StartupMemory, StartupProfile, User,
)
from founder.services import panel
from founder.services.ai import system_prompt
from founder.services.bruno import polish_stream
from founder.services.memory import conversation_context


def answer(session, text):
    return ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=text)


def shark_says(session, speaker, text):
    return ChatMessage.objects.create(session=session, role=ChatMessage.Role.ASSISTANT, speaker=speaker, content=text)


MODEL_VOTES = {
    "timur": {"decision": "invest", "reason": "Живой пользователь у вас уже есть.",
              "quote": "администратор обзванивала пациентов вручную", "condition_title": "Три постоянных клиники",
              "condition_steps": "Подключите три клиники.",
              "condition_done_when": "Три клиники пользуются сервисом месяц."},
    "oleg": {"decision": "pass", "reason": "Канала продаж без знакомых я не услышал.",
             "quote": "ищем клиентов через знакомых", "condition_title": "Канал без знакомых",
             "condition_steps": "Проверьте холодные звонки.", "condition_done_when": "Десять встреч за месяц."},
    "margarita": {"decision": "invest", "reason": "Цена 4900 ₽ понятна. Осталось проверить оплату.",
                  "quote": "берём 4900 в месяц", "condition_title": "Две оплаты",
                  "condition_steps": "Возьмите оплату с двух клиник.",
                  "condition_done_when": "Две клиники заплатили за второй месяц."},
}


def model_vote(key, **changes):
    return json.dumps({**MODEL_VOTES[key], **changes}, ensure_ascii=False)


ANSWERS = [
    "Администратор обзванивала пациентов вручную, два часа в день",
    "Пока ищем клиентов через знакомых",
    "Мы берём 4900 в месяц с клиники",
    "Пациент получает напоминание в мессенджере",
    "Конкуренты делают общую CRM, мы только запись",
    "Неявок становится меньше на треть",
    "Команда: я и разработчик",
]


class PanelSetup(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="founder", email="founder@example.test")
        self.startup = StartupProfile.objects.create(
            owner=self.user, name="ДентаСлот", stage="validation",
            one_line_pitch="Онлайн-запись для стоматологий", target_customer="Частные стоматологии",
        )
        self.client.force_login(self.user)

    def panel_with_answers(self, count):
        session = panel.create_panel(self.startup)
        for index in range(count):
            turn = panel.next_turn(session) if index else panel.Turn("timur")
            if index:
                shark_says(session, turn.speaker, "Вопрос?")
            answer(session, ANSWERS[index % len(ANSWERS)])
        return session


class TurnTests(PanelSetup):
    def test_panel_opens_with_three_sharks_and_timur_asks_first(self):
        session = panel.create_panel(self.startup)
        self.assertEqual(session.mode, ChatSession.Mode.PANEL)
        self.assertEqual(list(session.messages.values_list("speaker", flat=True)), ["margarita", "oleg", "timur"])
        self.assertIn("Частные стоматологии", session.messages.last().content)
        self.assertEqual(panel.current_speaker(session), "timur")

    def test_sharks_take_turns_and_press_an_evasive_answer_once(self):
        session = panel.create_panel(self.startup)
        answer(session, "Владелец клиники платит за меньшее число неявок")
        self.assertEqual(panel.next_turn(session), panel.Turn("oleg", answers=1))
        shark_says(session, "oleg", "Где возьмёте первых клиентов?")
        answer(session, "Не знаю")
        self.assertEqual(panel.next_turn(session), panel.Turn("oleg", pressing=True, answers=2))
        shark_says(session, "oleg", "Прикиньте хотя бы канал.")
        answer(session, "Без понятия, честно")
        # Дожим только один раз: дальше ход у Маргариты.
        self.assertEqual(panel.next_turn(session).speaker, "margarita")
        self.assertFalse(panel.next_turn(session).pressing)

    def test_neighbour_aside_is_allowed_every_third_turn_and_never_while_pressing(self):
        session = panel.create_panel(self.startup)
        answer(session, "Клиника платит за запись")
        shark_says(session, "oleg", "Канал?")
        answer(session, "Сарафан между врачами")
        self.assertTrue(panel.next_turn(session).aside)
        self.assertFalse(panel.Turn("oleg", pressing=True).aside)

    def test_evasive_detection(self):
        for text in ("не знаю", "Хз", "Сложно сказать, мы не считали", "?!"):
            self.assertTrue(panel.is_evasive(text), text)
        for text in ("Нет", "Владелец клиники", "4900 в месяц", "Не считали, но реклама примерно 3000 на клинику"):
            self.assertFalse(panel.is_evasive(text), text)


class PromptTests(PanelSetup):
    def test_prompt_speaks_as_active_shark_with_economics_for_margarita(self):
        session = panel.create_panel(self.startup)
        prompt = system_prompt(session, [], [{"role": "user", "content": "Берём 4900"}],
                               economics="Расчёт: прибыль 2200 ₽", turn=panel.Turn("margarita"))
        self.assertIn("Ты Маргарита, финансист-скептик", prompt)
        self.assertIn("не пересчитывай", prompt)
        self.assertIn("Расчёт: прибыль 2200 ₽", prompt)
        self.assertIn("женском", prompt)
        self.assertIn("Реплик соседей в этом ходе нет", prompt)
        pressing = system_prompt(session, [], [], turn=panel.Turn("timur", pressing=True))
        self.assertIn("Ты Тимур", pressing)
        self.assertIn("ушёл от ответа", pressing)
        last = system_prompt(session, [], [], turn=panel.Turn("oleg", answers=panel.MAX_ANSWERS))
        self.assertIn("Вопрос не задавай", last)

    def test_history_shows_shark_names_and_merges_their_lines(self):
        session = panel.create_panel(self.startup)
        latest = answer(session, "Клиника на три кресла")
        messages, _ = conversation_context(session, latest)
        self.assertEqual([item["role"] for item in messages], ["assistant", "user"])
        self.assertIn("Маргарита: Маргарита, финансы", messages[0]["content"])
        self.assertIn("Тимур: Я Тимур", messages[0]["content"])

    def test_panel_answers_are_not_remembered_as_project_facts(self):
        self.client.post(reverse("panel_create", args=[self.startup.pk]))
        session = self.startup.chat_sessions.get(mode=ChatSession.Mode.PANEL)
        with override_settings(AI_PROVIDER="demo", CHAT_BUFFERED_RESPONSES=True):
            self.client.post(reverse("chat_send", args=[self.startup.pk, session.pk]), {"content": "У нас 50 клиник"})
        self.assertFalse(StartupMemory.objects.filter(startup=self.startup).exists())

    def test_margarita_keeps_feminine_self_reference(self):
        self.assertEqual("".join(polish_stream(["Я рада цифрам."], self_male=False)), "Я рада цифрам.")
        self.assertEqual("".join(polish_stream(["Я рада цифрам."])), "Я рад цифрам.")


class SplitAsideTests(TestCase):
    def split(self, chunks, speaker="margarita"):
        return list(panel.split_aside(iter(chunks), speaker))

    def test_neighbour_line_becomes_separate_speaker(self):
        events = self.split(["Тимур: Маргарита, дай ", "человеку рассказать.\nСколько ", "стоит клиент?"])
        self.assertEqual(events[:4], [("speaker", "timur"), ("text", "Маргарита, дай человеку рассказать."),
                                      ("speaker", "margarita"), ("text", "Сколько ")])
        self.assertEqual("".join(value for kind, value in events[3:] if kind == "text"), "Сколько стоит клиент?")

    def test_plain_reply_and_own_name_stay_with_active_shark(self):
        self.assertEqual(self.split(["Сколько стоит клиент?"]), [("text", "Сколько стоит клиент?")])
        self.assertEqual(self.split(["Маргарита: Сколько стоит клиент?"]), [("text", "Сколько стоит клиент?")])
        self.assertEqual(self.split(["Смотрите: цена ниже затрат."]), [("text", "Смотрите: цена ниже затрат.")])

    def test_neighbour_name_without_main_reply_is_not_an_aside(self):
        self.assertEqual(self.split(["Олег: где каналы?"]), [("text", "где каналы?")])


@override_settings(AI_PROVIDER="demo", CHAT_BUFFERED_RESPONSES=True)
class ChatFlowTests(PanelSetup):
    def send(self, session, text):
        return self.client.post(reverse("chat_send", args=[self.startup.pk, session.pk]), {"content": text})

    def test_reply_names_speaker_and_saves_it(self):
        session = panel.create_panel(self.startup)
        body = self.send(session, "Владелец клиники").content.decode()
        events = [json.loads(line[6:]) for line in body.split("\n\n") if line.startswith("data: ")]
        self.assertEqual(events[0], {"type": "speaker", "speaker": "oleg", "name": "Олег",
                                     "title": "рыночник", "initial": "О"})
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(session.messages.last().speaker, "oleg")
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, session.pk]))
        self.assertContains(page, "Ваш ответ Олегу…")

    def test_aside_is_saved_before_the_main_reply(self):
        session = panel.create_panel(self.startup)
        self.send(session, "Владелец клиники")
        self.send(session, "Через знакомых врачей")
        main, aside = session.messages.order_by("-created_at", "-id")[:2]
        self.assertEqual((aside.speaker, aside.content), ("timur", panel.DEMO_ASIDE))
        self.assertEqual(main.speaker, "margarita")
        self.assertNotIn("Тимур:", main.content)

    def test_vote_button_appears_after_seven_answers_and_ninth_closes_the_chat(self):
        session = panel.create_panel(self.startup)
        url = reverse("chat_detail", args=[self.startup.pk, session.pk])
        for index in range(panel.MAX_ANSWERS):
            if index == panel.MIN_ANSWERS - 1:
                self.assertNotContains(self.client.get(url), reverse("panel_vote", args=[self.startup.pk, session.pk]))
            self.assertEqual(self.send(session, ANSWERS[index % len(ANSWERS)]).status_code, 200)
        page = self.client.get(url)
        self.assertContains(page, reverse("panel_vote", args=[self.startup.pk, session.pk]))
        self.assertContains(page, "Акулы услышали достаточно")
        self.assertEqual(self.send(session, "Ещё ответ").status_code, 409)

    def test_investor_page_offers_both_modes(self):
        page = self.client.get(reverse("investor", args=[self.startup.pk]))
        self.assertContains(page, reverse("pitch_create", args=[self.startup.pk]))
        self.assertContains(page, reverse("panel_create", args=[self.startup.pk]))
        self.assertContains(page, "Маргарита, финансист-скептик")


@override_settings(AI_PROVIDER="demo")
class VoteTests(PanelSetup):
    def test_vote_saves_three_votes_report_and_completes_panel(self):
        session = self.panel_with_answers(panel.MIN_ANSWERS)
        response = self.client.post(reverse("panel_vote", args=[self.startup.pk, session.pk]))
        self.assertRedirects(response, reverse("chat_detail", args=[self.startup.pk, session.pk]) + "?reveal=1#verdict",
                             fetch_redirect_response=False)
        verdict = PanelVerdict.objects.get(session=session)
        self.assertEqual([vote["shark"] for vote in verdict.votes], list(panel.ORDER))
        self.assertTrue(PitchReport.objects.filter(session=session).exists())
        session.refresh_from_db()
        self.assertIsNotNone(session.completed_at)
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, session.pk]))
        self.assertContains(page, f"{verdict.invested} из 3 акул вложились")
        self.assertContains(page, "panel.js")
        self.assertContains(self.client.get(reverse("investor", args=[self.startup.pk])), "из 3 акул вложились")

    def test_vote_needs_seven_answers(self):
        session = self.panel_with_answers(3)
        response = self.client.post(reverse("panel_vote", args=[self.startup.pk, session.pk]), follow=True)
        self.assertContains(response, "после 7 ответов")
        self.assertFalse(PanelVerdict.objects.filter(session=session).exists())

    def test_condition_becomes_task_once_and_respects_busy_axis(self):
        session = self.panel_with_answers(panel.MIN_ANSWERS)
        panel.run_vote(session)
        url = reverse("panel_vote_task", args=[self.startup.pk, session.pk, 2])
        self.assertRedirects(self.client.post(url), reverse("tasks", args=[self.startup.pk]),
                             fetch_redirect_response=False)
        task = BrunoTask.objects.get(startup=self.startup)
        self.assertEqual((task.axis, task.title), ("finance", "Первые оплаты"))
        self.client.post(url)
        self.assertEqual(BrunoTask.objects.filter(startup=self.startup).count(), 1)
        self.assertEqual(PanelVerdict.objects.get(session=session).votes[2]["task_id"], str(task.pk))
        BrunoTask.objects.create(startup=self.startup, axis="product", title="Занято", instructions="x",
                                 success_criterion="y")
        busy = self.client.post(reverse("panel_vote_task", args=[self.startup.pk, session.pk, 0]), follow=True)
        self.assertContains(busy, "уже есть задание в работе")

    def test_other_founder_cannot_vote_or_take_conditions(self):
        session = self.panel_with_answers(panel.MIN_ANSWERS)
        panel.run_vote(session)
        stranger = User.objects.create_user(username="stranger", email="stranger@example.test")
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(reverse("panel_vote", args=[self.startup.pk, session.pk])).status_code, 404)
        self.assertEqual(self.client.post(
            reverse("panel_vote_task", args=[self.startup.pk, session.pk, 0])).status_code, 404)

    def test_training_models_accept_only_their_mode(self):
        pitch = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.PITCH)
        with self.assertRaises(ValidationError):
            PanelVerdict(session=pitch, votes=[]).full_clean()
        PitchReport(session=panel.create_panel(self.startup), score=50, summary="Итог").full_clean()

    @override_settings(AI_REQUESTS_PER_MINUTE=1)
    def test_vote_counts_against_ai_limits(self):
        session = self.panel_with_answers(2)
        url = reverse("panel_vote", args=[self.startup.pk, session.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 429)


class NormaliseVoteTests(TestCase):
    def vote(self, key, strict=True, **changes):
        return panel.normalise_vote(json.loads(model_vote(key, **changes)), key, ANSWERS, {"4900"}, strict=strict)

    def test_quote_must_come_from_founder_answers(self):
        self.assertEqual(self.vote("timur")["quote"], "Администратор обзванивала пациентов вручную")
        self.assertEqual(self.vote("oleg", quote="выдуманная фраза")["quote"], "")

    def test_invented_money_is_rejected_then_cut_on_last_attempt(self):
        reason = "Выручка 90 000 ₽ в месяц. Цену 4900 ₽ я услышала."
        with self.assertRaises(ValueError):
            self.vote("margarita", reason=reason)
        self.assertEqual(self.vote("margarita", strict=False, reason=reason)["reason"], "Цену 4900 ₽ я услышала.")

    def test_russian_decisions_nested_condition_and_incomplete_votes(self):
        self.assertEqual(self.vote("timur", decision="Вкладываю")["decision"], "invest")
        self.assertEqual(self.vote("oleg", decision="пас")["decision"], "pass")
        nested = {"decision": "pass", "reason": "Нет канала.", "quote": "",
                  "condition": {"title": "Канал", "instructions": "Позвоните.", "success_criterion": "Десять встреч."}}
        self.assertEqual(panel.normalise_vote(nested, "oleg", ANSWERS)["condition"]["title"], "Канал")
        with self.assertRaises(ValueError):
            self.vote("oleg", condition_steps="")
        with self.assertRaises(ValueError):
            self.vote("oleg", decision="может быть")

    @override_settings(AI_PROVIDER="gigachat")
    def test_each_shark_votes_separately_and_retries_once(self):
        user = User.objects.create_user(username="retry", email="retry@example.test")
        startup = StartupProfile.objects.create(owner=user, name="ДентаСлот")
        session = panel.create_panel(startup)
        for text in ANSWERS:
            answer(session, text)
        report = json.dumps({"score": 60, "summary": "Ответы конкретные.", "mistakes": []}, ensure_ascii=False)
        replies = ["{}", model_vote("timur"), model_vote("oleg"), model_vote("margarita")]
        with patch("founder.services.panel.complete_text", side_effect=replies) as vote_call, \
                patch("founder.services.pitch.complete_text", return_value=report), \
                patch("founder.services.economics.unit_economics", return_value=[]):
            verdict = panel.run_vote(session)
        self.assertEqual(vote_call.call_count, 4)
        self.assertIn("Ты Тимур", vote_call.call_args_list[0].args[0])
        self.assertIn("Ты Маргарита", vote_call.call_args_list[3].args[0])
        self.assertEqual([vote["decision"] for vote in verdict.votes], ["invest", "pass", "invest"])

    @override_settings(AI_PROVIDER="gigachat")
    def test_two_bad_answers_stop_the_vote_without_saving(self):
        user = User.objects.create_user(username="broken", email="broken@example.test")
        session = panel.create_panel(StartupProfile.objects.create(owner=user, name="ДентаСлот"))
        for text in ANSWERS:
            answer(session, text)
        with patch("founder.services.panel.complete_text", return_value="{}"), \
                patch("founder.services.economics.unit_economics", return_value=[]), \
                self.assertRaisesMessage(panel.AIServiceError, "Акулы не договорились"):
            panel.run_vote(session)
        self.assertFalse(PanelVerdict.objects.filter(session=session).exists())


class SharkFocusTests(PanelSetup):
    def test_prompt_names_open_subtopic_and_drops_copyable_example(self):
        session = panel.create_panel(self.startup)
        messages = [{"role": "user", "content": "Пока ищем клиентов через знакомых врачей"}]
        prompt = system_prompt(session, [], messages, turn=panel.Turn("oleg"))
        self.assertIn("уже говорил: канал привлечения", prompt)
        self.assertIn("Спроси про «конкуренты»", prompt)
        self.assertNotIn(panel.SHARKS["oleg"]["example"], prompt)
        self.assertNotIn("Ранее сказанное основателем Бруно", prompt)
        self.assertIn("Бруно в панели не участвует", prompt)
        margarita = system_prompt(session, [], messages, turn=panel.Turn("margarita"))
        self.assertIn("не спрашивай это у основателя", margarita)

    def test_subtopics_close_from_founder_words(self):
        closed, open_ = panel.subtopic_status("margarita", ["Берём 4900 в месяц, за СМС платим около 700"])
        self.assertEqual(closed, ["цена", "затраты на клиента"])
        self.assertEqual(open_[0], "стоимость привлечения")

    def test_oleg_sees_market_report(self):
        from founder.models import MarketReport
        MarketReport.objects.create(startup=self.startup, sources=[{"domain": "ident.ru", "title": "IDENT",
                                                                    "url": "https://ident.ru/", "snippet": ""}],
                                    data={"relevance": "medium", "verdict": "Рынок занят CRM.", "competitors": [
                                        {"name": "IDENT", "what": "CRM для стоматологий", "price": "", "source": 1}]})
        session = panel.create_panel(self.startup)
        prompt = system_prompt(session, [], [{"role": "user", "content": "Делаем запись"}], turn=panel.Turn("oleg"))
        self.assertIn("Конкурент: IDENT (ident.ru)", prompt)
        timur = system_prompt(session, [], [{"role": "user", "content": "Делаем запись"}], turn=panel.Turn("timur"))
        self.assertNotIn("Конкурент: IDENT", timur)

    def test_vote_condition_needs_a_number(self):
        raw = json.loads(model_vote("oleg", condition_done_when="Получены данные о каналах."))
        with self.assertRaises(ValueError):
            panel.normalise_vote(raw, "oleg", ANSWERS)
        self.assertEqual(panel.normalise_vote(raw, "oleg", ANSWERS, strict=False)["condition"]["success_criterion"],
                         "Получены данные о каналах.")

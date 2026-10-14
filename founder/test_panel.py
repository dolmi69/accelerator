"""Панель акул: очередь по областям, уточнения, голосование и условия в задания."""
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import (
    BrunoTask, ChatMessage, ChatSession, PanelVerdict, PitchReport, StartupMemory, StartupProfile, User,
)
from founder.services import panel
from founder.services.ai import stream_reply, system_prompt
from founder.services.bruno import polish_stream
from founder.services.memory import conversation_context


def answer(session, text):
    return ChatMessage.objects.create(session=session, role=ChatMessage.Role.USER, content=text)


MODEL_VOTES = {
    "timur": {"decision": "invest", "reason": "Живой пользователь у вас уже есть.",
              "quote": "администратор обзванивала пациентов вручную", "condition_title": "Три постоянных клиники",
              "condition_steps": "Подключите три клиники.",
              "condition_done_when": "Три клиники пользуются сервисом месяц."},
    "vadim": {"decision": "pass", "reason": "Канала без знакомых я не услышал.",
              "quote": "ищем клиентов через знакомых", "condition_title": "Канал без знакомых",
              "condition_steps": "Проверьте холодные звонки.", "condition_done_when": "Десять заявок за месяц."},
    "margarita": {"decision": "invest", "reason": "Цена 4900 ₽ понятна. Осталось проверить оплату.",
                  "quote": "берём 4900 в месяц", "condition_title": "Две оплаты",
                  "condition_steps": "Возьмите оплату с двух клиник.",
                  "condition_done_when": "Две клиники заплатили за второй месяц."},
}


def model_vote(key, **changes):
    return json.dumps({**MODEL_VOTES[key], **changes}, ensure_ascii=False)


# Полная панель без уточнений: по три вопроса у каждой акулы, девятый ответ закрывает панель.
ANSWERS = [
    "Администратор обзванивала пациентов вручную, два часа в день",
    "Пациент сам выбирает время в мессенджере, это быстрее звонка",
    "Клиника пользуется каждый день, пока идёт запись",
    "Три клиники пользуются бесплатно с сентября",
    "Пока ищем клиентов через знакомых врачей",
    "Команда: я продаю, друг программирует",
    "Мы берём 4900 в месяц, за СМС платим около 700",
    "Привлечение клиники обходится примерно в 3000 рублей",
    "Своих денег хватит на четыре месяца",
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
        for text in ANSWERS[:count]:
            answer(session, text)
        return session


class TurnTests(PanelSetup):
    def plan(self, texts):
        return [(turn.speaker, turn.number, turn.follow_up, turn.final) for turn in panel.schedule(texts)]

    def test_panel_opens_with_timur_who_names_the_order(self):
        session = panel.create_panel(self.startup)
        self.assertEqual(session.mode, ChatSession.Mode.PANEL)
        opening = session.messages.get()
        self.assertEqual(opening.speaker, "timur")
        self.assertIn("Вадим про рост и команду и Маргарита про деньги", opening.content)
        self.assertIn("Частные стоматологии", opening.content)
        self.assertEqual(panel.current_speaker(session), "timur")

    def test_each_shark_asks_its_block_then_hands_over(self):
        self.assertEqual(self.plan(ANSWERS), [
            ("timur", 2, "", False), ("timur", 3, "", False),
            ("vadim", 1, "", False), ("vadim", 2, "", False), ("vadim", 3, "", False),
            ("margarita", 1, "", False), ("margarita", 2, "", False), ("margarita", 3, "", False),
            ("margarita", 3, "", True),
        ])
        topics = [turn.topic for turn in panel.schedule(ANSWERS)]
        self.assertEqual(topics[:3], ["чем вы заметно лучше", "почему клиенты вернутся", "что уже сделано в цифрах"])
        # После последнего хода новые ответы очередь не меняют.
        self.assertEqual(len(panel.schedule(ANSWERS + ["Ещё ответ"])), len(ANSWERS))

    def test_one_follow_up_per_question_and_only_when_needed(self):
        plan = self.plan(["Не знаю", "Без понятия, честно", "Через знакомых", "Сарафан между врачами клиник"])
        self.assertEqual(plan, [
            ("timur", 1, "evasive", False),
            # На тот же вопрос второго уточнения нет: Тимур переходит ко второму.
            ("timur", 2, "", False),
            ("timur", 2, "short", False),
            ("timur", 3, "", False),
        ])

    def test_money_question_without_numbers_gets_a_follow_up(self):
        turns = panel.schedule(ANSWERS[:6] + ["Будем брать недорого, как у всех"])
        self.assertEqual((turns[-1].speaker, turns[-1].follow_up), ("margarita", "number"))
        self.assertEqual(panel.schedule(ANSWERS[:7])[-1].follow_up, "")

    def test_shark_asks_two_questions_when_founder_covered_her_topics(self):
        covered = "Администратор обзванивала вручную, у нас быстрее, и клиники возвращаются каждый день"
        turns = panel.schedule([covered, "Ничего слабого не вижу, всё проверено"])
        self.assertEqual((turns[0].speaker, turns[0].number, turns[0].topic), ("timur", 2, ""))
        self.assertEqual((turns[1].speaker, turns[1].number), ("vadim", 1))

    def test_evasive_detection(self):
        for text in ("не знаю", "Хз", "Сложно сказать, мы не считали", "?!"):
            self.assertTrue(panel.is_evasive(text), text)
        for text in ("Нет", "Владелец клиники", "4900 в месяц", "Не считали, но реклама примерно 3000 на клинику"):
            self.assertFalse(panel.is_evasive(text), text)

    def test_progress_marks_done_current_and_waiting(self):
        session = self.panel_with_answers(4)
        progress = panel.progress(session)
        self.assertEqual([(step["key"], step["state"], step["question"]) for step in progress["steps"]],
                         [("timur", "done", 3), ("vadim", "current", 2), ("margarita", "waiting", 0)])
        self.assertFalse(progress["finished"])
        self.assertTrue(panel.progress(self.panel_with_answers(len(ANSWERS)))["finished"])


class PromptTests(PanelSetup):
    def test_prompt_speaks_as_active_shark_with_economics_for_margarita(self):
        session = panel.create_panel(self.startup)
        prompt = system_prompt(session, [], [{"role": "user", "content": "Берём 4900"}],
                               economics="Расчёт: прибыль 2200 ₽",
                               turn=panel.Turn("margarita", "стоимость привлечения", 2))
        self.assertIn("Ты Маргарита, финансовый инвестор", prompt)
        self.assertIn("Тимур: клиент и продукт; Вадим: рост и команда; Маргарита: деньги", prompt)
        self.assertIn("не пересчитывай", prompt)
        self.assertIn("Расчёт: прибыль 2200 ₽", prompt)
        self.assertIn("женском", prompt)
        self.assertIn("Это твой 2-й вопрос", prompt)
        self.assertIn("спроси про «стоимость привлечения»", prompt)
        self.assertNotIn("Олег", prompt)
        follow_up = system_prompt(session, [], [], turn=panel.Turn("timur", follow_up="evasive"))
        self.assertIn("Ты Тимур", follow_up)
        self.assertIn("ушёл от ответа", follow_up)
        self.assertIn("второго уточнения не будет", follow_up)
        self.assertIn("Не советуй", follow_up)
        handover = system_prompt(session, [], [], turn=panel.Turn("vadim", "что уже сделано в цифрах", 1))
        self.assertIn("Слово перешло к тебе", handover)
        self.assertIn("мужском", handover)

    @override_settings(AI_PROVIDER="gigachat")
    def test_closing_line_needs_no_model(self):
        session = self.panel_with_answers(len(ANSWERS))
        with patch("founder.services.ai._provider_stream") as provider:
            reply = "".join(stream_reply(session, [], [], turn=panel.next_turn(session)))
        self.assertEqual(reply, panel.CLOSING)
        provider.assert_not_called()

    def test_history_shows_shark_names(self):
        session = panel.create_panel(self.startup)
        latest = answer(session, "Клиника на три кресла")
        messages, _ = conversation_context(session, latest)
        self.assertEqual([item["role"] for item in messages], ["assistant", "user"])
        self.assertTrue(messages[0]["content"].startswith("Тимур: Нас трое"))

    def test_panel_answers_are_not_remembered_as_project_facts(self):
        self.client.post(reverse("panel_create", args=[self.startup.pk]))
        session = self.startup.chat_sessions.get(mode=ChatSession.Mode.PANEL)
        with override_settings(AI_PROVIDER="demo", CHAT_BUFFERED_RESPONSES=True):
            self.client.post(reverse("chat_send", args=[self.startup.pk, session.pk]), {"content": "У нас 50 клиник"})
        self.assertFalse(StartupMemory.objects.filter(startup=self.startup).exists())

    def test_margarita_keeps_feminine_self_reference(self):
        self.assertEqual("".join(polish_stream(["Я рада цифрам."], self_male=False)), "Я рада цифрам.")
        self.assertEqual("".join(polish_stream(["Я рад цифрам."])), "Я рад цифрам.")


class OwnReplyTests(TestCase):
    def reply(self, chunks, speaker="margarita"):
        return "".join(panel.own_reply(iter(chunks), speaker))

    def test_neighbour_line_is_dropped(self):
        self.assertEqual(self.reply(["Тимур: Маргарита, дай ", "человеку рассказать.\nСколько ", "стоит клиент?"]),
                         "Сколько стоит клиент?")

    def test_own_name_is_cut_and_plain_text_stays(self):
        self.assertEqual(self.reply(["Маргарита: Сколько стоит клиент?"]), "Сколько стоит клиент?")
        self.assertEqual(self.reply(["Смотрите: цена ниже затрат."]), "Смотрите: цена ниже затрат.")
        self.assertEqual(self.reply(["Олег: где каналы?"]), "где каналы?")

    def test_reply_streams_once_the_start_is_clear(self):
        self.assertEqual(list(panel.own_reply(iter(["Сколько стоит привлечь клиента", "?"]), "timur")),
                         ["Сколько стоит привлечь клиента", "?"])


@override_settings(AI_PROVIDER="demo", CHAT_BUFFERED_RESPONSES=True)
class ChatFlowTests(PanelSetup):
    def send(self, session, text):
        return self.client.post(reverse("chat_send", args=[self.startup.pk, session.pk]), {"content": text})

    def test_reply_names_speaker_and_saves_it(self):
        session = panel.create_panel(self.startup)
        body = self.send(session, ANSWERS[0]).content.decode()
        events = [json.loads(line[6:]) for line in body.split("\n\n") if line.startswith("data: ")]
        self.assertEqual(events[0], {"type": "speaker", "speaker": "timur", "name": "Тимур",
                                     "title": "клиент и продукт", "initial": "Т"})
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(session.messages.last().speaker, "timur")
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, session.pk]))
        self.assertContains(page, "Ваш ответ Тимуру…")
        self.assertContains(page, "вопрос 2")
        self.assertContains(page, 'aria-current="step"')
        self.assertContains(page, "chat-composer is-panel")

    def test_timur_hands_over_to_vadim_after_his_block(self):
        session = panel.create_panel(self.startup)
        for text in ANSWERS[:3]:
            self.send(session, text)
        last = session.messages.last()
        self.assertEqual(last.speaker, "vadim")
        self.assertTrue(last.content.startswith("Теперь мой черёд, спрошу про рост и команду."))
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, session.pk]))
        self.assertContains(page, "Ваш ответ Вадиму…")

    def test_vote_button_appears_after_the_last_question_and_closes_the_chat(self):
        session = panel.create_panel(self.startup)
        url = reverse("chat_detail", args=[self.startup.pk, session.pk])
        vote_url = reverse("panel_vote", args=[self.startup.pk, session.pk])
        for index, text in enumerate(ANSWERS):
            if index == len(ANSWERS) - 1:
                self.assertNotContains(self.client.get(url), vote_url)
            self.assertEqual(self.send(session, text).status_code, 200)
        closing = session.messages.last()
        self.assertEqual((closing.content, closing.provider), (panel.CLOSING, "system"))
        page = self.client.get(url)
        self.assertContains(page, vote_url)
        self.assertContains(page, "Акулы задали все вопросы")
        self.assertEqual(self.send(session, "Ещё ответ").status_code, 409)

    def test_search_names_the_shark(self):
        session = panel.create_panel(self.startup)
        data = self.client.get(reverse("chat_search", args=[self.startup.pk, session.pk]), {"q": "нас трое"}).json()
        self.assertEqual([item["author"] for item in data["results"]], ["Тимур"])

    def test_investor_page_offers_both_modes(self):
        page = self.client.get(reverse("investor", args=[self.startup.pk]))
        self.assertContains(page, reverse("pitch_create", args=[self.startup.pk]))
        self.assertContains(page, reverse("panel_create", args=[self.startup.pk]))
        self.assertContains(page, "Вадим · рост и команда")
        self.assertContains(page, "Маргарита · деньги")


@override_settings(AI_PROVIDER="demo")
class VoteTests(PanelSetup):
    def test_vote_saves_three_votes_report_and_completes_panel(self):
        session = self.panel_with_answers(len(ANSWERS))
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

    def test_vote_waits_for_all_questions(self):
        session = self.panel_with_answers(3)
        response = self.client.post(reverse("panel_vote", args=[self.startup.pk, session.pk]), follow=True)
        self.assertContains(response, "каждая задаст свои вопросы")
        self.assertFalse(PanelVerdict.objects.filter(session=session).exists())

    def test_condition_becomes_task_once_and_respects_busy_axis(self):
        session = self.panel_with_answers(len(ANSWERS))
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

    def test_old_verdict_with_oleg_still_renders_and_gives_a_task(self):
        session = self.panel_with_answers(len(ANSWERS))
        vote = {**json.loads(model_vote("vadim")), "shark": "oleg", "decision": "pass", "task_id": None,
                "condition": {"title": "Канал", "instructions": "Позвоните.", "success_criterion": "Десять встреч."}}
        verdict = PanelVerdict.objects.create(session=session, votes=[vote])
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, session.pk]))
        self.assertContains(page, "Олег")
        self.assertContains(page, "0 из 1 акул вложились")
        self.assertEqual(panel.condition_to_task(verdict, 0).axis, "market")

    def test_other_founder_cannot_vote_or_take_conditions(self):
        session = self.panel_with_answers(len(ANSWERS))
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
        self.assertEqual(self.vote("vadim", quote="выдуманная фраза")["quote"], "")

    def test_invented_money_is_rejected_then_cut_on_last_attempt(self):
        reason = "Выручка 90 000 ₽ в месяц. Цену 4900 ₽ я услышала."
        with self.assertRaises(ValueError):
            self.vote("margarita", reason=reason)
        self.assertEqual(self.vote("margarita", strict=False, reason=reason)["reason"], "Цену 4900 ₽ я услышала.")

    def test_russian_decisions_nested_condition_and_incomplete_votes(self):
        self.assertEqual(self.vote("timur", decision="Вкладываю")["decision"], "invest")
        self.assertEqual(self.vote("vadim", decision="пас")["decision"], "pass")
        nested = {"decision": "pass", "reason": "Нет канала.", "quote": "",
                  "condition": {"title": "Канал", "instructions": "Позвоните.", "success_criterion": "Десять встреч."}}
        self.assertEqual(panel.normalise_vote(nested, "vadim", ANSWERS)["condition"]["title"], "Канал")
        with self.assertRaises(ValueError):
            self.vote("vadim", condition_steps="")
        with self.assertRaises(ValueError):
            self.vote("vadim", decision="может быть")


@override_settings(AI_PROVIDER="gigachat")
class ModelVoteTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="voter", email="voter@example.test")
        self.session = panel.create_panel(StartupProfile.objects.create(owner=user, name="ДентаСлот"))
        for text in ANSWERS:
            answer(self.session, text)
        self.report = json.dumps({"score": 60, "summary": "Ответы конкретные.", "mistakes": []}, ensure_ascii=False)

    def run_vote(self, replies):
        with patch("founder.services.panel.complete_text", side_effect=replies) as vote_call, \
                patch("founder.services.pitch.complete_text", return_value=self.report), \
                patch("founder.services.economics.unit_economics", return_value=[]):
            try:
                return panel.run_vote(self.session), vote_call.call_count
            except panel.AIServiceError as exc:
                return exc, vote_call.call_count

    def test_each_shark_votes_separately_and_retries(self):
        verdict, calls = self.run_vote(["{}", model_vote("timur"), model_vote("vadim"), model_vote("margarita")])
        self.assertEqual(calls, 4)
        self.assertEqual([vote["decision"] for vote in verdict.votes], ["invest", "pass", "invest"])

    def test_failed_shark_does_not_erase_the_others(self):
        error, calls = self.run_vote([model_vote("timur"), model_vote("vadim")] + ["{}"] * panel.VOTE_ATTEMPTS)
        self.assertIn("нет голоса: Маргарита", str(error))
        self.assertEqual(calls, 2 + panel.VOTE_ATTEMPTS)
        self.assertFalse(PanelVerdict.objects.filter(session=self.session).exists())
        # Повторное голосование спрашивает только Маргариту.
        verdict, calls = self.run_vote([model_vote("margarita")])
        self.assertEqual(calls, 1)
        self.assertEqual([vote["shark"] for vote in verdict.votes], list(panel.ORDER))

    def test_vote_prompts_name_each_shark(self):
        with patch("founder.services.panel.complete_text", side_effect=[model_vote(key) for key in panel.ORDER]) \
                as vote_call, patch("founder.services.pitch.complete_text", return_value=self.report), \
                patch("founder.services.economics.unit_economics", return_value=[]):
            panel.run_vote(self.session)
        prompts = [call.args[0] for call in vote_call.call_args_list]
        self.assertIn("Ты Тимур, продуктовый инвестор", prompts[0])
        self.assertIn("Ты Вадим, инвестор роста", prompts[1])
        self.assertIn("Ты Маргарита, финансовый инвестор", prompts[2])


class SharkFocusTests(PanelSetup):
    def test_prompt_names_topic_and_what_was_already_said(self):
        session = panel.create_panel(self.startup)
        messages = [{"role": "user", "content": "Три клиники пользуются бесплатно с сентября"}]
        prompt = system_prompt(session, [], messages, turn=panel.Turn("vadim", "откуда придут следующие клиенты", 2))
        self.assertIn("спроси про «откуда придут следующие клиенты»", prompt)
        self.assertIn("не переспрашивай: что уже сделано в цифрах", prompt)
        self.assertNotIn("Ранее сказанное основателем Бруно", prompt)
        self.assertIn("Бруно в панели не участвует", prompt)
        margarita = system_prompt(session, [], messages, turn=panel.Turn("margarita", "цена и затраты на клиента"))
        self.assertIn("не спрашивай это у основателя", margarita)

    def test_subtopics_close_from_founder_words(self):
        closed, open_ = panel.subtopic_status("margarita", ["Берём 4900 в месяц, за СМС платим около 700"])
        self.assertEqual(closed, ["цена и затраты на клиента"])
        self.assertEqual(open_[0], "стоимость привлечения")

    def test_common_words_do_not_close_topics(self):
        for key, text in (("timur", "Сейчас работаем в нашем городе"), ("vadim", "Команда мечты, город большой"),
                          ("margarita", "Работаем сейчас в одном районе"),
                          # Конкурент не канал, а его цена не цена проекта.
                          ("vadim", "Конкуренты Профи.ру и Авито"),
                          ("margarita", "Мама платила 2000 за час взрослому репетитору")):
            self.assertEqual(panel.subtopic_status(key, [text])[0], [], text)

    def test_timur_sees_competitors_and_margarita_market_prices(self):
        from founder.models import MarketReport
        MarketReport.objects.create(startup=self.startup, sources=[{"domain": "ident.ru", "title": "IDENT",
                                                                    "url": "https://ident.ru/", "snippet": ""}],
                                    data={"relevance": "medium", "verdict": "Рынок занят CRM.", "competitors": [
                                        {"name": "IDENT", "what": "CRM для стоматологий", "price": "", "source": 1}]})
        session = panel.create_panel(self.startup)
        messages = [{"role": "user", "content": "Делаем запись"}]
        self.assertIn("Конкурент: IDENT (ident.ru)", system_prompt(session, [], messages, turn=panel.Turn("timur")))
        self.assertNotIn("Конкурент: IDENT", system_prompt(session, [], messages, turn=panel.Turn("vadim")))
        self.assertNotIn("Конкурент: IDENT", system_prompt(session, [], messages, turn=panel.Turn("margarita")))

    def test_vote_condition_needs_a_number(self):
        raw = json.loads(model_vote("vadim", condition_done_when="Получены данные о каналах."))
        with self.assertRaises(ValueError):
            panel.normalise_vote(raw, "vadim", ANSWERS)
        self.assertEqual(panel.normalise_vote(raw, "vadim", ANSWERS, strict=False)["condition"]["success_criterion"],
                         "Получены данные о каналах.")


class FundingDetectionTests(TestCase):
    def test_money_from_an_institution_or_company_is_found(self):
        from founder.services.funding import COMPANY, INSTITUTION, detect

        cases = {
            "Хотим получить грант Фонда содействия инновациям": INSTITUTION,
            "Деньги планирую взять не у инвесторов, а у ВШЭ: там есть конкурс": INSTITUTION,
            "Подали заявку в акселератор Сколково, там дают 1,5 млн": INSTITUTION,
            "Университет даст лабораторию и деньги на прототип": INSTITUTION,
            "Сбер может вложиться, если пилот пройдёт": COMPANY,
            "Договариваемся о пилоте с компанией «Самокат»": COMPANY,
        }
        for text, kind in cases.items():
            self.assertEqual(detect([text])["kind"], kind, text)
        self.assertEqual(detect(["Деньги планирую взять у ВШЭ."])["name"], "ВШЭ")
        self.assertEqual(detect(["Договариваемся о пилоте с компанией «Самокат»."])["quote"],
                         "Договариваемся о пилоте с компанией «Самокат»")

    def test_customers_and_refusals_are_not_a_funding_source(self):
        from founder.services.funding import detect

        for text in ("Продаём компаниям, инвестиции ищем у бизнес-ангелов", "Мы не будем просить грант",
                     "Клиенты — университеты и школы, продаём подписку", "Я студент ВШЭ, делаю сервис",
                     "Хочу привлечь 2 млн от бизнес-ангела", "Наши клиенты дают отзывы"):
            self.assertIsNone(detect([text]), text)

    def test_latest_mention_in_chats_with_bruno_wins(self):
        from founder.services.funding import COMPANY, founder_funding

        user = User.objects.create_user(username="funded", email="funded@example.test")
        startup = StartupProfile.objects.create(owner=user, name="Навигатор")
        chat = ChatSession.objects.create(startup=startup, mode=ChatSession.Mode.COFOUNDER)
        answer(chat, "Хотим грант от ВШЭ")
        answer(chat, "Передумали: пилот с компанией «Самокат» интереснее")
        # Ответы в самой панели — тренировка, Бруно их акулам не передаёт.
        answer(panel.create_panel(startup), "Деньги даст фонд «Старт»")
        self.assertEqual(founder_funding(startup)["kind"], COMPANY)


class AudienceTests(PanelSetup):
    def institution_panel(self, name="ВШЭ"):
        from founder.services import funding
        return panel.create_panel(self.startup, funding.audience(funding.INSTITUTION, name))

    def test_commission_opens_with_its_own_areas_and_topics(self):
        session = self.institution_panel()
        opening = session.messages.get().content
        self.assertTrue(opening.startswith("Мы комиссия: решаем, поддержит ли ВШЭ ваш проект."))
        self.assertIn("Я Тимур, спрашиваю про пользу и результат", opening)
        self.assertEqual(session.title, "Комиссия: ВШЭ")
        topics = [turn.topic for turn in panel.schedule(["Студентам первого курса, они не могут найти аудиторию",
                                                        "Раньше такого не было"], "institution")]
        self.assertEqual(topics, ["что в проекте нового", "как поймёте, что проект помог"])
        progress = panel.progress(session)
        self.assertEqual(progress["audience"]["heading"], "Комиссия: ВШЭ")
        self.assertEqual(progress["steps"][2]["area"], "смета и деньги")

    def test_commission_prompt_and_vote_speak_for_the_organisation(self):
        session = self.institution_panel()
        prompt = system_prompt(session, [], [{"role": "user", "content": "Просим 300 тысяч"}],
                               turn=panel.Turn("margarita", "что получится на эти деньги", 2))
        self.assertIn("просит поддержки не у инвесторов, а у организации «ВШЭ»", prompt)
        self.assertIn("Доля в бизнесе, оценка компании и возврат вложений комиссию не интересуют", prompt)
        self.assertIn("Ты Маргарита, финансовый эксперт комиссии", prompt)
        vote = panel.vote_prompt("timur", panel.audience_of(session))
        self.assertIn("член комиссии «ВШЭ»", vote)
        self.assertIn("«поддерживаю» — поддержать проект", vote)
        company = panel.vote_prompt("margarita", {"kind": "company", "name": "Самокат"})
        self.assertIn("финансовый директор компании", company)
        self.assertIn("«сотрудничаем»", company)

    def test_start_form_takes_what_bruno_heard_and_founder_can_change_it(self):
        chat = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        answer(chat, "Деньги хотим получить не от инвесторов, а от ВШЭ: подаёмся на грант.")
        page = self.client.get(reverse("investor", args=[self.startup.pk]))
        self.assertContains(page, "Бруно передал акулам")
        self.assertContains(page, 'value="institution" checked')
        self.assertContains(page, 'value="ВШЭ"')
        self.client.post(reverse("panel_create", args=[self.startup.pk]))
        heard = self.startup.chat_sessions.filter(mode=ChatSession.Mode.PANEL).first()
        self.assertEqual((heard.panel_audience["kind"], heard.panel_audience["name"], heard.panel_audience["source"]),
                         ("institution", "ВШЭ", "bruno"))
        self.client.post(reverse("panel_create", args=[self.startup.pk]),
                         {"audience": "company", "funder_name": "Самокат"})
        chosen = self.startup.chat_sessions.filter(mode=ChatSession.Mode.PANEL).first()
        self.assertEqual(chosen.panel_audience, {"kind": "company", "name": "Самокат", "quote": "", "source": "manual"})
        self.assertIn("Мы представляем компанию «Самокат»", chosen.messages.get().content)
        self.client.post(reverse("panel_create", args=[self.startup.pk]), {"audience": "investors"})
        self.assertEqual(panel.audience_of(self.startup.chat_sessions.filter(mode=ChatSession.Mode.PANEL).first())
                         ["kind"], "investors")

    def test_bruno_chat_shows_that_sharks_will_know(self):
        chat = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        answer(chat, "Хотим, чтобы Сбер вложился после пилота")
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, chat.pk]))
        self.assertContains(page, "Бруно передаст акулам:")
        self.assertContains(page, "от компании (Сбер)")

    @override_settings(AI_PROVIDER="demo")
    def test_commission_verdict_uses_its_words(self):
        session = self.institution_panel()
        for text in ["Студентам первого курса, они не могут найти аудиторию", "Раньше такого не было, все в чатах",
                     "Через полгода сократим опоздания на 20%", "Сделали прототип и опросили 40 студентов",
                     "План: к декабрю бот, к марту приложение", "Команда: я пишу код, Лиза дизайнер",
                     "Просим 300 тысяч на разработку и серверы", "На эти деньги подключим 2000 студентов",
                     "Потом будем продавать подписку другим вузам"]:
            if panel.is_finished(session):
                break
            answer(session, text)
        self.assertTrue(panel.is_finished(session))
        panel.run_vote(session)
        page = self.client.get(reverse("chat_detail", args=[self.startup.pk, session.pk]))
        self.assertContains(page, "из 3 членов комиссии поддержали")
        self.assertContains(page, "Поддержу, если")
        self.assertContains(self.client.get(reverse("investor", args=[self.startup.pk])), "Комиссия: ВШЭ")


class ReplyCheckTests(PanelSetup):
    SOURCES = "Берём 1500 рублей за урок. Своих денег 60 тысяч."

    def problem(self, text, founder="Берём 1500 рублей за урок"):
        return panel.reply_problem(text, self.SOURCES, founder)

    def test_plain_question_with_founder_numbers_passes(self):
        for text in ("Цена 1500 ₽ за урок понятна. Сколько рублей уходит, чтобы привести одного ученика?",
                     "С 60 000 ₽ своих денег сколько месяцев вы продержитесь?",
                     "Сколько учеников придёт за 30 дней?"):
            self.assertEqual(self.problem(text), "", text)

    def test_jargon_vague_questions_missing_question_and_invented_numbers_fail(self):
        self.assertIn("CAC", self.problem("Какой у вас CAC?"))
        self.assertIn("юнит-экономика", self.problem("Как выглядит ваша юнит-экономика по урокам?"))
        self.assertIn("слишком общий", self.problem("Какова ваша стратегия монетизации?"))
        self.assertIn("нет вопроса", self.problem("Цена 1500 ₽ понятна."))
        self.assertIn("4 500 ₽", self.problem("Значит, с трёх уроков у вас 4 500 ₽. Сколько стоит привлечь ученика?"))
        self.assertIn("35%", self.problem("Маржа выходит 35%. Сколько стоит привлечь ученика?"))
        # Слово основателя можно повторить.
        self.assertEqual(self.problem("Вы сказали про CAC. Сколько он в рублях?", founder="CAC пока не считали"), "")

    def test_bad_reply_is_rewritten_once_then_replaced_by_a_plain_question(self):
        session = panel.create_panel(self.startup)
        turn = panel.Turn("margarita", "стоимость привлечения", 2)
        attempts = []

        def generate(replies):
            def call(current):
                attempts.append(current.fix)
                yield replies[len(attempts) - 1]
            return call

        reply = "".join(panel.checked_reply(session, turn, generate(["Какой у вас CAC?", "Сколько рублей стоит привлечь клиента?"])))
        self.assertEqual(reply, "Сколько рублей стоит привлечь клиента?")
        self.assertEqual(attempts[0], "")
        self.assertIn("простыми словами", attempts[1])
        attempts.clear()
        reply = "".join(panel.checked_reply(session, turn, generate(["Какой CAC?", "А LTV какой?"])))
        self.assertEqual(reply, "Сколько рублей уходит, чтобы привести одного платящего клиента?")
        follow_up = panel.Turn("margarita", "стоимость привлечения", 2, follow_up="evasive")
        attempts.clear()
        reply = "".join(panel.checked_reply(session, follow_up, generate(["Какой CAC?", "Какой CAC?"])))
        self.assertTrue(reply.startswith("Точных данных может и не быть"), reply)

    def test_closing_line_is_not_checked(self):
        session = panel.create_panel(self.startup)
        final = panel.Turn("margarita", final=True)
        self.assertEqual("".join(panel.checked_reply(session, final, lambda turn: iter([panel.CLOSING]))), panel.CLOSING)

    def test_vote_may_write_thousands_in_full(self):
        allowed = panel.allowed_numbers(["Своих денег 60 тысяч, урок 1 500 ₽", "Окупится за 2,5 месяца"])
        self.assertTrue({"60", "60000", "1500", "2.5"} <= allowed)
        vote = panel.normalise_vote(json.loads(model_vote("margarita", reason="60 000 ₽ своих денег хватит.")),
                                    "margarita", ANSWERS, allowed)
        self.assertEqual(vote["reason"], "60 000 ₽ своих денег хватит.")

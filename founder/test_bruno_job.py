"""«Бруно работает»: страницы видят долгий AI-запрос; экономика вводится на странице рынка; чтение страниц."""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import AIRequestLease, IdeaRound, MarketReport, ProjectPicture, StartupProfile, User
from founder.services import economics, ideas, web_search
from founder.services.request_limits import acquire_ai_lease, release_ai_lease


class BrunoJobTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("jobs", email="jobs@example.test", password="x")
        self.startup = StartupProfile.objects.create(owner=self.user, name="Кофе у кампуса")
        self.client.force_login(self.user)

    def test_running_request_is_shown_on_any_page_and_in_status(self):
        self.assertEqual(self.client.get(reverse("bruno_job")).json(), {"busy": False, "label": "", "seconds": 0})
        token = acquire_ai_lease(self.user.pk, route="market_generate", startup_id=self.startup.pk)
        status = self.client.get(reverse("bruno_job")).json()
        self.assertEqual((status["busy"], status["label"]), (True, "изучает рынок и конкурентов"))
        page = self.client.get(reverse("market", args=[self.startup.pk]))
        self.assertContains(page, "Бруно сейчас")
        self.assertContains(page, "изучает рынок и конкурентов")
        self.assertContains(page, "bruno-job.js")
        release_ai_lease(self.user.pk, token)
        self.assertNotContains(self.client.get(reverse("market", args=[self.startup.pk])), "data-bruno-job ")
        self.assertFalse(self.client.get(reverse("bruno_job")).json()["busy"])

    def test_second_request_explains_what_bruno_is_doing_and_waits(self):
        acquire_ai_lease(self.user.pk, route="review_generate", startup_id=self.startup.pk)
        response = self.client.post(reverse("market_generate", args=[self.startup.pk]), HTTP_ACCEPT="text/html",
                                    HTTP_REFERER="http://testserver" + reverse("market", args=[self.startup.pk]))
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Бруно ещё работает", status_code=429)
        self.assertContains(response, "Бруно сейчас готовит разбор и план", status_code=429)
        self.assertContains(response, "data-bruno-wait", status_code=429)
        lease = AIRequestLease.objects.get(user=self.user)
        self.assertEqual((lease.route, lease.startup_id), ("review_generate", self.startup.pk))

    def test_lease_records_the_action_of_a_real_request(self):
        seen = {}

        def generate(startup):
            seen["job"] = AIRequestLease.objects.get(user=self.user)
            return MarketReport.objects.create(startup=startup, data={"verdict": "ok", "checks": []})

        with patch("founder.services.market.create_market_report", side_effect=generate):
            self.client.post(reverse("market_generate", args=[self.startup.pk]))
        self.assertEqual(seen["job"].route, "market_generate")
        self.assertLessEqual(AIRequestLease.objects.get(user=self.user).expires_at, timezone.now())


class EconomicsInputTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("money", email="money@example.test", password="x")
        self.startup = StartupProfile.objects.create(owner=self.user, name="Кофе у кампуса")
        self.client.force_login(self.user)

    def test_numbers_entered_on_market_page_are_calculated_and_remembered(self):
        page = self.client.get(reverse("market", args=[self.startup.pk]))
        self.assertContains(page, "Цена одной продажи для клиента")
        response = self.client.post(reverse("market_economics", args=[self.startup.pk]), {
            "price_per_unit": "300", "cost_per_unit": "120", "fixed_costs_month": "54000", "cash_on_hand": "200000"})
        self.assertRedirects(response, reverse("market", args=[self.startup.pk]) + "#economics",
                             fetch_redirect_response=False)
        self.startup.refresh_from_db()
        self.assertEqual(self.startup.economics, {"price_per_unit": 300.0, "cost_per_unit": 120.0,
                                                  "fixed_costs_month": 54000.0, "cash_on_hand": 200000.0})
        page = self.client.get(reverse("market", args=[self.startup.pk]))
        self.assertContains(page, "Расчёт по вашим цифрам")
        self.assertContains(page, "300 продаж в месяц")
        money = ProjectPicture.objects.get(startup=self.startup).facts["money"]
        self.assertIn("цена продажи 300 ₽", money["text"])
        # Отчёты по всему рассказу берут введённые числа без запроса к модели.
        with patch("founder.services.economics.complete_text") as model:
            lines = economics.project_economics(self.startup, ["Кофе по 250 ₽"], latest_only=False)
        model.assert_not_called()
        self.assertTrue(any("300 продаж" in line for line in lines))

    def test_empty_or_negative_numbers_are_rejected(self):
        response = self.client.post(reverse("market_economics", args=[self.startup.pk]), {"price_per_unit": ""})
        self.assertContains(response, "Заполните хотя бы одно число", status_code=400)
        response = self.client.post(reverse("market_economics", args=[self.startup.pk]), {"price_per_unit": "-5"})
        self.assertEqual(response.status_code, 400)
        self.startup.refresh_from_db()
        self.assertEqual(self.startup.economics, {})

    def test_viewer_cannot_change_numbers(self):
        other = User.objects.create_user("money2", email="money2@example.test", password="x")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("market_economics", args=[self.startup.pk]),
                                          {"price_per_unit": "1"}).status_code, 404)


class PageReadingTests(TestCase):
    def test_internal_addresses_are_never_read(self):
        for url in ("http://127.0.0.1/", "http://localhost:8000/admin/", "http://169.254.169.254/latest/",
                    "http://10.0.0.5/", "file:///etc/passwd", "https://example.com:8443/"):
            self.assertFalse(web_search._public_url(url), url)

    def test_visible_text_is_extracted_without_scripts(self):
        body = ('<html><head><meta name="description" content="Кофе для студентов"><style>.a{}</style>'
                '<script>alert(1)</script></head><body><h1>Цены</h1><p>Капучино 250 ₽</p></body></html>')
        self.assertEqual(web_search.html_text(body), "Кофе для студентов. Цены Капучино 250 ₽")

    @override_settings(MARKET_READ_PAGES=True)
    def test_competitor_pages_are_read_and_others_kept(self):
        sources = [{"url": "https://cafe.ru/", "kind": "russia"}, {"url": "https://news.ru/", "kind": "trends"}]
        with patch("founder.services.web_search._read_direct", return_value="Капучино 250 ₽. " * 30) as reader:
            result = web_search.read_pages(sources, kinds=("russia",))
        reader.assert_called_once_with("https://cafe.ru/")
        self.assertIn("Капучино", result[0]["page"])
        self.assertNotIn("page", result[1])


@override_settings(AI_PROVIDER="gigachat")
class RadarAfterIdeaTests(TestCase):
    def test_radar_is_assessed_right_after_the_project_is_created(self):
        user = User.objects.create_user("radar", email="radar@example.test", password="x")
        self.client.force_login(user)
        item = IdeaRound.objects.create(owner=user, seed="кофе", data=ideas.demo_round("кофе"))
        values = {**ideas.card_from_direction(item.data["directions"][0]), "action": "create"}
        with patch("founder.views.assess_startup") as assess:
            response = self.client.post(reverse("idea_new_card", args=[item.pk, 0]), values)
        startup = StartupProfile.objects.get(owner=user)
        assess.assert_called_once_with(startup)
        self.assertRedirects(response, reverse("dashboard", args=[startup.pk]), fetch_redirect_response=False)
        self.assertEqual(AIRequestLease.objects.get(user=user).route, "idea_new_card:create")
        json.dumps(startup.picture.facts, ensure_ascii=False)


class SummarizeFixesTests(TestCase):
    """Недочёты из прогона «почти готового проекта» (сценарии mature, panel_mature, panel_grant)."""

    def test_economics_says_whether_the_project_is_already_profitable(self):
        lines = economics.summarize({"customer_payment_month": 990, "cost_per_customer_month": 450,
                                     "fixed_costs_month": 190000, "customers": 420})
        self.assertIn("Сейчас 420 клиентов: 420 × 540 ₽ − 190 000 ₽ = 36 800 ₽ в месяц, проект уже в плюсе.", lines)
        lines = economics.summarize({"customer_payment_month": 990, "cost_per_customer_month": 450,
                                     "fixed_costs_month": 190000, "customers": 300})
        self.assertIn("до безубыточности не хватает 52 клиентов", lines[-1])

    def test_praise_sentence_in_the_middle_of_an_answer_is_dropped(self):
        from founder.services.bruno import polish_stream, tidy_reply

        text = "Значит, роли разделены. Отличное разделение обязанностей. Осталось понять каналы. Сколько точек?"
        self.assertEqual("".join(polish_stream(iter([text]))),
                         "Значит, роли разделены. Осталось понять каналы. Сколько точек?")
        self.assertEqual(tidy_reply("Это серьёзное достижение: 420 подписчиков платят. Как растёте?"),
                         "420 подписчиков платят. Как растёте?")
        opinion = "".join(polish_stream(iter(["Как считаете, сработает такое предложение?"])))
        self.assertEqual(opinion, "Сработает такое предложение?")

    def test_picture_note_is_not_replaced_by_an_unrelated_fragment(self):
        from founder.services.mentor import merge_facts

        old = {"customer": {"text": "Курьеры доставки в Москве на своих велосипедах", "status": "guess"}}
        new = {"customer": {"text": "Планируют новые города", "status": "guess"}}
        merged = merge_facts(old, new)["customer"]["text"]
        self.assertTrue(merged.startswith("Курьеры доставки в Москве"))
        update = {"customer": {"text": "Курьеры доставки в Москве и Казани, 420 на подписке", "status": "fact"}}
        self.assertEqual(merge_facts(old, update)["customer"], update["customer"])

    def test_shark_does_not_praise_or_count_shares_itself(self):
        from founder.services.panel import reply_problem

        self.assertIn("похвала", reply_problem("Быстро ответили, молодцы! Кто в команде что делает?"))
        self.assertIn("доля", reply_problem("Выходит, половина суммы уйдёт на серверы. Почему?", "",
                                            "Просим 300 тысяч: 100 на серверы"))
        self.assertEqual(reply_problem("Третий корпус уже готов?", "", ""), "")

    def test_money_question_fits_a_project_with_revenue_and_vote_asks_for_growth(self):
        from founder.services.panel import SUBTOPICS, vote_prompt

        names = [subtopic.name for subtopic in SUBTOPICS["margarita"]]
        self.assertNotIn("деньги до первых продаж", names)
        self.assertIn("следующий шаг роста", vote_prompt("margarita", mature=True))
        self.assertNotIn("следующий шаг роста", vote_prompt("margarita"))

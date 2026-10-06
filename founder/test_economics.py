import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from founder.services.economics import economics_note, needs_economics, summarize, unit_economics


class SummarizeTests(SimpleTestCase):
    def test_subscription_with_per_unit_cost_shows_loss(self):
        lines = summarize({"customer_payment_month": 1990, "cost_per_unit": 90, "units_per_customer_month": 30})
        text = "\n".join(lines)
        self.assertIn("90 ₽ × 30 = 2 700 ₽", text)
        self.assertIn("1 990 ₽ − 2 700 ₽ = −710 ₽", text)
        self.assertIn("убыток", text)

    def test_margin_cac_and_lifetime_give_payback_and_ltv(self):
        lines = summarize({"monthly_revenue": 1_200_000, "customers": 140, "margin_percent": 25,
                           "cac": 3000, "lifetime_months": 4})
        text = "\n".join(lines)
        self.assertIn("= 8 571 ₽ в месяц", text)
        self.assertIn("при марже 25%: 2 143 ₽", text)
        self.assertIn("окупается за 1.4 мес.", text)
        self.assertIn("LTV / стоимость привлечения = 2.9", text)
        self.assertIn("около 25% клиентов", text)

    def test_not_enough_numbers_or_garbage_gives_nothing(self):
        self.assertEqual(summarize({"cac": 3000}), [])
        self.assertEqual(summarize({"customer_payment_month": "много", "cost_per_unit": True}), [])
        self.assertEqual(economics_note([]), "")

    def test_only_money_talk_with_fresh_numbers_triggers_extraction(self):
        self.assertTrue(needs_economics(["подписка 1990 в месяц", "кофейням платим 90 за чашку"]))
        self.assertFalse(needs_economics(["подписка 1990 в месяц", "команда из 2 человек", "ладно"]))
        self.assertFalse(needs_economics(["у нас 3 клиники в Казани"]))
        self.assertTrue(needs_economics(["цена 500", "ок"], latest_only=False))


@override_settings(AI_PROVIDER="gigachat")
class ExtractionTests(SimpleTestCase):
    def test_model_only_extracts_and_python_computes(self):
        raw = json.dumps({"customer_payment_month": 1990, "cost_per_unit": 90, "units_per_customer_month": 30})
        with patch("founder.services.economics.complete_text", return_value=raw) as call:
            lines = unit_economics(["Подписка 1990 в месяц, чашка в день", "Кофейням платим 90 рублей"])
        self.assertEqual(call.call_count, 1)
        self.assertIn("−710 ₽", "\n".join(lines))

    def test_extraction_failure_never_breaks_the_reply(self):
        with patch("founder.services.economics.complete_text", return_value="не json"):
            self.assertEqual(unit_economics(["цена 1990 ₽, затраты 2700 ₽"]), [])

"""Тесты страницы тарифов и тестовой оплаты Pro (UI поверх founder.services.plans)."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from founder.models import User
from founder.services import plans


@override_settings(
    PRO_DAYS=30, PRO_PRICE_RUB="149", LAB_REQUESTS_PER_DAY=10, LAB_PRO_REQUESTS_PER_DAY=50
)
class TariffViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="tariff", email="tariff@example.com", password="***"
        )
        self.client.force_login(self.user)

    def test_tariffs_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/tariffs/").status_code, 302)

    def test_tariffs_page_shows_price(self):
        resp = self.client.get("/tariffs/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "149")

    def test_test_pay_activates_pro(self):
        resp = self.client.post("/tariffs/pro/test-pay/confirm/")
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(plans.is_pro(self.user))
        self.assertGreater(self.user.pro_until, timezone.now() + timedelta(days=29))

    def test_second_purchase_extends_pro(self):
        self.client.post("/tariffs/pro/test-pay/confirm/")
        self.user.refresh_from_db()
        first = self.user.pro_until
        self.client.post("/tariffs/pro/test-pay/confirm/")
        self.user.refresh_from_db()
        self.assertGreater(self.user.pro_until, first)

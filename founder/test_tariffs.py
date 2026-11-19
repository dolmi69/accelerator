"""Тесты страницы тарифов и тестовой оплаты Pro (UI поверх founder.services.plans)."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from founder.models import LabPlanPeriod, User
from founder.services import plans


@override_settings(
    DEBUG=True, PRO_TEST_CHECKOUT_ENABLED=True, PRO_DAYS=30, PRO_PRICE_RUB="350", LAB_REQUESTS_PER_DAY=10
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
        self.assertContains(resp, '350 ₽ <span class="plan-per">за 30 дней</span>', html=True)
        self.assertContains(resp, 'Один запрос на первую сборку')

    def test_test_pay_activates_pro(self):
        resp = self.client.post("/tariffs/pro/test-pay/confirm/")
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(plans.is_pro(self.user))
        self.assertGreater(self.user.pro_until, timezone.now() + timedelta(days=29))
        period = LabPlanPeriod.objects.get(user=self.user)
        self.assertEqual(period.remaining_units, 10_000_000)
        self.assertFalse(period.intro_used)

    def test_second_purchase_extends_pro(self):
        self.client.post("/tariffs/pro/test-pay/confirm/")
        self.user.refresh_from_db()
        first = self.user.pro_until
        self.client.post("/tariffs/pro/test-pay/confirm/")
        self.user.refresh_from_db()
        self.assertGreater(self.user.pro_until, first)
        periods = list(LabPlanPeriod.objects.filter(user=self.user).order_by('starts_at'))
        self.assertEqual(periods[0].ends_at, periods[1].starts_at)
        self.assertEqual(plans.lab_period(self.user).pk, periods[0].pk)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
    def test_no_free_checkout_in_production(self):
        self.assertEqual(self.client.get("/tariffs/pro/test-pay/").status_code, 404)
        self.assertEqual(self.client.post("/tariffs/pro/test-pay/confirm/").status_code, 404)
        self.assertFalse(LabPlanPeriod.objects.exists())

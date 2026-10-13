"""Тесты тарифов Free/Pro: активация, квота, страница и тестовая оплата."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from founder.models import User
from founder.services import plans


@override_settings(PRO_DAYS=30, AI_REQUESTS_PER_DAY=200, PRO_AI_REQUESTS_PER_DAY=500)
class PlanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", email="u@example.com", password="***")

    def test_free_by_default(self):
        self.assertFalse(plans.is_pro(self.user))
        self.assertEqual(plans.quota(self.user)["plan"], plans.FREE)
        self.assertEqual(plans.quota(self.user)["requests_per_day"], 200)

    def test_activate_pro_grants_30_days(self):
        plans.activate_pro(self.user)
        self.user.refresh_from_db()
        self.assertTrue(plans.is_pro(self.user))
        self.assertGreater(self.user.pro_until, timezone.now() + timedelta(days=29))
        self.assertEqual(plans.quota(self.user)["requests_per_day"], 500)

    def test_expired_pro_is_free(self):
        self.user.pro_until = timezone.now() - timedelta(days=1)
        self.user.save(update_fields=["pro_until"])
        self.assertFalse(plans.is_pro(self.user))

    def test_extend_pro_adds_days(self):
        first = plans.activate_pro(self.user, days=10)
        second = plans.activate_pro(self.user, days=10)
        self.assertGreater(second, first + timedelta(days=9))

    def test_anonymous_is_not_pro(self):
        self.assertFalse(plans.is_pro(None))


@override_settings(
    PRO_DAYS=30, AI_REQUESTS_PER_DAY=200, PRO_AI_REQUESTS_PER_DAY=500, PRO_PRICE_RUB="149"
)
class TariffViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="v", email="v@example.com", password="***")
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

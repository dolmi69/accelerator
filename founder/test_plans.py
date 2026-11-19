from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import User, StartupProfile
from founder.services import lab_models, plans
from founder.services.ai_costs import CostLimitError, billing_scope, reserve, settle
from founder.services.plans import is_pro, quota
from founder.services.qwen import CodeResult


@override_settings(LAB_REQUESTS_PER_DAY=1)
class PlanQuotaTests(TestCase):
    def setUp(self):
        from founder.test_lab_support import install_lab_mocks
        install_lab_mocks(self)
        self.user = User.objects.create_user(username='plan-owner', email='plan@example.test')
        self.project = StartupProfile.objects.create(owner=self.user, name='Сайт')
        self.client.force_login(self.user)

    def test_pro_expiry_uses_server_time(self):
        self.assertFalse(is_pro(self.user))
        self.assertEqual(quota(self.user, 'lab_requests_per_day'), 1)
        self.user.pro_until = timezone.now() + timedelta(days=1)
        self.assertTrue(is_pro(self.user))
        self.assertEqual(quota(self.user, 'lab_requests_per_day'), 0)
        self.user.pro_until = timezone.now() - timedelta(seconds=1)
        self.assertFalse(is_pro(self.user))

    def test_upgrade_uses_balance_instead_of_free_daily_quota(self):
        route = reverse('lab_generate', args=[self.project.pk])
        html = '<!doctype html><html><head></head><body><h1>Demo</h1></body></html>'
        with patch('founder.lab_views.generate_site', return_value=CodeResult(html, 'Qwen', 4, 8)) as api:
            self.assertEqual(self.client.post(route, {'prompt':'First design'}).status_code, 302)
            self.assertEqual(self.client.post(route, {'prompt':'Second design'}).status_code, 429)
            self.user.pro_until = timezone.now() + timedelta(days=1)
            self.user.save(update_fields=['pro_until'])
            self.assertEqual(self.client.post(route, {'prompt':'Second design'}).status_code, 302)
            self.assertEqual(self.client.post(route, {'prompt':'Third design'}).status_code, 302)
            self.assertEqual(api.call_count, 3)

    def test_expired_pro_returns_to_free_quota(self):
        self.user.pro_until = timezone.now() - timedelta(seconds=1)
        self.user.save(update_fields=['pro_until'])
        self.assertEqual(quota(self.user, 'lab_requests_per_day'), 1)
        with override_settings(LAB_REQUESTS_PER_DAY=0):
            self.assertEqual(quota(self.user, 'lab_requests_per_day'), 0)

    @override_settings(LAB_UNRESTRICTED_TEST_MODE=True)
    def test_plan_accounts_for_each_model_and_only_one_intro_per_period(self):
        plans.activate_pro(self.user, 30)
        period = plans.lab_period(self.user)
        pricing = ('200', '1000')
        with plans.lab_plan_request(self.user, introductory=True):
            for operation in ('intent', 'create', 'create'):
                with billing_scope(self.user, self.project, operation):
                    usage = reserve(lab_models.SOL, [{'content': 'test'}], 400, pricing=pricing)
                    settle(usage, input_tokens=100, output_tokens=20, status='success')
                    usage.refresh_from_db()
                    self.assertEqual(usage.plan_units, 0)
                    self.assertEqual(usage.accounted_micro_rub, 40_000)
        period.refresh_from_db()
        self.assertTrue(period.intro_used)
        self.assertEqual(period.remaining_units, 10_000_000)
        with plans.lab_plan_request(self.user, introductory=True), billing_scope(self.user, self.project, 'create'):
            usage = reserve(lab_models.SOL, [{'content': 'test'}], 400, pricing=pricing)
            settle(usage, input_tokens=100, output_tokens=20, status='success')
            settle(usage, input_tokens=100, output_tokens=20, status='success')
        with plans.lab_plan_request(self.user), billing_scope(self.user, self.project, 'patch'):
            usage = reserve(lab_models.LUNA, [{'content': 'test'}], 400, pricing=('10', '50'))
            settle(usage, input_tokens=100, output_tokens=20, status='output_error')
        period.refresh_from_db()
        self.assertEqual(period.used_units, 4_200)

    @override_settings(LAB_UNRESTRICTED_TEST_MODE=True)
    def test_depleted_plan_refuses_paid_calls_but_keeps_intro(self):
        plans.activate_pro(self.user, 30)
        period = plans.lab_period(self.user)
        period.used_units = period.included_units
        period.save(update_fields=['used_units'])
        with plans.lab_plan_request(self.user), billing_scope(self.user, self.project, 'patch'):
            with self.assertRaisesRegex(CostLimitError, 'не хватает токенов'):
                reserve(lab_models.LUNA, [{'content': 'test'}], 400, pricing=('10', '50'))
        with plans.lab_plan_request(self.user, introductory=True), billing_scope(self.user, self.project, 'create'):
            usage = reserve(lab_models.SOL, [{'content': 'test'}], 400, pricing=('200', '1000'))
            self.assertEqual(usage.plan_units, 0)

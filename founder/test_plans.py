from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import User, StartupProfile
from founder.services.plans import is_pro, quota
from founder.services.qwen import CodeResult


@override_settings(LAB_REQUESTS_PER_DAY=1, LAB_PRO_REQUESTS_PER_DAY=2)
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
        self.assertEqual(quota(self.user, 'lab_requests_per_day'), 2)
        self.user.pro_until = timezone.now() - timedelta(seconds=1)
        self.assertFalse(is_pro(self.user))

    def test_upgrade_keeps_used_quota_and_does_not_charge_templates(self):
        route = reverse('lab_generate', args=[self.project.pk])
        html = '<!doctype html><html><head></head><body><h1>Demo</h1></body></html>'
        with patch('founder.lab_views.generate_site', return_value=CodeResult(html, 'Qwen', 4, 8)) as api:
            self.assertEqual(self.client.post(route, {'prompt':'First design'}).status_code, 302)
            self.assertEqual(self.client.post(route, {'prompt':'Second design'}).status_code, 429)
            self.user.pro_until = timezone.now() + timedelta(days=1)
            self.user.save(update_fields=['pro_until'])
            self.assertEqual(self.client.post(route, {'prompt':'Second design'}).status_code, 302)
            self.assertEqual(self.client.post(route, {'prompt':'Third design'}).status_code, 429)
            self.assertEqual(api.call_count, 2)

    def test_expired_pro_returns_to_free_quota(self):
        self.user.pro_until = timezone.now() - timedelta(seconds=1)
        self.user.save(update_fields=['pro_until'])
        self.assertEqual(quota(self.user, 'lab_requests_per_day'), 1)
        with override_settings(LAB_REQUESTS_PER_DAY=0):
            self.assertEqual(quota(self.user, 'lab_requests_per_day'), 0)

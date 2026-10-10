from django.test import TestCase, override_settings
from django.urls import reverse

from founder.models import CardReport, StartupProfile, User
from founder.services.project_cards import card_values, get_card, save_card


class CardReportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('report_owner', email='report_owner@example.test')
        self.alice = User.objects.create_user('report_alice', email='report_alice@example.test')
        self.bob = User.objects.create_user('report_bob', email='report_bob@example.test')
        project = StartupProfile.objects.create(owner=self.owner, name='Быстрые деньги')
        self.card = get_card(project)
        save_card(self.card, {**card_values(self.card), 'name': 'Быстрые деньги', 'summary': 'Удвоим вклад'},
                  self.card.revision, publish=True)
        self.startup_id = project.pk
        self.report_url = reverse('card_report', args=[project.pk])
        self.detail_url = reverse('card_detail', args=[project.pk])

    def test_button_in_card_corner_and_no_mark_before_report(self):
        self.client.force_login(self.alice)
        for url in (reverse('community'), self.detail_url):
            page = self.client.get(url)
            self.assertContains(page, 'data-report-form')
            self.assertContains(page, f'action="{self.report_url}"')
            self.assertNotContains(page, 'danger-label')
            self.assertContains(page, 'community-tag')  # The stage is still shown.

    def test_report_shows_danger_mark_and_is_counted_once(self):
        self.client.force_login(self.alice)
        response = self.client.post(self.report_url, HTTP_ACCEPT='application/json')
        self.assertEqual(response.json(), {'reported': True})
        self.client.post(self.report_url, HTTP_ACCEPT='application/json')
        self.assertEqual(CardReport.objects.count(), 1)
        for url in (reverse('community'), self.detail_url):
            page = self.client.get(url)
            self.assertContains(page, '<p class="danger-label">Опасно!</p>', html=True)
            self.assertContains(page, 'report-button is-reported')
            self.assertNotContains(page, 'data-report-form')

    def test_form_without_javascript_redirects_back(self):
        self.client.force_login(self.alice)
        community = reverse('community') + '?stage=idea'
        response = self.client.post(self.report_url, {'next': community})
        self.assertRedirects(response, community, fetch_redirect_response=False)
        evil = self.client.post(self.report_url, {'next': 'https://evil.example/'})
        self.assertRedirects(evil, self.detail_url, fetch_redirect_response=False)

    def test_owner_cannot_report_and_sees_no_button(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.detail_url), 'data-report-form')
        self.assertEqual(self.client.post(self.report_url, HTTP_ACCEPT='application/json').status_code, 400)
        self.assertFalse(CardReport.objects.exists())

    def test_only_logged_in_users_and_post(self):
        self.assertEqual(self.client.post(self.report_url).status_code, 302)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(self.report_url).status_code, 405)
        self.assertFalse(CardReport.objects.exists())

    def test_unpublished_card_cannot_be_reported(self):
        self.card.published_at = None
        self.card.save(update_fields=['published_at'])
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(self.report_url).status_code, 404)

    @override_settings(CARD_REPORT_THRESHOLD=2)
    def test_threshold_controls_what_others_see(self):
        self.client.force_login(self.alice)
        self.client.post(self.report_url, HTTP_ACCEPT='application/json')
        self.assertContains(self.client.get(self.detail_url), 'danger-label')  # Reporter sees it at once.
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.detail_url), 'danger-label')
        self.client.force_login(self.bob)
        self.client.post(self.report_url, HTTP_ACCEPT='application/json')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.detail_url), 'danger-label')
        self.assertContains(self.client.get(self.owner.get_absolute_url()), 'danger-label')

    def test_default_one_report_marks_card_for_everyone(self):
        self.client.force_login(self.alice)
        self.client.post(self.report_url, HTTP_ACCEPT='application/json')
        self.client.force_login(self.bob)
        page = self.client.get(reverse('community'))
        self.assertContains(page, 'danger-label')
        self.assertContains(page, 'data-report-form')  # Bob can still add his own report.

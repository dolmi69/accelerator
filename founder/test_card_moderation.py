from django.test import TestCase
from django.urls import reverse

from founder.models import CardReport, ProjectCard, StartupProfile, User
from founder.services.card_moderation import find_violation, withdraw
from founder.services.card_rules import first_violation
from founder.services.project_cards import CardBlockedError, card_values, get_card, save_card


class RuleTests(TestCase):
    def test_stems_and_inflections_are_caught_whatever_the_case(self):
        self.assertEqual(first_violation('Ты — сука').code, 'MAT-05')
        self.assertEqual(first_violation('Пиздец').code, 'MAT-02')
        self.assertEqual(first_violation('Блядь!').code, 'MAT-04')
        self.assertEqual(first_violation('долбоёб').code, 'MAT-07')  # ё is normalised to е
        self.assertEqual(first_violation('ХАЙЛЬ ГИТЛЕР').code, 'NAZI-01')
        self.assertEqual(first_violation('Sieg Heil').code, 'NAZI-01')
        self.assertEqual(first_violation('Смерть  евреям').code, 'NAZI-02')  # several spaces
        self.assertEqual(first_violation('Кровь и почва').code, 'NAZI-03')

    def test_neutral_words_that_contain_a_stem_are_not_blocked(self):
        for text in ('Сукно и шерсть', 'Хуже не будет', 'Бляшка на столе', 'Мудрость ведёт',
                     'Европейцы и азиаты', 'Суккуленты для дома', 'Хайлайт в видео', 'Смерть без страха'):
            with self.subTest(text=text):
                self.assertIsNone(first_violation(text))

    def test_numbers_and_empty_text_are_never_violations(self):
        self.assertIsNone(first_violation('88 клиентов и 14 заявок'))
        self.assertIsNone(first_violation(''))
        self.assertIsNone(first_violation(None))


class ServiceTests(TestCase):
    def test_only_text_values_of_the_published_snapshot_are_checked(self):
        self.assertEqual(find_violation({'name': 'Клиника', 'summary': 'Хайль Гитлер'}).code, 'NAZI-01')
        self.assertIsNone(find_violation({'name': 'Клиника', 'radar': {'score': 3}, 'stage': 'idea'}))
        self.assertIsNone(find_violation({}))


class CardModerationFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('mod_owner', email='mod_owner@example.test')
        self.alice = User.objects.create_user('mod_alice', email='mod_alice@example.test')
        self.project = StartupProfile.objects.create(owner=self.owner, name='Клиника')
        self.report_url = reverse('card_report', args=[self.project.pk])
        self.edit_url = reverse('card_edit', args=[self.project.pk])
        self.detail_url = reverse('card_detail', args=[self.project.pk])
        self.community_url = reverse('community')

    def publish(self, **changes):
        card = get_card(self.project)
        values = {**card_values(card), 'name': 'Клиника', 'summary': 'Расписание без очередей', **changes}
        save_card(card, values, card.revision, publish=True)

    def stored(self):
        return ProjectCard.objects.get(startup=self.project)

    def test_violation_withdraws_card_and_stores_rule_reason_and_date(self):
        self.publish(summary='Хайль Гитлер')
        self.client.force_login(self.alice)
        data = self.client.post(self.report_url, HTTP_ACCEPT='application/json').json()
        self.assertEqual(data, {'reported': True, 'removed': True, 'reason': 'Нацистский лозунг',
                                'redirect': self.community_url})
        card = self.stored()
        self.assertIsNone(card.published_at)
        self.assertEqual(card.published_data, {})
        self.assertIsNotNone(card.blocked_at)
        self.assertEqual((card.block_rule, card.block_reason), ('NAZI-01', 'Нацистский лозунг'))
        self.assertEqual(CardReport.objects.get().rule, 'NAZI-01')
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)

    def test_clean_card_keeps_the_complaint_and_stays_published(self):
        self.publish()
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(self.report_url, HTTP_ACCEPT='application/json').json(),
                         {'reported': True})
        card = self.stored()
        self.assertIsNotNone(card.published_at)
        self.assertIsNone(card.blocked_at)
        self.assertEqual(CardReport.objects.get().rule, '')

    def test_only_published_text_is_checked_not_the_draft(self):
        self.publish()
        card = self.stored()
        self.client.force_login(self.owner)
        self.client.post(self.edit_url, {**card_values(card), 'summary': 'Хайль Гитлер',
                                         'revision': card.revision, 'action': 'save'})
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(self.report_url, HTTP_ACCEPT='application/json').json(),
                         {'reported': True})
        self.assertIsNotNone(self.stored().published_at)

    def test_author_sees_the_reason_and_cannot_publish_after_editing(self):
        self.publish(summary='Хайль Гитлер')
        self.client.force_login(self.alice)
        self.client.post(self.report_url, HTTP_ACCEPT='application/json')
        self.client.force_login(self.owner)
        page = self.client.get(self.edit_url)
        self.assertContains(page, 'Карточка снята с публикации')
        self.assertContains(page, 'нацистский лозунг (правило NAZI-01)')
        self.assertContains(page, 'disabled title="Карточку нельзя опубликовать снова"')
        card = self.stored()
        response = self.client.post(self.edit_url, {**card_values(card), 'summary': 'Чистый текст',
                                                    'revision': card.revision, 'action': 'publish'})
        # A rejected form is answered with 400 here, the same way as a stale-revision error.
        self.assertContains(response, 'Публикация заблокирована: Нацистский лозунг', status_code=400)
        self.assertIsNone(self.stored().published_at)

    def test_author_can_still_save_a_draft_of_a_blocked_card(self):
        self.publish(summary='Хайль Гитлер')
        self.client.force_login(self.alice)
        self.client.post(self.report_url, HTTP_ACCEPT='application/json')
        self.client.force_login(self.owner)
        card = self.stored()
        response = self.client.post(self.edit_url, {**card_values(card), 'name': 'Клиника 2',
                                                    'revision': card.revision, 'action': 'save'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored().name, 'Клиника 2')

    def test_save_card_refuses_publishing_a_blocked_card(self):
        self.publish(summary='Хайль Гитлер')
        card = self.stored()
        withdraw(card, first_violation('Хайль Гитлер'))
        card = self.stored()
        with self.assertRaises(CardBlockedError):
            save_card(card, {**card_values(card), 'summary': 'Чисто'}, card.revision, publish=True)

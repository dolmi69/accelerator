"""Монеты: начисления, дневные лимиты и продвижение проекта."""
import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from founder.models import (BrunoTask, ChatSession, CoinTransaction, LabSiteVersion, ProjectMember, Promotion,
                            StartupProfile, User)
from founder.services.coins import EARN_RULES, OFFERS, WELCOME_BONUS, CoinError, award, buy_promotion, plural_coins
from founder.services.project_cards import card_values, get_card, save_card

Kind = CoinTransaction.Kind


def coins(user):
    user.refresh_from_db()
    return user.coins


@override_settings(AI_PROVIDER='demo')
class CoinTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('coin_owner', email='coins@example.test', handle='coin_owner')
        self.startup = StartupProfile.objects.create(owner=self.user, name='Coin Clinic')
        self.client.force_login(self.user)

    def url(self, name, *args):
        return reverse(name, args=[self.startup.pk, *args])

    def publish_card(self, startup=None):
        startup = startup or self.startup
        card = get_card(startup)
        save_card(card, {**card_values(card), 'summary': 'Публичное описание'}, card.revision, publish=True)

    def test_new_account_gets_welcome_bonus_once(self):
        self.assertEqual(coins(self.user), WELCOME_BONUS)
        self.user.save()
        self.assertEqual(self.user.coin_transactions.filter(kind=Kind.WELCOME).count(), 1)

    def test_award_is_idempotent_and_capped_per_day(self):
        rule = EARN_RULES[Kind.EVIDENCE]
        self.assertEqual(award(self.user, Kind.EVIDENCE, key='a'), rule.amount)
        self.assertEqual(award(self.user, Kind.EVIDENCE, key='a'), 0)
        total = rule.amount
        for index in range(20):
            total += award(self.user, Kind.EVIDENCE, key=f'b{index}')
        self.assertEqual(total, rule.daily_cap)
        self.assertEqual(coins(self.user), WELCOME_BONUS + rule.daily_cap)

    def test_chat_awards_meaningful_messages_only(self):
        session = ChatSession.objects.create(startup=self.startup, mode=ChatSession.Mode.COFOUNDER)
        send = lambda text: b''.join(self.client.post(self.url('chat_send', session.pk),
                                                      {'content': text}).streaming_content).decode()
        events = send('Мы опросили двенадцать клиник в августе')
        self.assertIn('"coins_earned": 2', events)
        self.assertNotIn('coins_earned', send('ок'))
        self.assertEqual(coins(self.user), WELCOME_BONUS + 2)

    def test_evidence_and_task_completion_award_coins(self):
        task = BrunoTask.objects.create(startup=self.startup, axis='market', title='Опросить клиники',
                                        instructions='...', success_criterion='5 ответов')
        response = self.client.post(self.url('evidence_create') + f'?task={task.pk}', {
            'task': task.pk, 'axis': 'market', 'claim': 'Клиники ждут', 'observation': '4 из 5 ждут',
            'observed_on': timezone.localdate().isoformat(), 'outcome': 'supported',
        })
        self.assertRedirects(response, self.url('evidence_list'))
        expected = EARN_RULES[Kind.EVIDENCE].amount + EARN_RULES[Kind.TASK].amount
        self.assertEqual(coins(self.user), WELCOME_BONUS + expected)
        entry = self.startup.evidence_entries.get()
        self.client.post(self.url('evidence_edit', entry.pk), {
            'task': task.pk, 'axis': 'market', 'claim': 'Клиники ждут', 'observation': 'правка',
            'observed_on': timezone.localdate().isoformat(), 'outcome': 'supported',
        })
        self.assertEqual(coins(self.user), WELCOME_BONUS + expected)

    def test_promotion_requires_published_card_and_enough_coins(self):
        with self.assertRaisesMessage(CoinError, 'опубликуйте карточку'):
            buy_promotion(self.user, self.startup, Promotion.Kind.FEED_TOP)
        self.publish_card()
        self.user.refresh_from_db()
        with self.assertRaisesMessage(CoinError, 'Не хватает монет'):
            buy_promotion(self.user, self.startup, Promotion.Kind.FEED_TOP)
        self.assertEqual(coins(self.user), WELCOME_BONUS)
        with self.assertRaisesMessage(CoinError, 'прототип'):
            buy_promotion(self.user, self.startup, Promotion.Kind.TESTERS)

    def test_buying_extends_and_debits(self):
        self.publish_card()
        award(self.user, Kind.TASK, key='t1')
        award(self.user, Kind.TASK, key='t2')
        cost = OFFERS[Promotion.Kind.HIGHLIGHT].cost
        before = coins(self.user)
        response = self.client.post(self.url('promote_buy'), {'kind': 'highlight'})
        self.assertRedirects(response, self.url('promote'))
        first = Promotion.objects.get()
        buy_promotion(self.user, self.startup, Promotion.Kind.HIGHLIGHT)
        second = Promotion.objects.exclude(pk=first.pk).get()
        self.assertEqual(second.starts_at, first.ends_at)
        self.assertEqual(coins(self.user), before - 2 * cost)
        self.assertEqual(self.user.coin_transactions.filter(kind=Kind.PROMOTION).count(), 2)

    def test_promoted_card_goes_first_in_feed(self):
        other_owner = User.objects.create_user('later', email='later@example.test')
        later = StartupProfile.objects.create(owner=other_owner, name='Later Project')
        self.publish_card()
        self.publish_card(later)  # опубликован позже — без продвижения был бы первым
        Promotion.objects.create(startup=self.startup, kind=Promotion.Kind.FEED_TOP, cost=60,
                                 ends_at=timezone.now() + timedelta(days=1))
        Promotion.objects.create(startup=later, kind=Promotion.Kind.HIGHLIGHT, cost=30,
                                 ends_at=timezone.now() - timedelta(minutes=1))
        page = self.client.get(reverse('community')).content.decode()
        self.assertLess(page.index('Coin Clinic'), page.index('Later Project'))
        self.assertIn('Продвигается', page)
        self.assertNotIn('is-highlighted', page)

    def test_external_tester_earns_but_team_does_not(self):
        version = LabSiteVersion.objects.create(startup=self.startup, html='<html><body>ok</body></html>',
                                               prompt='p', model='m')
        self.publish_card()
        self.client.post(self.url('lab_publish'), {'version': str(version.pk), 'visibility': 'public'})
        mate = User.objects.create_user('mate_t', email='mt@example.test')
        ProjectMember.objects.create(startup=self.startup, user=mate, status='active')
        tester = User.objects.create_user('tester_t', email='tt@example.test')
        for user, expected in ((tester, EARN_RULES[Kind.TEST].amount), (mate, 0), (self.user, 0)):
            self.client.force_login(user)
            before = coins(user)
            session = self.client.post(self.url('lab_test_start'), {'version': str(version.pk)}).json()
            finish = self.client.post(session['finish'], json.dumps({'rating': 5, 'feedback': '', 'duration': 30}),
                                      content_type='application/json')
            self.assertEqual(finish.json().get('coins_earned', 0), expected)
            self.assertEqual(coins(user) - before, expected)

    def test_wallet_page_and_plural(self):
        response = self.client.get(reverse('wallet'))
        self.assertContains(response, 'Стартовый бонус')
        self.assertContains(response, 'Поднять карточку в ленте')
        self.assertEqual([plural_coins(n) for n in (1, 2, 5, 11, 21, 104)],
                         ['монета', 'монеты', 'монет', 'монет', 'монета', 'монеты'])

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


@override_settings(AI_PROVIDER='demo')
class StreakTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('streaker', email='streak@example.test')

    def work_on(self, days_ago, key):
        award(self.user, Kind.EVIDENCE, key=key)
        CoinTransaction.objects.filter(key=f'evidence:{key}').update(
            created_at=timezone.now() - timedelta(days=days_ago))

    def test_fifth_day_in_a_row_pays_bonus_once(self):
        from founder.services.coins import STREAK_BONUS, streak
        for days_ago in (4, 3, 2, 1):
            self.work_on(days_ago, f'd{days_ago}')
        self.assertEqual(streak(self.user), 4)  # сегодня ещё не потерян
        before = coins(self.user)
        self.assertEqual(award(self.user, Kind.EVIDENCE, key='today'), EARN_RULES[Kind.EVIDENCE].amount + STREAK_BONUS)
        self.assertEqual(award(self.user, Kind.EVIDENCE, key='today2'), EARN_RULES[Kind.EVIDENCE].amount)
        self.assertEqual(coins(self.user), before + 2 * EARN_RULES[Kind.EVIDENCE].amount + STREAK_BONUS)
        self.assertEqual(streak(self.user), 5)

    def test_gap_resets_streak(self):
        from founder.services.coins import streak
        self.work_on(3, 'old')
        self.work_on(1, 'yesterday')
        self.assertEqual(streak(self.user), 1)
        self.assertEqual(self.client.get(reverse('wallet')).status_code, 302)  # нужен вход
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('wallet')), '1 день подряд')


@override_settings(AI_PROVIDER='demo')
class BountyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('bounty_owner', email='bo@example.test')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Bounty Proto')
        self.version = LabSiteVersion.objects.create(startup=self.startup, html='<html><body>ok</body></html>',
                                                     prompt='p', model='m')
        card = get_card(self.startup)
        save_card(card, {**card_values(card), 'summary': 'Публичное описание'}, card.revision, publish=True)
        self.client.force_login(self.owner)
        self.client.post(reverse('lab_publish', args=[self.startup.pk]),
                         {'version': str(self.version.pk), 'visibility': 'public'})

    def run_test(self, user, feedback):
        self.client.force_login(user)
        session = self.client.post(reverse('lab_test_start', args=[self.startup.pk]),
                                   {'version': str(self.version.pk)}).json()
        return self.client.post(session['finish'], json.dumps({'rating': 4, 'feedback': feedback, 'duration': 60}),
                                content_type='application/json').json()

    def test_funding_paying_and_refund(self):
        from founder.models import TestBounty
        response = self.client.post(reverse('bounty_fund', args=[self.startup.pk]), {'reward': 10, 'tests': 2})
        self.assertRedirects(response, reverse('promote', args=[self.startup.pk]))
        self.assertEqual(coins(self.owner), WELCOME_BONUS - 20)
        good = 'Понятно, что делает сервис, но кнопка записи спрятана внизу.'
        tester = User.objects.create_user('bounty_tester', email='bt@example.test')
        lazy = User.objects.create_user('bounty_lazy', email='bl@example.test')
        # Короткий отзыв: только стандартные монеты за тест, без награды автора.
        self.assertEqual(self.run_test(lazy, 'норм')['coins_earned'], EARN_RULES[Kind.TEST].amount)
        self.assertEqual(self.run_test(tester, good)['coins_earned'], 10 + EARN_RULES[Kind.TEST].amount)
        # Второй тест того же проекта награду автора не приносит.
        self.assertEqual(self.run_test(tester, good)['coins_earned'], EARN_RULES[Kind.TEST].amount)
        bounty = TestBounty.objects.get()
        self.assertEqual(bounty.remaining, 10)
        self.client.force_login(self.owner)
        self.client.post(reverse('bounty_close', args=[self.startup.pk, bounty.pk]))
        self.assertEqual(coins(self.owner), WELCOME_BONUS - 10)
        bounty.refresh_from_db()
        self.assertIsNotNone(bounty.closed_at)

    def test_bounty_shown_in_testers_block_and_trial(self):
        self.client.post(reverse('bounty_fund', args=[self.startup.pk]), {'reward': 20, 'tests': 1})
        visitor = User.objects.create_user('bounty_visitor', email='bv@example.test')
        self.client.force_login(visitor)
        self.assertContains(self.client.get(reverse('community')), '+20 🪙 от автора')
        self.assertContains(self.client.get(reverse('lab_trial', args=[self.startup.pk])), 'Автор платит')

    def test_cannot_fund_without_coins_or_public_prototype(self):
        self.client.post(reverse('bounty_fund', args=[self.startup.pk]), {'reward': 30, 'tests': 20})
        self.assertEqual(coins(self.owner), WELCOME_BONUS)
        self.client.post(reverse('lab_publish', args=[self.startup.pk]), {'action': 'hide'})
        self.client.post(reverse('bounty_fund', args=[self.startup.pk]), {'reward': 5, 'tests': 1})
        self.assertEqual(coins(self.owner), WELCOME_BONUS)


@override_settings(AI_PROVIDER='demo')
class WardrobeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('dresser', email='dr@example.test')
        self.startup = StartupProfile.objects.create(owner=self.user, name='Dress Bruno')
        self.client.force_login(self.user)
        self.url = reverse('wardrobe_action', args=[self.startup.pk])

    def test_buy_wear_and_swap_in_slot(self):
        from founder.models import MascotState
        self.client.post(self.url, {'action': 'buy', 'code': 'party'})
        self.assertEqual(coins(self.user), WELCOME_BONUS - 30)
        state = MascotState.objects.get(startup=self.startup)
        self.assertEqual(state.accessories, ['party'])
        self.client.post(self.url, {'action': 'buy', 'code': 'party'})
        self.assertEqual(coins(self.user), WELCOME_BONUS - 30)  # второй раз не списывает
        page = self.client.get(reverse('dashboard', args=[self.startup.pk]))
        self.assertContains(page, 'accessory-party')
        # При нехватке монет покупка не проходит.
        self.client.post(self.url, {'action': 'buy', 'code': 'crown'})
        self.assertFalse(self.startup.mascot_items.filter(code='crown').exists())
        award(self.user, Kind.TASK, key='earn-for-bowtie')
        self.client.post(self.url, {'action': 'buy', 'code': 'bowtie'})
        state.refresh_from_db()
        self.assertEqual(sorted(state.accessories), ['bowtie', 'party'])
        self.client.post(self.url, {'action': 'take_off', 'code': 'party'})
        state.refresh_from_db()
        self.assertEqual(state.accessories, ['bowtie'])
        self.client.post(self.url, {'action': 'wear', 'code': 'cap'})  # не куплено
        state.refresh_from_db()
        self.assertEqual(state.accessories, ['bowtie'])

    def test_pose_frames_match_bruno_template(self):
        """Аксессуары рисуются в той же области, что и картинка позы в _bruno.html."""
        import re
        from django.template.loader import render_to_string
        from founder.models import MascotState
        from founder.services.wardrobe import CATALOG, POSES
        for mood, pose in POSES.items():
            x, y, width, height, view_box = pose.frame
            html = render_to_string('founder/_bruno.html', {'mascot': MascotState(mood=mood), 'investor': False})
            self.assertIn(f'<svg x="{x}" y="{y}" width="{width}" height="{height}" viewBox="{view_box}"', html, mood)
            for outfit in (['party', 'glasses', 'bowtie'], ['cap', 'shades', 'scarf'], ['crown']):
                dressed = render_to_string('founder/_bruno.html', {
                    'mascot': MascotState(mood=mood, accessories=outfit), 'investor': False})
                # В русской локали дробные числа выводятся с запятой — SVG такое не понимает.
                self.assertIsNone(re.search(r'="[^"]*\d,\d', dressed), (mood, outfit))
                self.assertIn(f'viewBox="{view_box}" overflow="visible"', dressed)
        self.assertEqual(set(CATALOG), {'party', 'cap', 'crown', 'glasses', 'shades', 'bowtie', 'scarf'})

    def test_reading_pose_keeps_neck_items_behind_book(self):
        from django.template.loader import render_to_string
        from founder.models import MascotState
        from founder.services.wardrobe import hidden_now, layers
        reading = MascotState(mood=MascotState.Mood.FOCUSED, accessories=['bowtie', 'glasses'])
        self.assertEqual([item['code'] for item in layers(reading)['items']], ['glasses'])
        self.assertEqual([item.code for item in hidden_now(reading)], ['bowtie'])
        scarf = render_to_string('founder/_bruno.html', {
            'mascot': MascotState(mood=MascotState.Mood.FOCUSED, accessories=['scarf']), 'investor': False})
        self.assertIn('mask="url(#bruno-cover-card)"', scarf)
        upright = MascotState(mood=MascotState.Mood.CURIOUS, accessories=['bowtie'])
        self.assertFalse(layers(upright)['items'][0]['behind'])

    def test_accessories_skip_investor(self):
        self.client.post(self.url, {'action': 'buy', 'code': 'bowtie'})
        page = self.client.get(reverse('investor', args=[self.startup.pk])).content.decode()
        investor, pet = page.split('class="bruno-pet"')
        self.assertNotIn('accessory-bowtie', investor)  # строгий инвестор без нарядов
        self.assertIn('accessory-bowtie', pet)  # а помощник в углу — в бабочке

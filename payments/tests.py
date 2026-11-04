"""Тесты кошелька: деривация, баланс, пополнение и сверка оплаты."""

import time
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from founder.models import User
from payments.models import Balance, Invoice
from payments.services import checker, hd

# Эталон: тестовый мнемоник «abandon…about», путь m/44'/195'/0'/0.
# Значения один раз посчитаны bip-utils и зафиксированы — тесты не требуют крипто-библиотек.
TEST_XPUB = ("xpub6EuK4CZWW5urEHdwAVDdDw327danAtccFcrXYvgf1DHrPXRwErt36xStQ2PNhn4hpwzPbzJ8p"
             "JVpewgChRnSs59q5Ay61GCfQZKUe71gbLq")
TEST_ADDRESSES = ["TUEZSdKsoDHQMeZwihtdoBiN46zxhGWYdH", "TSeJkUh4Qv67VNFwY8LaAxERygNdy6NQZK",
                  "TYJPRrdB5APNeRs4R7fYZSwW3TcrTKw2gx"]
TEST_ADDRESS = TEST_ADDRESSES[0]


@override_settings(PAYMENTS_XPUB=TEST_XPUB, PAYMENTS_RECEIVE_INDEX=0)
class DerivationTests(TestCase):
    def test_keccak256_matches_the_known_vector(self):
        self.assertEqual(
            hd.keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )

    def test_xpub_derivation_matches_known_addresses(self):
        for index, expected in enumerate(TEST_ADDRESSES):
            self.assertEqual(hd.derive_tron_address(index), expected)

    def test_receiving_address_is_index_0(self):
        self.assertEqual(hd.receiving_address(), TEST_ADDRESS)

    @override_settings(PAYMENTS_XPUB="")
    def test_missing_xpub_is_a_clear_error(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            hd.receiving_address()


class FormatTests(TestCase):
    def test_invoice_amount_text(self):
        self.assertEqual(Invoice(amount_expected=Decimal("0.500000")).amount_expected_text, "0.5")
        self.assertEqual(Invoice(amount_expected=Decimal("1000.000000")).amount_expected_text, "1000")

    def test_balance_amount_text(self):
        self.assertEqual(Balance(amount=Decimal("12.300000")).amount_text, "12.3")


@override_settings(
    PAYMENTS_XPUB=TEST_XPUB,
    PAYMENTS_RECEIVE_INDEX=0,
    PAYMENTS_ENABLED=True,
    PAYMENTS_REQUIRED_CONFIRMATIONS=19,
)
class TopUpViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="paytester", email="paytester@example.com", password="x"
        )
        self.client.force_login(self.user)

    def test_wallet_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/pay/").status_code, 302)

    def test_wallet_shows_balance(self):
        resp = self.client.get("/pay/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "wallet-chip")
        self.assertContains(resp, "USDT:")

    def test_header_hides_empty_wallet_when_payments_are_disabled(self):
        with self.settings(PAYMENTS_ENABLED=False):
            self.assertNotContains(self.client.get("/community/"), "wallet-chip")
            self.assertFalse(Balance.objects.filter(user=self.user).exists())
            Balance.objects.create(user=self.user, amount=Decimal("3"))
            self.assertContains(self.client.get("/community/"), "USDT: 3")

    def test_topup_uses_main_address(self):
        resp = self.client.post("/pay/topup/new/", {"amount": "10"})
        self.assertEqual(resp.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.address, TEST_ADDRESS)
        # Один адрес на всех: сумму делает уникальной хвост в миллионных долях.
        self.assertGreater(invoice.amount_expected, Decimal("10"))
        self.assertLess(invoice.amount_expected, Decimal("10.01"))
        self.assertEqual(invoice.user, self.user)
        self.assertContains(self.client.get(resp["Location"]), TEST_ADDRESS)

    def test_bad_amount_rejected(self):
        resp = self.client.post("/pay/topup/new/", {"amount": "not-a-number"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_same_amount_invoices_get_different_tails(self):
        for _ in range(5):
            self.client.post("/pay/topup/new/", {"amount": "10"})
        amounts = list(Invoice.objects.values_list("amount_expected", flat=True))
        self.assertEqual(len(amounts), 5)
        self.assertEqual(len(set(amounts)), 5)

    def test_invalid_amounts_are_rejected_without_server_error(self):
        for raw in ("NaN", "Infinity", "-1", "0.5", "10.555"):
            with self.subTest(raw=raw):
                self.assertEqual(self.client.post("/pay/topup/new/", {"amount": raw}).status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_amount_over_max_rejected(self):
        resp = self.client.post("/pay/topup/new/", {"amount": "100000"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_other_users_invoice_is_hidden(self):
        other = User.objects.create_user(username="stranger", email="stranger@example.com", password="x")
        invoice = Invoice.objects.create(
            user=other, order_id="top_other", amount_expected=Decimal("5"), address=TEST_ADDRESS,
            status=Invoice.Status.PENDING, expires_at=timezone.now() + timedelta(minutes=30),
        )
        for suffix in ("", "status/", "qr.png"):
            with self.subTest(suffix=suffix):
                url = f"/pay/{invoice.id}/{suffix}"
                self.assertEqual(self.client.get(url).status_code, 404)


@override_settings(
    PAYMENTS_XPUB=TEST_XPUB,
    PAYMENTS_RECEIVE_INDEX=0,
    PAYMENTS_REQUIRED_CONFIRMATIONS=19,
)
class CheckerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", email="u1@example.com", password="x")

    def _make_invoice(self, amount="10", *, user=None, created_minutes_ago=10, expires_in_minutes=20, status=None):
        now = timezone.now()
        return Invoice.objects.create(
            user=user or self.user,
            order_id=f"top_{uuid.uuid4().hex[:12]}",
            amount_expected=Decimal(amount),
            address=TEST_ADDRESS,
            status=status or Invoice.Status.PENDING,
            created_at=now - timedelta(minutes=created_minutes_ago),
            expires_at=now + timedelta(minutes=expires_in_minutes),
        )

    def _patch(self, *transfers):
        """transfers: (value в USDT, возраст перевода в секундах, txid)."""
        now_ms = int(time.time() * 1000)
        data = [
            {"to": TEST_ADDRESS, "value": str(int(Decimal(value) * 10**6)), "transaction_id": txid,
             "block_timestamp": now_ms - age_seconds * 1000}
            for value, age_seconds, txid in reversed(transfers)  # TronGrid отдаёт новые первыми
        ]
        return mock.patch.object(checker.tron, "fetch_incoming_usdt", return_value=data)

    def test_credits_balance_after_enough_confirmations(self):
        invoice = self._make_invoice("10.004213")
        with self._patch(("10.004213", 120, "tx1")):
            stats = checker.check_all()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.txid, "tx1")
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("10.004213"))
        self.assertEqual(stats["paid"], 1)

    def test_no_credit_before_confirmations(self):
        self._make_invoice("10.004213")
        with self._patch(("10.004213", 10, "tx1")):
            checker.check_all()
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("0"))

    def test_no_double_credit(self):
        self._make_invoice("10.004213")
        with self._patch(("10.004213", 200, "tx1")):
            checker.check_all()
            checker.check_all()
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("10.004213"))

    def test_older_small_invoice_does_not_capture_someone_elses_payment(self):
        # Раньше перевод уходил самому старому счёту с суммой не больше перевода,
        # и копеечный счёт забирал чужие 10 USDT целиком.
        attacker = User.objects.create_user(username="u2", email="u2@example.com", password="x")
        dust = self._make_invoice("1.000001", user=attacker, created_minutes_ago=20)
        victim = self._make_invoice("10.004213")
        with self._patch(("10.004213", 120, "tx1")):
            checker.check_all()
        dust.refresh_from_db()
        victim.refresh_from_db()
        self.assertEqual(dust.status, Invoice.Status.PENDING)
        self.assertEqual(victim.status, Invoice.Status.PAID)
        self.assertEqual(Balance.for_user(attacker).amount, Decimal("0"))
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("10.004213"))

    def test_transfer_sent_before_the_invoice_existed_is_not_credited(self):
        # Адрес и переводы видны в блокчейне всем: старый незачисленный перевод нельзя присвоить новым счётом.
        invoice = self._make_invoice("10.004213", created_minutes_ago=1)
        with self._patch(("10.004213", 60 * 60, "old-tx")):
            checker.check_all()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PENDING)
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("0"))

    def test_ambiguous_amount_is_left_for_manual_review(self):
        other = User.objects.create_user(username="u3", email="u3@example.com", password="x")
        first = self._make_invoice("10.004213")
        second = self._make_invoice("10.004213", user=other)
        with self._patch(("10.004213", 120, "tx1")):
            stats = checker.check_all()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(stats["ambiguous"], 1)
        self.assertEqual({first.status, second.status}, {Invoice.Status.PENDING})
        self.assertEqual(Balance.objects.filter(amount__gt=0).count(), 0)

    def test_payment_sent_in_time_but_confirmed_after_expiry_is_credited(self):
        invoice = self._make_invoice("10.004213", created_minutes_ago=31, expires_in_minutes=-1)
        with self._patch(("10.004213", 120, "tx1")):
            checker.check_all()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("10.004213"))

    def test_payment_sent_after_expiry_is_not_credited(self):
        invoice = self._make_invoice("10.004213", created_minutes_ago=40, expires_in_minutes=-10)
        with self._patch(("10.004213", 120, "tx1")):
            checker.check_all()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.EXPIRED)
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("0"))

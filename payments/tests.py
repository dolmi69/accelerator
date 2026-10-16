"""Тесты кошелька: деривация, баланс, пополнение и сверка оплаты."""

import time
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

    def test_topup_uses_main_address(self):
        resp = self.client.post("/pay/topup/new/", {"amount": "10"})
        self.assertEqual(resp.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.address, TEST_ADDRESS)
        self.assertEqual(invoice.amount_expected, Decimal("10"))
        self.assertEqual(invoice.user, self.user)
        self.assertContains(self.client.get(resp["Location"]), TEST_ADDRESS)

    def test_bad_amount_rejected(self):
        resp = self.client.post("/pay/topup/new/", {"amount": "not-a-number"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_amount_over_max_rejected(self):
        resp = self.client.post("/pay/topup/new/", {"amount": "100000"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)


@override_settings(
    PAYMENTS_XPUB=TEST_XPUB,
    PAYMENTS_RECEIVE_INDEX=0,
    PAYMENTS_REQUIRED_CONFIRMATIONS=19,
)
class CheckerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", email="u1@example.com", password="x")

    def _make_invoice(self, amount="10"):
        return Invoice.objects.create(
            user=self.user,
            order_id=f"top_{amount}",
            amount_expected=Decimal(amount),
            address=TEST_ADDRESS,
            status=Invoice.Status.PENDING,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

    def _patch(self, *, value, age_seconds):
        ts = int(time.time() * 1000) - age_seconds * 1000
        return mock.patch.object(
            checker.tron,
            "fetch_incoming_usdt",
            return_value=[
                {"to": TEST_ADDRESS, "value": str(value), "transaction_id": "tx1", "block_timestamp": ts}
            ],
        )

    def test_credits_balance_after_enough_confirmations(self):
        invoice = self._make_invoice()
        with self._patch(value=10 * 10**6, age_seconds=120):
            stats = checker.check_all()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("10"))
        self.assertEqual(stats["paid"], 1)

    def test_no_credit_before_confirmations(self):
        self._make_invoice()
        with self._patch(value=10 * 10**6, age_seconds=10):
            checker.check_all()
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("0"))

    def test_no_double_credit(self):
        self._make_invoice()
        with self._patch(value=10 * 10**6, age_seconds=200):
            checker.check_all()
            checker.check_all()
        self.assertEqual(Balance.for_user(self.user).amount, Decimal("10"))

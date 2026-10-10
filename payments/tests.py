"""Тесты кошелька: деривация, баланс, пополнение и сверка оплаты."""

import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins
from django.test import TestCase, override_settings
from django.utils import timezone

from founder.models import User
from payments.models import Balance, Invoice
from payments.services import checker, hd

TEST_MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
_CHANGE = (
    Bip44.FromSeed(Bip39SeedGenerator(TEST_MNEMONIC).Generate(), Bip44Coins.TRON)
    .Purpose()
    .Coin()
    .Account(0)
    .Change(Bip44Changes.CHAIN_EXT)
)
TEST_XPUB = _CHANGE.PublicKey().ToExtended()
TEST_ADDRESS = _CHANGE.AddressIndex(0).PublicKey().ToAddress()


@override_settings(PAYMENTS_XPUB=TEST_XPUB, PAYMENTS_RECEIVE_INDEX=0)
class DerivationTests(TestCase):
    def test_xpub_derivation_matches_bip44(self):
        for index in range(3):
            expected = _CHANGE.AddressIndex(index).PublicKey().ToAddress()
            self.assertEqual(hd.derive_tron_address(index), expected)

    def test_receiving_address_is_index_0(self):
        self.assertEqual(hd.receiving_address(), TEST_ADDRESS)


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
        self.assertContains(resp, "Кошелёк:")

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

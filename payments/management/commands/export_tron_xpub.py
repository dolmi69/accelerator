"""Локально выводит xpub Tron-кошелька из сид-фразы. Сид никуда не отправляется.

Запускать ТОЛЬКО на своей машине::

    (.venv) pip install bip-utils
    (.venv) python manage.py export_tron_xpub

`bip-utils` намеренно НЕ в requirements.txt: он тянет `coincurve`, который не
собирается на Python 3.14, а серверу для приёма платежей он не нужен — адреса
считаются на чистом Python (`payments/services/hd.py`). Пакет нужен один раз,
только чтобы получить xpub из сид-фразы.

Фраза вводится скрыто и не сохраняется. На экран печатается только публичный
ключ (xpub) и первые адреса для сверки.
"""

import getpass

from django.core.management.base import BaseCommand, CommandError


def _bip_utils():
    try:
        from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins
    except ImportError as exc:
        raise CommandError(
            "Для вывода xpub нужен пакет bip-utils (он тянет coincurve). Его нет в "
            "requirements.txt, чтобы CI ставился на Python 3.14. Установите локально:\n"
            "    pip install bip-utils"
        ) from exc
    return Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins


class Command(BaseCommand):
    help = "Выводит xpub Tron (m/44'/195'/0'/0) из сид-фразы для PAYMENTS_XPUB."

    def handle(self, *args, **options):
        Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins = _bip_utils()

        mnemonic = getpass.getpass("Сид-фраза (ввод скрыт, не сохраняется): ").strip()
        if not mnemonic:
            raise CommandError("Пустой ввод.")

        try:
            seed = Bip39SeedGenerator(mnemonic).Generate()
        except Exception as exc:  # noqa: BLE001 — показываем причину пользователю
            raise CommandError(f"Не удалось разобрать фразу: {exc}") from exc

        change = (
            Bip44.FromSeed(seed, Bip44Coins.TRON)
            .Purpose()
            .Coin()
            .Account(0)
            .Change(Bip44Changes.CHAIN_EXT)
        )
        self.stdout.write("")
        self.stdout.write("Скопируйте строку ниже в .env как PAYMENTS_XPUB:")
        self.stdout.write("")
        self.stdout.write("PAYMENTS_XPUB=" + change.PublicKey().ToExtended())
        self.stdout.write("")
        self.stdout.write("Сверка — первые адреса этого кошелька:")
        for index in range(3):
            self.stdout.write(f"  [{index}] {change.AddressIndex(index).PublicKey().ToAddress()}")
        self.stdout.write("")
        self.stdout.write("Адреса [0..2] должны совпасть с вашим кошельком. Если нет — "
                          "у кошелька другой путь деривации, скажите мне.")

"""Локально выводит xpub Tron-кошелька из сид-фразы. Сид никуда не отправляется.

Запускать ТОЛЬКО на своей машине:

    (.venv) python manage.py export_tron_xpub

Фраза вводится скрыто (не отображается) и не сохраняется. На экран печатается
только публичный ключ (xpub) и несколько первых адресов для сверки.
Приватный ключ и сид-фраза никуда не передаются и не пишутся на диск.
"""

import getpass

from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Выводит xpub Tron (m/44'/195'/0'/0) из сид-фразы для PAYMENTS_XPUB."

    def handle(self, *args, **options):
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
        xpub = change.PublicKey().ToExtended()

        self.stdout.write("")
        self.stdout.write("Скопируйте строку ниже в .env как PAYMENTS_XPUB:")
        self.stdout.write("")
        self.stdout.write("PAYMENTS_XPUB=" + xpub)
        self.stdout.write("")
        self.stdout.write("Сверка — первые адреса этого кошелька:")
        for index in range(3):
            address = change.AddressIndex(index).PublicKey().ToAddress()
            self.stdout.write(f"  [{index}] {address}")
        self.stdout.write("")
        self.stdout.write("Адреса [0..2] должны совпасть с вашим кошельком. Если нет — "
                          "у кошелька другой путь деривации, скажите мне.")

"""Деривация адресов приёма из xpub — только наблюдение, без приватных ключей.

В `.env` кладётся PAYMENTS_XPUB — расширенный ПУБЛИЧНЫЙ ключ (Tron, путь
m/44'/195'/0'/0). Приватный ключ серверу не нужен, поэтому утечка xpub не
даёт доступа к деньгам.

Основной адрес приёма — PAYMENTS_RECEIVE_INDEX (по умолчанию 0). Один адрес
на все пополнения; их различают по ожидаемой сумме.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _xpub() -> str:
    value = (settings.PAYMENTS_XPUB or "").strip()
    if not value:
        raise ImproperlyConfigured("PAYMENTS_XPUB не задан в .env")
    return value


def derive_tron_address(index: int) -> str:
    """Tron-адрес приёма (base58check) для индекса деривации."""
    from bip_utils import Bip32Secp256k1, TrxAddrEncoder

    root = Bip32Secp256k1.FromExtendedKey(_xpub())
    child = root.ChildKey(index)
    return TrxAddrEncoder.EncodeKey(child.PublicKey().KeyObject())


def receiving_address() -> str:
    """Основной адрес приёма (индекс PAYMENTS_RECEIVE_INDEX)."""
    return derive_tron_address(int(settings.PAYMENTS_RECEIVE_INDEX))

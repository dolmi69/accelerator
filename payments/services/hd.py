"""Деривация Tron-адресов приёма из xpub — только наблюдение, без приватных ключей.

В `.env` хранится PAYMENTS_XPUB — расширенный ПУБЛИЧНЫЙ ключ (Tron, путь
m/44'/195'/0'/0). Приватный ключ серверу не нужен: по xpub нельзя потратить деньги.

Реализовано на чистом Python (keccak-256, secp256k1, base58check) без внешних
крипто-библиотек: `bip-utils` тянет `coincurve`, который на Python 3.14 не собирается,
а нам нужны лишь нежёсткие дочерние ключи и адрес. Корректность закреплена тестами
на известных адресах.

Основной адрес приёма — PAYMENTS_RECEIVE_INDEX (по умолчанию 0). Один адрес на все
пополнения; их различают по ожидаемой сумме.
"""

import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# --- base58check ---

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = _ALPHABET[remainder] + encoded
    padding = len(data) - len(data.lstrip(b"\x00"))
    return "1" * padding + (encoded or ("" if padding else "1"))


def _b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        number = number * 58 + _ALPHABET.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    padding = len(text) - len(text.lstrip("1"))
    return b"\x00" * padding + body


def _b58check_encode(payload: bytes) -> str:
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return _b58encode(payload + checksum)


def _b58check_decode(text: str) -> bytes:
    raw = _b58decode(text)
    if hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] != raw[-4:]:
        raise ValueError("Проверка base58check не прошла")
    return raw[:-4]


# --- keccak-256 (Keccak, не SHA3: доменный байт 0x01) ---

_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_MASK = (1 << 64) - 1
_KECCAK_RATE = 136


def _rol(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _keccak_f(state: list) -> None:
    for round_constant in _ROUND_CONSTANTS:
        column = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        delta = [column[(x - 1) % 5] ^ _rol(column[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= delta[x]
        rotated = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                rotated[y][(2 * x + 3 * y) % 5] = _rol(state[x][y], _ROTATION[x][y])
        for x in range(5):
            for y in range(5):
                state[x][y] = rotated[x][y] ^ ((~rotated[(x + 1) % 5][y]) & rotated[(x + 2) % 5][y] & _MASK)
        state[0][0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    state = [[0] * 5 for _ in range(5)]
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\x00" * ((-len(padded)) % _KECCAK_RATE))
    padded[-1] ^= 0x80
    for offset in range(0, len(padded), _KECCAK_RATE):
        block = padded[offset:offset + _KECCAK_RATE]
        for lane in range(_KECCAK_RATE // 8):
            state[lane % 5][lane // 5] ^= int.from_bytes(block[lane * 8:lane * 8 + 8], "little")
        _keccak_f(state)
    output = bytearray()
    for lane in range(4):  # 32 байта = 4 полосы
        output += state[lane % 5][lane // 5].to_bytes(8, "little")
    return bytes(output)


# --- secp256k1 ---

_FIELD = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GENERATOR = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _point_add(first, second):
    if first is None:
        return second
    if second is None:
        return first
    x1, y1 = first
    x2, y2 = second
    if x1 == x2 and (y1 + y2) % _FIELD == 0:
        return None
    if first == second:
        slope = (3 * x1 * x1) * pow(2 * y1, _FIELD - 2, _FIELD) % _FIELD
    else:
        slope = (y2 - y1) * pow(x2 - x1, _FIELD - 2, _FIELD) % _FIELD
    x3 = (slope * slope - x1 - x2) % _FIELD
    return x3, (slope * (x1 - x3) - y1) % _FIELD


def _point_mul(scalar, point):
    result = None
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _decompress(public_key: bytes):
    x = int.from_bytes(public_key[1:33], "big")
    y_squared = (pow(x, 3, _FIELD) + 7) % _FIELD
    y = pow(y_squared, (_FIELD + 1) // 4, _FIELD)
    if (y * y - y_squared) % _FIELD:
        raise ValueError("Публичный ключ не лежит на кривой secp256k1")
    if (y & 1) != (public_key[0] & 1):
        y = _FIELD - y
    return x, y


def _compress(point) -> bytes:
    x, y = point
    return bytes([2 + (y & 1)]) + x.to_bytes(32, "big")


# --- BIP32 и адрес Tron ---


def _parse_xpub(xpub: str):
    raw = _b58check_decode(xpub)
    if len(raw) != 78:
        raise ValueError("xpub неверной длины")
    chain_code, key = raw[13:45], raw[45:78]
    if key[0] not in (2, 3):
        raise ValueError("Нужен xpub (публичный ключ), а не xpriv")
    return chain_code, key


def _child_public(chain_code: bytes, public_key: bytes, index: int):
    """Нежёсткий дочерний ключ BIP32: IL·G + родительская точка."""
    digest = hmac.new(chain_code, public_key + index.to_bytes(4, "big"), hashlib.sha512).digest()
    tweak = int.from_bytes(digest[:32], "big")
    if tweak >= _ORDER:
        raise ValueError("Недопустимый индекс деривации")
    point = _point_add(_point_mul(tweak, _GENERATOR), _decompress(public_key))
    if point is None:
        raise ValueError("Дочерний ключ попал в бесконечность")
    return digest[32:], _compress(point)


def _xpub() -> str:
    value = (settings.PAYMENTS_XPUB or "").strip()
    if not value:
        raise ImproperlyConfigured("PAYMENTS_XPUB не задан в .env")
    return value


def derive_tron_address(index: int) -> str:
    """Tron-адрес приёма (base58check) для индекса деривации."""
    chain_code, public_key = _parse_xpub(_xpub())
    _, child = _child_public(chain_code, public_key, index)
    x, y = _decompress(child)
    payload = b"\x41" + keccak256(x.to_bytes(32, "big") + y.to_bytes(32, "big"))[12:]
    return _b58check_encode(payload)


def receiving_address() -> str:
    """Основной адрес приёма (индекс PAYMENTS_RECEIVE_INDEX)."""
    return derive_tron_address(int(settings.PAYMENTS_RECEIVE_INDEX))

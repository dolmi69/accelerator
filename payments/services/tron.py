"""Тонкий клиент TronGrid: поиск входящих переводов USDT TRC-20 по адресу.

Блокчейн сам ничего не «присылает», поэтому мы спрашиваем сеть через публичный
API TronGrid. Используем только GET-эндпоинт: POST-эндпоинты TronGrid с этой
машины оказались недоступны (таймаут), а этот работает даже без API-ключа.
"""

import httpx
from django.conf import settings

TRONGRID_BASE = "https://api.trongrid.io"
USDT_DECIMALS = 6
TRON_BLOCK_SECONDS = 3
REQUEST_TIMEOUT = 20.0


class TronGridError(RuntimeError):
    """Сетевая ошибка или неожиданный ответ TronGrid."""


def _headers() -> dict:
    key = (settings.PAYMENTS_TRONGRID_API_KEY or "").strip()
    return {"TRON-PRO-API-KEY": key} if key else {}


def fetch_incoming_usdt(address: str, *, limit: int = 50) -> list[dict]:
    """Входящие переводы USDT TRC-20 на адрес (последние по времени)."""
    url = f"{TRONGRID_BASE}/v1/accounts/{address}/transactions/trc20"
    params = {
        "limit": limit,
        "only_to": "true",
        "contract_address": settings.PAYMENTS_USDT_CONTRACT,
    }
    try:
        resp = httpx.get(url, params=params, headers=_headers(), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise TronGridError(str(exc)) from exc
    return resp.json().get("data", [])

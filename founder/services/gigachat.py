"""GigaChat REST API: OAuth, TLS и потоковые ответы без Python SDK."""

import hashlib
import json
import os
import ssl
import threading
import time
import uuid
from pathlib import Path

import httpx
import truststore
from django.conf import settings


OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
# Оба адреса официальные. Основной бывает недоступен из некоторых сетей, тогда
# запрос сам уходит на запасной адрес со своей моделью (там нет GigaChat-3).
API_URL = os.getenv("GIGACHAT_API_URL", "https://api.giga.chat/v1").rstrip("/")
FALLBACK_URL = os.getenv("GIGACHAT_FALLBACK_URL", "https://gigachat.devices.sberbank.ru/api/v1").rstrip("/")
FALLBACK_MODEL = os.getenv("GIGACHAT_FALLBACK_MODEL", "GigaChat-2-Max")
# Сколько не стучаться в недоступный основной адрес, чтобы не ждать каждый раз.
PRIMARY_PAUSE_SECONDS = 300
ALLOWED_SCOPES = {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}
# Живой диалог: чуть меньше случайности, штраф за повторы фраз.
CHAT_SAMPLING = {"temperature": 0.7, "top_p": 0.9, "repetition_penalty": 1.1}
RATE_LIMIT_RETRIES = 2
UNREACHABLE = "Не удалось связаться с GigaChat. Проверьте сеть и сертификат Минцифры."
_token_lock = threading.Lock()
_token_state = {"token": "", "expires_at": 0.0, "fingerprint": ""}
_route_state = {"primary_down_until": 0.0}


class GigaChatError(Exception):
    pass


class GigaChatFormatError(GigaChatError):
    """Незавершённый ответ, который можно один раз запросить заново."""


def _tls_context():
    """Проверяем сертификат через macOS либо указанный доверенный PEM."""
    if settings.GIGACHAT_CA_BUNDLE:
        bundle = Path(settings.GIGACHAT_CA_BUNDLE).expanduser()
        if not bundle.is_file():
            raise GigaChatError("Файл GIGACHAT_CA_BUNDLE не найден.")
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=bundle)
        return context
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _client():
    context = _tls_context()
    return httpx.Client(
        verify=context,
        # Один повтор неудачного подключения; дальше выручает запасной адрес.
        # Короткий connect: пользователь не ждёт минуту, чтобы узнать о сбое сети.
        transport=httpx.HTTPTransport(verify=context, retries=1),
        timeout=httpx.Timeout(60.0, connect=6.0),
        follow_redirects=True,
    )


def _credentials():
    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if credentials.lower().startswith("basic "):
        credentials = credentials[6:].strip()
    if not credentials:
        raise GigaChatError("Добавьте GIGACHAT_CREDENTIALS в локальный файл .env.")
    if settings.GIGACHAT_SCOPE not in ALLOWED_SCOPES:
        raise GigaChatError("Некорректный GIGACHAT_SCOPE в файле .env.")
    return credentials


def _raise_for_status(response):
    if response.status_code < 400:
        return
    if response.status_code in {401, 403}:
        raise GigaChatError("GigaChat отклонил ключ или выбранный scope.")
    if response.status_code == 429:
        raise GigaChatError("Лимит запросов GigaChat исчерпан. Попробуйте позже.")
    if response.status_code == 422:
        raise GigaChatError("Запрос слишком велик для выбранной модели GigaChat.")
    raise GigaChatError(f"GigaChat вернул ошибку HTTP {response.status_code}.")


def _access_token():
    credentials = _credentials()
    fingerprint = hashlib.sha256(
        f"{credentials}:{settings.GIGACHAT_SCOPE}".encode("utf-8")
    ).hexdigest()
    with _token_lock:
        now = time.time()
        if (
            _token_state["fingerprint"] == fingerprint
            and _token_state["expires_at"] > now
        ):
            return _token_state["token"]

        try:
            with _client() as client:
                response = client.post(
                    OAUTH_URL,
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "RqUID": str(uuid.uuid4()),
                        "Accept": "application/json",
                    },
                    data={"scope": settings.GIGACHAT_SCOPE},
                )
            _raise_for_status(response)
            payload = response.json()
            token = payload["access_token"]
            expires_at = float(payload.get("expires_at", now + 1500))
            if expires_at > 1e12:  # API может вернуть миллисекунды.
                expires_at /= 1000
        except (httpx.HTTPError, ssl.SSLError) as exc:
            raise GigaChatError(UNREACHABLE) from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise GigaChatError("GigaChat вернул неверный ответ авторизации.") from exc

        _token_state.update({
            "token": token,
            "expires_at": min(expires_at - 60, now + 25 * 60),
            "fingerprint": fingerprint,
        })
        return token


def _headers(stream=False):
    return {
        "Authorization": f"Bearer {_access_token()}",
        "Accept": "text/event-stream" if stream else "application/json",
    }


def _routes():
    """Адреса и модели по порядку попыток: основной, затем запасной."""
    primary = (API_URL, settings.GIGACHAT_MODEL)
    if not FALLBACK_URL or FALLBACK_URL == API_URL:
        return [primary]
    fallback = (FALLBACK_URL, FALLBACK_MODEL)
    if time.time() < _route_state["primary_down_until"]:
        return [fallback]
    return [primary, fallback]


def active_model():
    """Модель, которая ответит на ближайший запрос (для подписи сообщений)."""
    return _routes()[0][1]


def _mark_unreachable(url):
    if url == API_URL:
        _route_state["primary_down_until"] = time.time() + PRIMARY_PAUSE_SECONDS


def _payload(system_prompt, messages, stream, max_tokens=None, model=None):
    return {
        "model": model or settings.GIGACHAT_MODEL,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "stream": stream,
        "max_tokens": max_tokens or settings.AI_MAX_OUTPUT_TOKENS,
    }


def _sse_data(lines):
    """Собрать поле data из событий Server-Sent Events."""
    current = []
    for line in lines:
        if not line:
            if current:
                yield "\n".join(current)
                current = []
        elif line.startswith("data:"):
            current.append(line[5:].lstrip())
    if current:
        yield "\n".join(current)


def _rate_limit_wait(response, attempt):
    """Пауза перед повтором после 429: личный ключ ограничивает параллельные запросы."""
    try:
        delay = float(response.headers.get("Retry-After", ""))
    except ValueError:
        delay = 1.5 * (attempt + 1)
    time.sleep(min(max(delay, 0.5), 5.0))


def _connection_failed(exc):
    """Сбой до ответа сервера: адрес недоступен, можно пробовать запасной."""
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def stream_chat(system_prompt, messages, max_tokens=None):
    """Возвращать текстовые части ответа GigaChat по мере их прихода."""
    last_error = None
    for url, model in _routes():
        payload = {**_payload(system_prompt, messages, stream=True, max_tokens=max_tokens, model=model),
                   **CHAT_SAMPLING}
        try:
            with _client() as client:
                for attempt in range(RATE_LIMIT_RETRIES + 1):
                    with client.stream("POST", f"{url}/chat/completions", headers=_headers(stream=True),
                                       json=payload) as response:
                        # Повтор безопасен: при 429 пользователь ещё не получил ни одного фрагмента.
                        if response.status_code == 429 and attempt < RATE_LIMIT_RETRIES:
                            _rate_limit_wait(response, attempt)
                            continue
                        _raise_for_status(response)
                        completed = False
                        for data in _sse_data(response.iter_lines()):
                            if data == "[DONE]":
                                completed = True
                                break
                            try:
                                event = json.loads(data)
                                delta = event["choices"][0]["delta"].get("content")
                            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                                raise GigaChatError("GigaChat вернул некорректное потоковое событие.") from exc
                            if delta:
                                yield delta
                        if not completed:
                            raise GigaChatError("Потоковый ответ GigaChat оборвался.")
                        return
        except (httpx.HTTPError, ssl.SSLError) as exc:
            # Подключение не состоялось: ни одного фрагмента ещё не отдано.
            if _connection_failed(exc):
                _mark_unreachable(url)
                last_error = exc
                continue
            raise GigaChatError(UNREACHABLE) from exc
    raise GigaChatError(UNREACHABLE) from last_error


def _post_chat(payload):
    """Обычный запрос с повтором при 429 и переходом на запасной адрес."""
    last_error = None
    for url, model in _routes():
        try:
            with _client() as client:
                for attempt in range(RATE_LIMIT_RETRIES + 1):
                    response = client.post(f"{url}/chat/completions", headers=_headers(),
                                           json={**payload, "model": model})
                    if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                        return response
                    _rate_limit_wait(response, attempt)
        except (httpx.HTTPError, ssl.SSLError) as exc:
            if _connection_failed(exc):
                _mark_unreachable(url)
                last_error = exc
                continue
            raise GigaChatError(UNREACHABLE) from exc
    raise GigaChatError(UNREACHABLE) from last_error


def complete_chat(system_prompt, content, *, json_schema=None):
    """Полный ответ, при необходимости ограниченный обязательной JSON-схемой.

    Схема GigaChat v1 задаётся в response_format.schema (не json_schema).
    https://developers.sber.ru/docs/ru/gigachat/guides/structured-output
    Запасная модель схему не соблюдает, поэтому вызывающие сервисы дублируют
    формат в промпте и разбирают ответ терпимо.
    """
    payload = _payload(
        system_prompt,
        [{"role": "user", "content": content}],
        stream=False,
        max_tokens=2400 if json_schema is not None else 1400,
    )
    if json_schema is not None:
        payload["temperature"] = 0.1
        payload["response_format"] = {
            "type": "json_schema", "schema": json_schema, "strict": True,
        }
    response = _post_chat(payload)
    try:
        _raise_for_status(response)
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") in {"length", "error"}:
            raise GigaChatFormatError("GigaChat не завершил формирование оценки.")
        if choice.get("finish_reason") == "blacklist":
            raise GigaChatError("GigaChat не смог оценить этот текст. Уточните описание сервиса.")
        answer = choice["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise GigaChatFormatError("GigaChat вернул ответ без текста.")
        return answer
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GigaChatFormatError("GigaChat вернул ответ в неверном формате.") from exc

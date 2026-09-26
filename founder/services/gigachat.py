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
CHAT_URL = "https://api.giga.chat/v1/chat/completions"
ALLOWED_SCOPES = {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}
_token_lock = threading.Lock()
_token_state = {"token": "", "expires_at": 0.0, "fingerprint": ""}


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
    return httpx.Client(
        verify=_tls_context(),
        timeout=httpx.Timeout(60.0, connect=15.0),
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
            raise GigaChatError(
                "Не удалось связаться с GigaChat. Проверьте сеть и сертификат Минцифры."
            ) from exc
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


def _payload(system_prompt, messages, stream, max_tokens=None):
    return {
        "model": settings.GIGACHAT_MODEL,
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


def stream_chat(system_prompt, messages):
    """Возвращать текстовые части ответа GigaChat по мере их прихода."""
    try:
        with _client() as client:
            with client.stream(
                "POST", CHAT_URL,
                headers=_headers(stream=True),
                json=_payload(system_prompt, messages, stream=True),
            ) as response:
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
    except (httpx.HTTPError, ssl.SSLError) as exc:
        raise GigaChatError(
            "Не удалось связаться с GigaChat. Проверьте сеть и сертификат Минцифры."
        ) from exc


def complete_chat(system_prompt, content, *, json_schema=None):
    """Полный ответ, при необходимости ограниченный обязательной JSON-схемой.

    Схема GigaChat v1 задаётся в response_format.schema (не json_schema).
    https://developers.sber.ru/docs/ru/gigachat/guides/structured-output
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
    try:
        with _client() as client:
            response = client.post(
                CHAT_URL,
                headers=_headers(),
                json=payload,
            )
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
    except (httpx.HTTPError, ssl.SSLError) as exc:
        raise GigaChatError(
            "Не удалось связаться с GigaChat. Проверьте сеть и сертификат Минцифры."
        ) from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise GigaChatFormatError("GigaChat вернул ответ в неверном формате.") from exc

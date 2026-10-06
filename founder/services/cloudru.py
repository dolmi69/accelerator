"""Модель GigaChat в Cloud.ru через совместимый Chat Completions API."""

import logging
import os

from django.conf import settings
from openai import OpenAI


BASE_URL = "https://foundation-models.api.cloud.ru/v1"
logger = logging.getLogger(__name__)


class CloudRuError(Exception):
    pass


def _client():
    key = os.getenv("CLOUDRU_API_KEY", "").strip()
    if not key:
        raise CloudRuError("Добавьте CLOUDRU_API_KEY в локальный файл .env.")
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=60.0, max_retries=1)


def _messages(system_prompt, messages):
    return [{"role": "system", "content": system_prompt}, *messages]


def stream_chat(system_prompt, messages, max_tokens=None):
    try:
        stream = _client().chat.completions.create(
            model=settings.CLOUDRU_MODEL,
            messages=_messages(system_prompt, messages),
            max_tokens=max_tokens or settings.AI_MAX_OUTPUT_TOKENS,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except CloudRuError:
        raise
    except Exception as exc:
        logger.exception("Cloud.ru streaming failed")
        raise CloudRuError("Cloud.ru не ответил. Проверьте ключ, модель и доступ к сервису.") from exc


def complete_chat(system_prompt, content):
    try:
        response = _client().chat.completions.create(
            model=settings.CLOUDRU_MODEL,
            messages=_messages(system_prompt, [{"role": "user", "content": content}]),
            max_tokens=1400,
        )
        text = response.choices[0].message.content
        if not text:
            raise CloudRuError("Cloud.ru вернул пустой ответ.")
        return text
    except CloudRuError:
        raise
    except Exception as exc:
        logger.exception("Cloud.ru completion failed")
        raise CloudRuError("Cloud.ru не подготовил отчёт. Проверьте ключ и модель.") from exc

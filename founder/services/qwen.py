"""Bounded, single-call code generation through Cloud.ru Foundation Models.

This adapter never executes model output. Callers must isolate any HTML preview
from the main application, its session cookies, and server-side execution.
"""

import logging
import os
from dataclasses import dataclass

import httpx
from django.conf import settings
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from founder.services.cloudru import BASE_URL

logger = logging.getLogger(__name__)


class QwenError(Exception):
    """A safe, actionable message; never includes the provider's raw response."""


class QwenOutputError(QwenError):
    """The request completed, but its output cannot be saved as a full result."""


@dataclass(frozen=True)
class CodeResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


def _token_count(usage, field):
    value = getattr(usage, field, None)
    return value if type(value) is int and value >= 0 else None


def generate_code(system_prompt, messages, *, max_tokens=None):
    """Make one paid request, without retries or a silent fallback to another LLM.

    Usage is supplied by Cloud.ru, not estimated from string length. A caller
    should persist it alongside a job before implementing user billing.
    """
    key = os.getenv("CLOUDRU_API_KEY", "").strip()
    if not key:
        raise QwenError("Нужен API-ключ Cloud.ru для Foundation Models: CLOUDRU_API_KEY в .env.")
    model = settings.QWEN_CODE_MODEL.strip()
    limit = settings.QWEN_CODE_MAX_TOKENS
    tokens = limit if max_tokens is None else max_tokens
    if not model or type(limit) is not int or not 1 <= limit <= 32768:
        raise QwenError("Проверьте QWEN_CODE_MODEL и QWEN_CODE_MAX_TOKENS (1–32768).")
    if type(tokens) is not int or not 1 <= tokens <= limit:
        raise QwenError("Запрошенный размер ответа превышает лимит генератора.")
    if not 1 <= settings.QWEN_CODE_TIMEOUT <= 300:
        raise QwenError("QWEN_CODE_TIMEOUT должен быть от 1 до 300 секунд.")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise QwenError("Не задана инструкция генератора.")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
        raise QwenError("Генератор принимает от 1 до 20 сообщений.")
    normalized = [{"role": "system", "content": system_prompt}]
    for message in messages:
        if (not isinstance(message, dict) or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("content"), str) or not message["content"].strip()):
            raise QwenError("Некорректное сообщение генератору.")
        normalized.append({"role": message["role"], "content": message["content"]})
    if sum(len(message["content"]) for message in normalized) > 140_000:
        raise QwenError("Слишком большой контекст генерации. Сократите описание или исходный сайт.")

    try:
        with OpenAI(
            api_key=key, base_url=BASE_URL,
            timeout=httpx.Timeout(settings.QWEN_CODE_TIMEOUT, connect=10),
            max_retries=0,
        ) as client:
            response = client.chat.completions.create(
                model=model, messages=normalized, max_tokens=tokens,
                temperature=0.3, stream=False,
            )
    except APITimeoutError:
        raise QwenError("Cloud.ru не успел ответить. Запрос мог быть оплачен; автоматического повтора нет.") from None
    except APIConnectionError:
        raise QwenError("Не удалось связаться с Cloud.ru. Проверьте подключение и попробуйте позже.") from None
    except APIStatusError as exc:
        logger.warning("Cloud.ru code generation failed: HTTP %s", exc.status_code)
        messages_by_status = {
            400: "Cloud.ru отклонил параметры генерации. Проверьте модель и лимит токенов.",
            401: "Cloud.ru отклонил API-ключ. Нужен действующий ключ для Foundation Models.",
            402: "Cloud.ru требует оплату. Проверьте баланс и подключение Foundation Models в кабинете.",
            403: "Нет доступа к модели Cloud.ru. Проверьте права ключа, проект и подключение сервиса.",
            404: "Модель не найдена в Cloud.ru. Проверьте QWEN_CODE_MODEL по каталогу кабинета.",
            429: "Cloud.ru ограничил запросы. Проверьте квоту и баланс, затем повторите позже.",
        }
        raise QwenError(messages_by_status.get(exc.status_code, "Cloud.ru временно не может выполнить генерацию.")) from None
    except (ValueError, TypeError):
        raise QwenError("Cloud.ru вернул ответ в неожиданном формате.") from None

    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise QwenOutputError("Cloud.ru вернул пустой результат генерации.")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise QwenOutputError("Код обрезан лимитом токенов. Упростите задачу или увеличьте лимит генератора.")
    if finish_reason != "stop":
        raise QwenOutputError("Модель не завершила генерацию обычным ответом. Попробуйте изменить описание.")
    text = getattr(getattr(choice, "message", None), "content", None)
    if not isinstance(text, str) or not text.strip() or len(text) > 128_000:
        raise QwenOutputError("Модель вернула пустой или слишком большой код.")
    return CodeResult(
        text=text.strip(), model=model,
        input_tokens=_token_count(getattr(response, "usage", None), "prompt_tokens"),
        output_tokens=_token_count(getattr(response, "usage", None), "completion_tokens"),
    )

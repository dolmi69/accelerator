"""Bounded, single-call code generation through the selected lab provider.

This adapter never executes model output. Callers must isolate any HTML preview
from the main application, its session cookies, and server-side execution.
"""

import logging
import math
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx
from django.conf import settings
from openai import APIConnectionError, APIStatusError, APITimeoutError, APIError, OpenAI

from founder.services.cloudru import BASE_URL
from founder.services.ai_costs import CostLimitError, reserve, settle

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
    request_id: str | None = None
    edit_method: str = "full"
    request_ids: tuple = ()
    report: dict = field(default_factory=dict)
    intent: dict | None = None
    add_modules: tuple = ()


def _token_count(usage, field):
    value = getattr(usage, field, None)
    return value if type(value) is int and value >= 0 else None


def generate_code(system_prompt, messages, *, max_tokens=None, temperature=0.3):
    """Make one paid request, without retries or a silent fallback to another LLM.

    Reserve budget before contacting the provider, then settle reported usage.
    Unknown usage remains reserved; no automatic paid retry is performed.
    """
    provider = settings.LAB_CODE_PROVIDER
    if provider not in {"cloudru", "ofox"}:
        raise QwenError("LAB_CODE_PROVIDER должен быть cloudru или ofox.")
    is_ofox = provider == "ofox"
    provider_name = "Ofox" if is_ofox else "Cloud.ru"
    key_name = "OFOX_API_KEY" if is_ofox else "CLOUDRU_API_KEY"
    key = os.getenv(key_name, "").strip()
    if not key:
        raise QwenError(f"Нужен API-ключ {provider_name}: {key_name} в .env.")
    model = (settings.OFOX_CODE_MODEL if is_ofox else settings.QWEN_CODE_MODEL).strip()
    pricing = None
    if is_ofox:
        if model != settings.OFOX_PRICE_MODEL:
            raise QwenError("Для выбранной модели настройте её тариф OFOX_PRICE_MODEL.")
        try:
            rate = Decimal(settings.OFOX_USD_RUB_RATE)
            prices = [Decimal(settings.OFOX_INPUT_USD_PER_MILLION), Decimal(settings.OFOX_OUTPUT_USD_PER_MILLION)]
            if not all(value.is_finite() and value > 0 for value in [rate, *prices]):
                raise InvalidOperation
            pricing = tuple(price * rate for price in prices)
        except (InvalidOperation, ValueError, TypeError):
            raise QwenError("Проверьте тариф Ofox и расчётный курс OFOX_USD_RUB_RATE.") from None
        if settings.OFOX_CODE_REASONING_EFFORT not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise QwenError("Некорректный OFOX_CODE_REASONING_EFFORT.")
    limit = settings.QWEN_CODE_MAX_TOKENS
    tokens = limit if max_tokens is None else max_tokens
    # Cloud.ru serves Coder-Next with a 262144-token context. The provider
    # additionally validates the combined input/output against that context.
    if not model or type(limit) is not int or not 1 <= limit <= 262144:
        raise QwenError("Проверьте QWEN_CODE_MODEL и QWEN_CODE_MAX_TOKENS (1–262144).")
    if type(tokens) is not int or not 1 <= tokens <= limit:
        raise QwenError("Запрошенный размер ответа превышает лимит генератора.")
    if is_ofox and model.startswith("openai/gpt-6") and tokens > 128000:
        raise QwenError("GPT-6 принимает не более 128000 выходных токенов за запрос.")
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 1:
        raise QwenError("Температура генератора должна быть числом от 0 до 1.")
    if not 1 <= settings.QWEN_CODE_TIMEOUT <= 1500:
        raise QwenError("QWEN_CODE_TIMEOUT должен быть от 1 до 1500 секунд.")
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
        usage_record = reserve(model, normalized, tokens, pricing=pricing)
    except CostLimitError as exc:
        raise QwenError(str(exc)) from None
    try:
        with OpenAI(
            api_key=key, base_url="https://api.ofox.ai/v1" if is_ofox else BASE_URL,
            timeout=httpx.Timeout(settings.QWEN_CODE_TIMEOUT, connect=10),
            max_retries=0,
        ) as client:
            parameters = dict(model=model, messages=normalized, stream=False)
            if is_ofox and model.startswith("openai/gpt-6"):
                parameters.update(max_completion_tokens=tokens, reasoning_effort=settings.OFOX_CODE_REASONING_EFFORT)
                if settings.OFOX_CODE_REASONING_EFFORT == "none":
                    parameters["temperature"] = temperature
            else:
                parameters.update(max_tokens=tokens, temperature=temperature)
            response = client.chat.completions.create(**parameters)
    except APITimeoutError:
        settle(usage_record)
        raise QwenError(f"{provider_name} не успел ответить. Запрос мог быть оплачен; автоматического повтора нет.") from None
    except APIConnectionError:
        settle(usage_record)
        raise QwenError(f"Не удалось связаться с {provider_name}. Проверьте подключение и попробуйте позже.") from None
    except APIStatusError as exc:
        settle(usage_record)
        logger.warning("%s code generation failed: HTTP %s", provider_name, exc.status_code)
        messages_by_status = {
            400: "Cloud.ru отклонил параметры генерации. Проверьте модель и лимит токенов.",
            401: "Cloud.ru отклонил API-ключ. Нужен действующий ключ для Foundation Models.",
            402: "Cloud.ru требует оплату. Проверьте баланс и подключение Foundation Models в кабинете.",
            403: "Нет доступа к модели Cloud.ru. Проверьте права ключа, проект и подключение сервиса.",
            404: "Модель не найдена в Cloud.ru. Проверьте QWEN_CODE_MODEL по каталогу кабинета.",
            429: "Cloud.ru ограничил запросы. Проверьте квоту и баланс, затем повторите позже.",
        }
        if is_ofox:
            messages_by_status = {
                400: "Ofox отклонил параметры генерации. Проверьте модель и лимит токенов.",
                401: "Ofox отклонил API-ключ. Проверьте OFOX_API_KEY.",
                402: "Недостаточно средств на балансе Ofox.",
                403: "Нет доступа к модели Ofox. Проверьте права ключа.",
                404: "Модель не найдена в Ofox. Проверьте OFOX_CODE_MODEL.",
                429: "Ofox ограничил запросы. Немного подождите и повторите позже.",
            }
        raise QwenError(messages_by_status.get(exc.status_code, f"{provider_name} временно не может выполнить генерацию.")) from None
    except (APIError, ValueError, TypeError):
        settle(usage_record)
        raise QwenError(f"{provider_name} вернул ответ в неожиданном формате.") from None

    input_tokens = _token_count(getattr(response, "usage", None), "prompt_tokens")
    output_tokens = _token_count(getattr(response, "usage", None), "completion_tokens")
    # Even truncated or invalid output can incur a charge.
    settle(usage_record, input_tokens=input_tokens, output_tokens=output_tokens, status="output_error")
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise QwenOutputError(f"{provider_name} вернул пустой результат генерации.")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise QwenOutputError("Код обрезан лимитом токенов. Упростите задачу или увеличьте лимит генератора.")
    if finish_reason != "stop":
        raise QwenOutputError("Модель не завершила генерацию обычным ответом. Попробуйте изменить описание.")
    text = getattr(getattr(choice, "message", None), "content", None)
    if not isinstance(text, str) or not text.strip() or len(text) > 128_000:
        raise QwenOutputError("Модель вернула пустой или слишком большой код.")
    from founder.models import LabAIUsage
    LabAIUsage.objects.filter(pk=usage_record.pk, status="output_error").update(status="success")
    return CodeResult(
        text=text.strip(), model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        request_id=str(usage_record.pk),
    )

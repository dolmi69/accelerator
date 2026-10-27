"""Bounded, single-call code generation through the selected lab provider.

This adapter never executes model output. Callers must isolate any HTML preview
from the main application, its session cookies, and server-side execution.
"""

import logging
import json
import hashlib
import math
import os
import time
from types import SimpleNamespace
import re
from pathlib import Path
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
    backend_files: dict = field(default_factory=dict)


class StreamInterrupted(QwenError):
    """No authoritative terminal event: partial code must never be applied."""


def _output_char_limit():
    return 2_000_000 if settings.LAB_UNRESTRICTED_TEST_MODE else 128_000


def _consume_responses_stream(stream, stats):
    chunks, size, final = [], 0, None
    started = time.monotonic()
    try:
        with stream:
            for event in stream:
                elapsed = time.monotonic() - started
                stats['events'] += 1
                stats.setdefault('first_event_seconds', round(elapsed, 3))
                if elapsed > settings.QWEN_CODE_TIMEOUT:
                    raise StreamInterrupted('Истекло время потоковой генерации. Незавершённый код не применён; автоматического повтора нет.')
                kind = getattr(event, 'type', None)
                if kind == 'response.output_text.delta':
                    delta = getattr(event, 'delta', None)
                    if not isinstance(delta, str):
                        raise StreamInterrupted('Поток модели содержит некорректный фрагмент. Код не применён.')
                    size += len(delta)
                    if size > _output_char_limit():
                        raise StreamInterrupted('Поток модели превысил допустимый размер кода. Код не применён.')
                    stats.setdefault('first_text_seconds', round(elapsed, 3))
                    chunks.append(delta)
                elif kind in {'response.completed', 'response.incomplete', 'response.failed'}:
                    final = getattr(event, 'response', None)
                    break
                elif kind == 'error':
                    raise StreamInterrupted('Провайдер остановил поток ответа. Код не применён; автоматического повтора нет.')
    finally:
        stats['elapsed_seconds'] = round(time.monotonic() - started, 3)
    if final is None:
        raise StreamInterrupted('Поток ответа оборвался до подтверждения завершения. Код не применён; автоматического повтора нет.')
    # Some compatible gateways put the text only in deltas, but the terminal
    # response is still required for completion status and authoritative usage.
    text = getattr(final, 'output_text', None) or ''.join(chunks)
    return SimpleNamespace(output_text=text, status=getattr(final, 'status', None),
        usage=getattr(final, 'usage', None), output=getattr(final, 'output', None),
        incomplete_details=getattr(final, 'incomplete_details', None), error=getattr(final, 'error', None),
        id=getattr(final, 'id', None), _request_id=getattr(final, '_request_id', None))


def _token_count(usage, field):
    value = getattr(usage, field, None)
    return value if type(value) is int and value >= 0 else None


def _safe_identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}', value) else None


def _filter_metadata(response, choice):
    """Only documented filter categories and scalar decisions; never raw text."""
    categories = {'hate', 'sexual', 'violence', 'self_harm', 'jailbreak',
                  'protected_material_code', 'protected_material_text', 'profanity', 'pii'}
    def summarize(value):
        if not isinstance(value, dict):
            return {}
        result = {}
        for name in categories & value.keys():
            item = value[name]
            if not isinstance(item, dict):
                continue
            fields = {key: item[key] for key in ('filtered', 'detected') if type(item.get(key)) is bool}
            if item.get('severity') in ('safe', 'low', 'medium', 'high'):
                fields['severity'] = item['severity']
            if fields:
                result[name] = fields
        return result
    prompts = getattr(response, 'prompt_filter_results', None)
    return {'output': summarize(getattr(choice, 'content_filter_results', None)),
            'input': [summarize(item.get('content_filter_results')) for item in prompts[:10]
                      if isinstance(item, dict)] if isinstance(prompts, list) else []}


def generate_code(system_prompt, messages, *, max_tokens=None, temperature=0.3, reasoning_effort=None):
    """Make one paid request, without retries or a silent fallback to another LLM.

    Reserve budget before contacting the provider, then settle reported usage.
    Unknown usage remains reserved; no automatic paid retry is performed.
    """
    provider = settings.LAB_CODE_PROVIDER
    if provider not in {"cloudru", "ofox"}:
        raise QwenError("LAB_CODE_PROVIDER должен быть cloudru или ofox.")
    is_ofox = provider == "ofox"
    protocol = settings.OFOX_CODE_API if is_ofox else 'chat_completions'
    if protocol not in {'chat_completions', 'responses'}:
        raise QwenError('OFOX_CODE_API должен быть chat_completions или responses.')
    responses_api = protocol == 'responses'
    streaming = responses_api and is_ofox and settings.OFOX_CODE_STREAM
    stream_stats = {'enabled': streaming, 'events': 0}
    provider_name = "Ofox" if is_ofox else "Cloud.ru"
    key_name = "OFOX_API_KEY" if is_ofox else "CLOUDRU_API_KEY"
    key = os.getenv(key_name, "").strip()
    if not key:
        raise QwenError(f"Нужен API-ключ {provider_name}: {key_name} в .env.")
    from founder.services.lab_models import active_model, override_model, prices as routed_prices
    model = active_model().strip()
    upstream = settings.OFOX_PROVIDER_TYPE if is_ofox else ''
    if upstream and upstream not in {'openai', 'azure_foundry', 'anthropic', 'aws_bedrock', 'gemini', 'vertex'}:
        raise QwenError('Проверьте OFOX_PROVIDER_TYPE по каталогу провайдеров Ofox.')
    pricing = None
    if is_ofox:
        routed = override_model() is not None
        if not routed and model != settings.OFOX_PRICE_MODEL:
            raise QwenError("Для выбранной модели настройте её тариф OFOX_PRICE_MODEL.")
        try:
            rate = Decimal(settings.OFOX_USD_RUB_RATE)
            prices = [Decimal(value) for value in (routed_prices(model) if routed else
                      (settings.OFOX_INPUT_USD_PER_MILLION, settings.OFOX_OUTPUT_USD_PER_MILLION))]
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
            if upstream:
                # Ofox documents this header for every text protocol. The SDK's
                # extra_body argument has different merge semantics; use header.
                parameters['extra_headers'] = {'X-OfoxAI-Provider-Type': upstream}
            if is_ofox and model.startswith("openai/gpt-6"):
                effort = reasoning_effort or settings.OFOX_CODE_REASONING_EFFORT
                if effort not in {'none', 'low', 'medium', 'high', 'xhigh', 'max'}:
                    raise QwenError('Некорректный уровень рассуждения модели.')
                parameters.update(max_completion_tokens=tokens, reasoning_effort=effort)
                if effort == "none":
                    parameters["temperature"] = temperature
            else:
                parameters.update(max_tokens=tokens, temperature=temperature)
            if responses_api:
                inputs = []
                for message in normalized[1:]:
                    item = {'type': 'message', 'role': message['role'],
                            'content': [{'type': 'output_text' if message['role'] == 'assistant' else 'input_text', 'text': message['content']}]}
                    if message['role'] == 'assistant':
                        item.update(id='msg_' + hashlib.sha256(message['content'].encode()).hexdigest()[:32], status='completed')
                        item['content'][0]['annotations'] = []
                    inputs.append(item)
                response_parameters = dict(model=model, instructions=system_prompt, input=inputs,
                                           max_output_tokens=tokens, store=False, stream=streaming)
                if 'reasoning_effort' in parameters:
                    response_parameters['reasoning'] = {'effort': parameters['reasoning_effort']}
                if 'temperature' in parameters:
                    response_parameters['temperature'] = parameters['temperature']
                if 'extra_headers' in parameters:
                    response_parameters['extra_headers'] = parameters['extra_headers']
                response = client.responses.create(**response_parameters)
                if streaming:
                    response = _consume_responses_stream(response, stream_stats)
            else:
                response = client.chat.completions.create(**parameters)
    except StreamInterrupted:
        settle(usage_record)
        raise
    except APITimeoutError:
        settle(usage_record)
        raise QwenError(f"{provider_name} не успел ответить. Запрос мог быть оплачен; автоматического повтора нет.") from None
    except APIConnectionError:
        settle(usage_record)
        raise QwenError(f"Не удалось связаться с {provider_name}. Проверьте подключение и попробуйте позже.") from None
    except APIStatusError as exc:
        settle(usage_record)
        logger.warning("%s code generation failed: HTTP %s", provider_name, exc.status_code)
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get('error', body)
        error = error if isinstance(error, dict) else {}
        allowed_codes = {'provider_type_unavailable', 'invalid_provider_type', 'moderation_blocked',
                         'content_filter', 'invalid_request_error', 'context_length_exceeded',
                         'insufficient_quota', 'rate_limit_exceeded', 'model_not_found'}
        codes = [error.get('code'), error.get('type')]
        code = next((value for value in codes if isinstance(value, str) and value in allowed_codes), 'other')
        metadata = {'provider': provider, 'protocol': protocol, 'upstream': upstream or 'auto', 'model': model,
                    'request_id': str(usage_record.pk), 'provider_request_id': _safe_identifier(getattr(exc, 'request_id', None)),
                    'http_status': exc.status_code, 'error_code': code, 'stream': stream_stats}
        try:
            folder = Path(settings.BASE_DIR) / '.runtime' / 'lab-ai-diagnostics'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f'{usage_record.pk}.json').write_text(json.dumps(metadata), encoding='utf-8')
        except OSError:
            pass
        if is_ofox and code in {'provider_type_unavailable', 'invalid_provider_type'}:
            raise QwenError('Ofox не предоставляет выбранный канал для этой модели. Проверьте OFOX_PROVIDER_TYPE; автоматической смены модели нет.') from None
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
                502: "Ofox не получил корректный ответ от сервиса модели (HTTP 502). Сайт не изменён; автоматического повтора нет.",
                503: "Сервис генерации Ofox сейчас недоступен (HTTP 503). Сайт не изменён; попробуйте позже.",
                504: "Ofox прервал ожидание ответа модели (HTTP 504). Сайт не изменён; автоматического повтора нет.",
            }
        raise QwenError(messages_by_status.get(exc.status_code, f"{provider_name} временно не может выполнить генерацию.")) from None
    except APIError:
        settle(usage_record)
        raise QwenError(f"Поток {provider_name} оборвался. Код не применён; автоматического повтора нет." if streaming else f"{provider_name} вернул ответ в неожиданном формате.") from None
    except (ValueError, TypeError):
        settle(usage_record)
        raise QwenError(f"{provider_name} вернул ответ в неожиданном формате.") from None

    input_tokens = _token_count(getattr(response, "usage", None), "input_tokens" if responses_api else "prompt_tokens")
    output_tokens = _token_count(getattr(response, "usage", None), "output_tokens" if responses_api else "completion_tokens")
    # Even truncated or invalid output can incur a charge.
    settle(usage_record, input_tokens=input_tokens, output_tokens=output_tokens, status="output_error")
    choices = getattr(response, "choices", None)
    # Keep only structural metadata, never prompts, generated code or credentials.
    choice = choices[0] if isinstance(choices, list) and choices else None
    finish_reason = getattr(choice, "finish_reason", None)
    text = getattr(getattr(choice, "message", None), "content", None)
    refusal = bool(getattr(getattr(choice, 'message', None), 'refusal', None))
    status = reason = error_code = None
    if responses_api:
        text = getattr(response, 'output_text', None)
        status = getattr(response, 'status', None)
        reason = getattr(getattr(response, 'incomplete_details', None), 'reason', None)
        error_code = getattr(getattr(response, 'error', None), 'code', None)
        refusal = any(getattr(part, 'type', None) == 'refusal' for item in (getattr(response, 'output', None) or [])
                      for part in (getattr(item, 'content', None) or []))
        finish_reason = ('content_filter' if reason == 'content_filter' or error_code == 'content_filter'
                         else 'refusal' if refusal
                         else 'stop' if status == 'completed'
                         else 'length' if reason == 'max_output_tokens' else 'error')
    elif refusal and finish_reason != 'content_filter':
        finish_reason = 'refusal'
    diagnostic = {'provider': provider, 'protocol': protocol, 'upstream': upstream or 'auto', 'model': model, 'request_id': str(usage_record.pk),
                  'choices': len(choices) if isinstance(choices, list) else 0,
                  'provider_request_id': _safe_identifier(getattr(response, '_request_id', None)),
                  'provider_response_id': _safe_identifier(getattr(response, 'id', None)),
                  'response_status': status if status in (None, 'completed', 'incomplete', 'failed', 'queued', 'in_progress', 'cancelled') else 'other',
                  'incomplete_reason': reason if reason in (None, 'content_filter', 'max_output_tokens', 'max_messages', 'steered') else 'other',
                  'provider_error_code': _safe_identifier(error_code), 'refusal': refusal,
                  'finish_reason': finish_reason if finish_reason in (None, 'stop', 'length', 'content_filter', 'refusal', 'tool_calls', 'function_call', 'error') else 'other',
                  'content_chars': len(text) if isinstance(text, str) else 0,
                  'input_tokens': input_tokens, 'output_tokens': output_tokens,
                  'filters': _filter_metadata(response, choice), 'stream': stream_stats}
    try:
        folder = Path(settings.BASE_DIR) / '.runtime' / 'lab-ai-diagnostics'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{usage_record.pk}.json').write_text(json.dumps(diagnostic), encoding='utf-8')
    except OSError:
        logger.warning('Could not save AI response metadata: %s', usage_record.pk)
    if not responses_api and (not isinstance(choices, list) or not choices):
        raise QwenOutputError(f"{provider_name} вернул пустой результат генерации.")
    if finish_reason == "length":
        raise QwenOutputError("Код обрезан лимитом токенов. Упростите задачу или увеличьте лимит генератора.")
    if finish_reason == 'content_filter':
        flagged = [category for category, decision in diagnostic['filters']['output'].items() if decision.get('filtered')]
        detail = ', '.join(flagged) if flagged else 'категория не передана'
        raise QwenOutputError(f'Фильтр провайдера {provider_name} остановил ответ ({detail}). Сайт не изменён. Автоматического повтора нет.')
    if finish_reason == 'refusal':
        raise QwenOutputError(f'Модель {provider_name} отказалась выполнить запрос. Сайт не изменён. Автоматического повтора нет.')
    if finish_reason is None and not text:
        raise QwenOutputError(f"{provider_name} вернул пустой ответ без статуса завершения. Это ошибка ответа API, а не дизайна. Автоматического повтора нет.")
    # Some compatible gateways omit finish_reason. Accept their text only for
    # downstream full-document/JSON validation; explicit errors stay rejected.
    if finish_reason not in ("stop", None):
        raise QwenOutputError(f"{provider_name} не завершил генерацию: {diagnostic['finish_reason']}. Код не применён; автоматического повтора нет.")
    if not isinstance(text, str) or not text.strip() or len(text) > _output_char_limit():
        raise QwenOutputError("Модель вернула пустой или слишком большой код.")
    from founder.models import LabAIUsage
    LabAIUsage.objects.filter(pk=usage_record.pk, status="output_error").update(status="success")
    return CodeResult(
        text=text.strip(), model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        request_id=str(usage_record.pk),
    )

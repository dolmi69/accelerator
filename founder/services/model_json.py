"""Разбор JSON, который вернула модель.

GigaChat-3 соблюдает JSON-схему на уровне API, а запасная GigaChat-2-Max её
игнорирует: оборачивает ответ в ```json, переименовывает поля, вкладывает
списки в словари. Здесь только снимаем обёртку; поля нормализует каждый сервис.
"""

import json


def load_model_json(raw):
    """JSON-объект или список из ответа модели; ValueError, если его там нет."""
    if not isinstance(raw, str):
        raise ValueError("Ответ модели не текст")
    cleaned = raw.strip().lstrip("﻿")
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Модель могла добавить фразу до или после JSON.
        starts = [index for index in (cleaned.find("{"), cleaned.find("[")) if index != -1]
        if not starts:
            raise
        start = min(starts)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end <= start:
            raise
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return _consecutive_objects(cleaned[start:end + 1])


def _consecutive_objects(text):
    """Несколько объектов подряд («{...}\\n{...}»): GigaChat иногда делит схему на части."""
    decoder, parts, index = json.JSONDecoder(), [], 0
    while index < len(text):
        if text[index] in " \t\r\n,":
            index += 1
            continue
        value, index = decoder.raw_decode(text, index)
        parts.append(value)
    if len(parts) > 1 and all(isinstance(part, dict) for part in parts):
        merged = {}
        for part in parts:
            merged.update(part)
        return merged
    return parts[0]


def first_text(item, *keys):
    """Первое непустое текстовое поле из списка синонимов."""
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
        if isinstance(value, list) and value and all(isinstance(part, str) for part in value):
            return " ".join(" ".join(part.split()) for part in value if part.strip())
    return ""

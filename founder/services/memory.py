"""Поиск прежних слов основателя по основам слов; поле embedding_ref готово для векторов."""

import re

from django.db.models import Q

from founder.models import ChatSession, StartupMemory


WORD_RE = re.compile(r"[\w-]{3,}", re.UNICODE)
# Служебные слова вопроса не помогают найти прежний рассказ.
STOP_WORDS = frozenset(
    "что как какой какая какую какие каким это эти этот эта для или его она они оно ещё уже "
    "так вот там тут где когда сейчас теперь тоже надо нужно можно очень просто давай напомни "
    "называл называла говорил говорила рассказывал рассказывала был была были есть если мне "
    "тебе тебя меня мой моя мои наш наша наши ваш ваша про при над под без чем чего кто ним ней "
    "них всё все всех сколько почему зачем делать сделать начале раньше".split()
)


def stem(word):
    """Грубая основа русского слова: «конверсию» и «конверсия» дают «конве».

    Хватает для поиска по памяти без морфологического словаря: длинные слова
    режем до пяти букв, короткие теряют окончание.
    """
    word = word.lower()
    if len(word) >= 6:
        return word[:5]
    if len(word) >= 4:
        return word[:-1]
    return word


def query_stems(text, limit=10):
    words = (word.lower() for word in WORD_RE.findall(text or ""))
    return list(dict.fromkeys(stem(word) for word in words if word not in STOP_WORDS))[:limit]


def remember_user_message(message):
    """Сохранить исходный текст без выдачи непроверенных слов за факты."""
    if message.session.mode == ChatSession.Mode.PITCH:
        return None
    attachment_text = "\n".join(
        attachment.extracted_text[:4000]
        for attachment in message.attachments.all()
    )
    content = "\n".join(part for part in (message.content, attachment_text) if part).strip()
    if not content:
        return None
    memory = StartupMemory(
        startup=message.session.startup,
        source_message=message,
        kind=StartupMemory.Kind.NOTE,
        content=content[:6000],
    )
    memory.full_clean()
    memory.save()
    return memory


def relevant_memories(startup, query, exclude_message_ids=(), limit=6):
    """Ищем прежние слова основателя во всех сессиях одного стартапа."""
    terms = query_stems(query)
    queryset = StartupMemory.objects.filter(startup=startup, is_active=True, source_message__session__mode=ChatSession.Mode.COFOUNDER).exclude(
        source_message_id__in=exclude_message_ids
    )
    if not terms:
        return list(queryset.order_by("-created_at")[:limit])

    condition = Q()
    for term in terms:
        # SQLite сравнивает кириллицу без учёта регистра только для ASCII,
        # поэтому ищем и слово с заглавной буквы в начале предложения.
        condition |= Q(content__icontains=term) | Q(content__icontains=term.capitalize())
    candidates = list(queryset.filter(condition).order_by("-created_at")[:80])
    terms_set = set(terms)
    candidates.sort(
        key=lambda item: (
            len(terms_set.intersection(stem(word) for word in WORD_RE.findall(item.content))),
            item.created_at,
        ),
        reverse=True,
    )
    return candidates[:limit]


def conversation_context(session, latest_message):
    """Объединить недавний диалог и подходящие старые заметки."""
    recent = list(
        session.messages.prefetch_related("attachments").order_by("-created_at", "-id")[:18]
    )
    recent.reverse()
    recent_ids = {message.id for message in recent}
    query = latest_message.content + " " + " ".join(
        attachment.extracted_text[:2000]
        for attachment in latest_message.attachments.all()
    )
    memories = relevant_memories(session.startup, query, recent_ids)

    messages = []
    for message in recent:
        parts = [message.content.strip()]
        if message.role == message.Role.USER:
            parts.extend(
                f"Файл {attachment.original_name}:\n{attachment.extracted_text[:6000]}"
                for attachment in message.attachments.all()
                if attachment.extracted_text
            )
        content = "\n\n".join(part for part in parts if part)
        if content:
            messages.append({"role": message.role, "content": content[:9000]})
    return messages, memories

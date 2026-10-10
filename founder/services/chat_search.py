"""Telegram-style search inside one conversation (chat with Bruno or a direct thread).

Matching runs in Python rather than SQL: SQLite's LIKE ignores case only for
ASCII, so "Привет" would not find "привет". One conversation is small enough
to scan; only id/content columns are read, in chunks, newest first.
"""
from django.db import OperationalError
from django.http import JsonResponse

from founder.services.request_limits import RequestLimitExceeded, consume_limit

MAX_QUERY_LENGTH = 100
MAX_RESULTS = 200
SNIPPET_BEFORE = 40
SNIPPET_AFTER = 80
SEARCHES_PER_MINUTE = 60


def fold(text):
    """Case-insensitive form that keeps every character at its position.

    Position-preserving folding lets the browser highlight exactly the same
    matches (static/founder/js/chat-search.js uses the identical rule).
    """
    folded = []
    for char in text:
        lower = char.lower()
        char = lower if len(lower) == 1 else char
        folded.append('е' if char == 'ё' else char)
    return ''.join(folded)


def normalize_query(raw):
    if not isinstance(raw, str):
        return ''
    return ' '.join(raw.split())[:MAX_QUERY_LENGTH]


def snippet(content, start, length):
    """A one-line excerpt around the first match, like a search results list."""
    left = max(0, start - SNIPPET_BEFORE)
    right = min(len(content), start + length + SNIPPET_AFTER)
    if left:
        # Start at a word boundary when one is close, so the excerpt reads well.
        space = content.rfind(' ', left, start)
        left = space + 1 if space != -1 else left
    text = ' '.join(content[left:right].split())
    return f"{'…' if left else ''}{text}{'…' if right < len(content) else ''}"


def find_matches(rows, query, limit=MAX_RESULTS):
    """rows: iterable of (id, content, *extra), newest first. Returns (matches, truncated)."""
    needle = fold(query)
    matches = []
    for row in rows:
        content = row[1] or ''
        position = fold(content).find(needle)
        if position == -1:
            continue
        if len(matches) == limit:
            return matches, True
        matches.append((row, snippet(content, position, len(needle))))
    return matches, False


def search_response(request, rows, describe):
    """JSON for the chat search panel. describe(row) -> dict with id/author/own/created_at."""
    query = normalize_query(request.GET.get('q'))
    if not query:
        return JsonResponse({'query': '', 'results': [], 'truncated': False})
    try:
        consume_limit(f'chat-search:{request.user.pk}', SEARCHES_PER_MINUTE, 60)
    except RequestLimitExceeded as exc:
        response = JsonResponse({'error': 'Слишком много запросов поиска. Подождите немного.'}, status=429)
        response['Retry-After'] = str(exc.retry_after)
        return response
    except OperationalError:
        response = JsonResponse({'error': 'Сервис занят. Повторите поиск через несколько секунд.'}, status=503)
        response['Retry-After'] = '5'
        return response
    matches, truncated = find_matches(rows, query)
    results = [{**describe(row), 'snippet': excerpt} for row, excerpt in matches]
    response = JsonResponse({'query': query, 'results': results, 'truncated': truncated})
    response['Cache-Control'] = 'no-store'
    return response

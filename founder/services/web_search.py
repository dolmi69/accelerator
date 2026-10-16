"""Поиск в открытых источниках для анализа рынка (по мотивам скилла agent-reach).

Exa — основной поиск: публичный MCP mcp.exa.ai отвечает без ключа, с EXA_API_KEY
запрос идёт в api.exa.ai. DuckDuckGo (HTML-версия) — запасной. Несколько запросов
выполняются параллельно, потом результаты сводятся и чистятся. В поиск уходят только
короткие запросы о нише, без имени основателя. Текст страниц — данные, а не инструкции:
модель получает его с пометкой «источник».
"""

import html
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from django.conf import settings


logger = logging.getLogger(__name__)

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
EXA_API_URL = "https://api.exa.ai/search"
DDG_URL = "https://html.duckduckgo.com/html/"
TIMEOUT = httpx.Timeout(15.0, connect=6.0)
USER_AGENT = "Mozilla/5.0 (compatible; BrunoLab/1.0; +market-research)"
TITLE_LIMIT = 200
SNIPPET_LIMIT = 900
# С одного сайта не больше двух страниц: иначе один агрегатор заслонит рынок.
PER_DOMAIN = 2


class SearchError(Exception):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published: str = ""

    @property
    def domain(self):
        host = urlparse(self.url).hostname or ""
        return host.removeprefix("www.")

    def as_dict(self):
        return {**asdict(self), "domain": self.domain}


CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏ -‮﻿]")
TAG_RE = re.compile(r"<[^>]+>")


def _clean(text, limit):
    text = CONTROL_RE.sub(" ", html.unescape(TAG_RE.sub(" ", str(text or ""))))
    return " ".join(text.split())[:limit]


def _safe_url(url):
    """Только http(s): ссылка попадёт в шаблон, javascript: и прочее не пропускаем."""
    url = str(url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    return url[:500]


def _result(title, url, snippet, published=""):
    url = _safe_url(url)
    title = _clean(title, TITLE_LIMIT)
    if not url or not title:
        return None
    return SearchResult(title=title, url=url, snippet=_clean(snippet, SNIPPET_LIMIT),
                        published=_clean(published, 10) if published and published != "N/A" else "")


EXA_BLOCK_RE = re.compile(r"(?m)^Title:\s*(?P<title>.*)\nURL:\s*(?P<url>\S+)\s*\n(?P<rest>[\s\S]*?)(?=^Title:|\Z)")


def parse_exa_mcp(body):
    """Результаты из ответа MCP Exa: SSE «data: {...}» с текстом «Title: … URL: … Highlights: …»."""
    payloads = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payloads.append(line[5:].strip())
    if not payloads and body.strip().startswith("{"):
        payloads.append(body.strip())
    results = []
    for payload in payloads:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if data.get("error") or (data.get("result") or {}).get("isError"):
            raise SearchError("Exa вернул ошибку")
        for part in (data.get("result") or {}).get("content") or []:
            text = part.get("text") if isinstance(part, dict) else ""
            for match in EXA_BLOCK_RE.finditer(text or ""):
                rest = match.group("rest")
                published = re.search(r"^Published:\s*(\S+)", rest, re.MULTILINE)
                snippet = rest.split("Highlights:", 1)[-1] if "Highlights:" in rest else rest
                snippet = re.sub(r"(?m)^(?:Published|Author):.*$", "", snippet).replace("\n---", " ")
                item = _result(match.group("title"), match.group("url"), snippet,
                               published.group(1) if published else "")
                if item:
                    results.append(item)
    return results


MARKET_OBJECTIVE = "Найти компании и сервисы на рынке России, их цены и отзывы клиентов."


def _exa_mcp(query, limit, objective=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "web_search_exa",
        "arguments": {"query": query, "numResults": limit, "objective": objective or MARKET_OBJECTIVE},
    }}
    response = httpx.post(EXA_MCP_URL, json=body, timeout=TIMEOUT,
                          headers={"Accept": "application/json, text/event-stream", "User-Agent": USER_AGENT})
    response.raise_for_status()
    return parse_exa_mcp(response.text)


def _exa_api(query, limit, objective=None):
    response = httpx.post(EXA_API_URL, timeout=TIMEOUT, headers={"x-api-key": os.getenv("EXA_API_KEY", "")},
                          json={"query": query, "numResults": limit, "contents": {"highlights": {"numSentences": 4}}})
    response.raise_for_status()
    results = []
    for item in response.json().get("results") or []:
        snippet = " ".join(item.get("highlights") or []) or item.get("text") or item.get("summary") or ""
        found = _result(item.get("title"), item.get("url"), snippet, (item.get("publishedDate") or "")[:10])
        if found:
            results.append(found)
    return results


DDG_TITLE_RE = re.compile(r'class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>[\s\S]*?)</a>', re.IGNORECASE)
DDG_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(?P<snippet>[\s\S]*?)</(?:a|div)>', re.IGNORECASE)


def _ddg_url(href):
    """Ссылка DuckDuckGo вида //duckduckgo.com/l/?uddg=<адрес> ведёт на настоящий сайт."""
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg")
        return unquote(target[0]) if target else ""
    return href


def parse_duckduckgo(body):
    results = []
    # Рекламные блоки помечены result--ad: их пропускаем.
    for block in re.split(r'<div class="result ', body)[1:]:
        if "result--ad" in block[:200]:
            continue
        match = DDG_TITLE_RE.search(block)
        if not match:
            continue
        snippet = DDG_SNIPPET_RE.search(block)
        item = _result(match.group("title"), _ddg_url(match.group("url")), snippet.group("snippet") if snippet else "")
        if item:
            results.append(item)
    return results


def _duckduckgo(query, limit, objective=None):
    response = httpx.post(DDG_URL, data={"q": query, "kl": "ru-ru"}, timeout=TIMEOUT,
                          headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return parse_duckduckgo(response.text)[:limit]


def _providers():
    mode = getattr(settings, "MARKET_SEARCH", "off")
    if mode == "off":
        return []
    exa = _exa_api if os.getenv("EXA_API_KEY") else _exa_mcp
    return {"exa": [exa, _duckduckgo], "duckduckgo": [_duckduckgo]}.get(mode, [exa, _duckduckgo])


def enabled():
    return bool(_providers())


def search(query, limit=5, objective=None):
    """Результаты одного запроса: первый поиск, который ответил непусто.

    objective — что ищем, своими словами: Exa по нему отбирает страницы, остальные поиски его не знают.
    """
    query = _clean(query, 200)
    providers = _providers()
    if not providers:
        raise SearchError("Поиск в интернете выключен.")
    if not query:
        return []
    for provider in providers:
        try:
            results = provider(query, limit, objective) if objective else provider(query, limit)
        except (httpx.HTTPError, SearchError, ValueError) as exc:
            logger.warning("Web search failed: provider=%s error=%s", getattr(provider, "__name__", "search"),
                           type(exc).__name__)
            continue
        if results:
            return results[:limit]
    return []


def _interleave(batches, limit):
    """Результаты по очереди из каждой пачки, без повторов адреса."""
    seen, merged = set(), []
    for rank in range(limit):
        for batch in batches:
            if rank < len(batch):
                key = batch[rank].url.split("#")[0].rstrip("/")
                if key not in seen:
                    seen.add(key)
                    merged.append(batch[rank])
    return merged


def search_wide(query, limit=5, objective=None):
    """Один запрос сразу во все поиски параллельно (правило agent-reach: собирать из нескольких мест).

    Exa лучше находит стартапы и статьи, DuckDuckGo с регионом ru-ru — российские сайты и форумы.
    Упавший поиск не мешает остальным.
    """
    query = _clean(query, 200)
    providers = _providers()
    if not providers:
        raise SearchError("Поиск в интернете выключен.")
    if not query:
        return []

    def run(provider):
        try:
            return provider(query, limit, objective) if objective else provider(query, limit)
        except (httpx.HTTPError, SearchError, ValueError) as exc:
            logger.warning("Web search failed: provider=%s error=%s", getattr(provider, "__name__", "search"),
                           type(exc).__name__)
            return []

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        batches = list(pool.map(run, providers))
    return _interleave(batches, limit)[:limit * 2]


def search_many(queries, limit=5, total=12, wide=False):
    """Несколько запросов параллельно; результаты по очереди из каждого, без повторов.

    wide=True — каждый запрос сразу во все поиски (search_wide), так находится больше российских сайтов.
    """
    queries = [query for query in dict.fromkeys(_clean(q, 200) for q in queries) if query][:5]
    if not queries:
        return []
    if not enabled():
        raise SearchError("Поиск в интернете выключен.")
    find = search_wide if wide else search
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        batches = list(pool.map(lambda query: find(query, limit), queries))
    limit = max((len(batch) for batch in batches), default=0)
    seen, per_domain, merged = set(), {}, []
    for rank in range(limit):
        for batch in batches:
            if rank >= len(batch):
                continue
            item = batch[rank]
            key = item.url.split("#")[0].rstrip("/")
            if key in seen or per_domain.get(item.domain, 0) >= PER_DOMAIN:
                continue
            seen.add(key)
            per_domain[item.domain] = per_domain.get(item.domain, 0) + 1
            merged.append(item)
    if not merged and all(not batch for batch in batches):
        logger.info("Web search returned nothing for %d queries", len(queries))
    return merged[:total]


# --- Чтение страницы целиком (agent-reach: Jina Reader) ---------------------------------------------------------

JINA_URL = "https://r.jina.ai/"
PAGE_LIMIT = 2500
PAGE_BYTES = 1_500_000
PAGE_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
PAGE_REDIRECTS = 3
DROP_BLOCKS_RE = re.compile(r"<(script|style|noscript|svg|template|iframe)\b[\s\S]*?</\1>", re.IGNORECASE)
HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
META_DESCRIPTION_RE = re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', re.IGNORECASE)


def _public_url(url):
    """Адрес ведёт в интернет, а не на сервер и не в локальную сеть: иначе чтение страницы стало бы SSRF."""
    import ipaddress
    import socket

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.port not in (None, 80, 443):
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(address.split("%")[0]).is_global for address in addresses)


def html_text(body, limit=PAGE_LIMIT):
    """Видимый текст страницы: описание и текст без скриптов, стилей и разметки."""
    description = META_DESCRIPTION_RE.search(body)
    body = DROP_BLOCKS_RE.sub(" ", HTML_COMMENT_RE.sub(" ", body))
    text = _clean(body, limit * 3)
    return _clean(((description.group(1) + ". ") if description else "") + text, limit)


def _read_direct(url):
    """Сами скачиваем страницу: перенаправления проверяем вручную, размер ограничен."""
    for _ in range(PAGE_REDIRECTS + 1):
        if not _public_url(url):
            return ""
        with httpx.stream("GET", url, timeout=PAGE_TIMEOUT, follow_redirects=False,
                          headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as response:
            if response.is_redirect:
                url = _safe_url(str(response.next_request.url)) if response.next_request else ""
                if not url:
                    return ""
                continue
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", ""):
                return ""
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > PAGE_BYTES:
                    break
            encoding = response.encoding or "utf-8"
        return html_text(b"".join(chunks).decode(encoding, errors="replace"))
    return ""


def _read_jina(url):
    """Jina Reader отдаёт текст страницы без разметки. Без ключа с некоторых сетей отвечает 401."""
    response = httpx.get(JINA_URL + url, timeout=PAGE_TIMEOUT, headers={
        "Authorization": f"Bearer {os.getenv('JINA_API_KEY', '')}", "X-Return-Format": "text",
        "User-Agent": USER_AGENT})
    response.raise_for_status()
    return _clean(response.text, PAGE_LIMIT)


def read_page(url):
    """Текст страницы целиком или пустая строка: с JINA_API_KEY через Jina Reader, иначе напрямую."""
    url = _safe_url(url)
    if not url or not getattr(settings, "MARKET_READ_PAGES", False):
        return ""
    readers = ([_read_jina] if os.getenv("JINA_API_KEY") else []) + [_read_direct]
    for reader in readers:
        try:
            text = reader(url)
        except (httpx.HTTPError, ValueError, UnicodeError, LookupError) as exc:
            logger.info("Page read failed: reader=%s error=%s", reader.__name__, type(exc).__name__)
            continue
        if len(text) > 200:
            return text
    return ""


def read_pages(sources, count=3, kinds=None):
    """Дочитать до count страниц конкурентов целиком (поле page), параллельно. Остальные источники как были."""
    picked = [index for index, source in enumerate(sources)
              if (kinds is None or source.get("kind") in kinds)][:count]
    if not picked:
        return sources
    with ThreadPoolExecutor(max_workers=len(picked)) as pool:
        pages = list(pool.map(lambda index: read_page(sources[index]["url"]), picked))
    sources = [dict(source) for source in sources]
    for index, page in zip(picked, pages):
        if page:
            sources[index]["page"] = page
    return sources

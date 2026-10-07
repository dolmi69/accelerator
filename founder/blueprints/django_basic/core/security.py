import hashlib
import time
from urllib.parse import urlsplit
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http.request import validate_host
from .models import RateBucket


def allowed(key, limit, seconds):
    key = hashlib.sha256(key.encode()).hexdigest()
    window = int(time.time()) // seconds
    with transaction.atomic():
        bucket, _ = RateBucket.objects.get_or_create(key=key)
        if bucket.window != window:
            RateBucket.objects.filter(pk=key).update(count=0, window=window)
        return bool(RateBucket.objects.filter(pk=key, count__lt=limit).update(count=F("count") + 1))


class SameOriginWebSocket:
    """Reject foreign origins, even when another site uses localhost."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        try:
            host = headers.get(b"host", b"").decode("ascii").lower()
            origin = headers.get(b"origin", b"").decode("ascii")
            parsed = urlsplit(origin)
            scheme = "https" if scope.get("scheme") == "wss" else "http"
            valid = (validate_host(urlsplit("//" + host).hostname or "", settings.ALLOWED_HOSTS)
                     and parsed.scheme in {"http", "https"}
                     and (origin == scheme + "://" + host or origin in settings.WS_ALLOWED_ORIGINS))
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            await send({"type": "websocket.close", "code": 4403})
            return
        return await self.app(scope, receive, send)

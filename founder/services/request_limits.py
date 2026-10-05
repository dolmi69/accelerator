"""Database-backed request limits shared by the website and Telegram workers."""
import math
import hashlib
import uuid
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from founder.models import AIRequestLease, RequestLimit


class RequestLimitExceeded(Exception):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = max(1, math.ceil(retry_after))


def consume_limit(identity, limit, seconds):
    """Conditional updates avoid read/increment races, including on SQLite."""
    # Both local origins use the same DB but deliberately different session keys.
    # A stable digest keeps their quotas shared, even after secret rotation.
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    now = timezone.now()
    expires = now + timedelta(seconds=seconds)
    _, created = RequestLimit.objects.get_or_create(key=key, defaults={"expires_at": expires})
    if created:
        RequestLimit.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
    RequestLimit.objects.filter(key=key, expires_at__lte=now).update(count=0, expires_at=expires)
    accepted = RequestLimit.objects.filter(key=key, expires_at__gt=now, count__lt=limit).update(count=F("count") + 1)
    if not accepted:
        deadline = RequestLimit.objects.values_list("expires_at", flat=True).get(key=key)
        raise RequestLimitExceeded("Слишком много запросов. Немного подождите и попробуйте снова.",
                                   (deadline - now).total_seconds())


def acquire_ai_lease(user_id):
    now = timezone.now()
    token = uuid.uuid4()
    expires = now + timedelta(minutes=10)
    _, created = AIRequestLease.objects.get_or_create(
        user_id=user_id, defaults={"token": token, "expires_at": expires},
    )
    if not created and not AIRequestLease.objects.filter(user_id=user_id, expires_at__lte=now).update(
        token=token, expires_at=expires,
    ):
        raise RequestLimitExceeded("Бруно уже выполняет ваш запрос. Дождитесь ответа и попробуйте снова.", 5)
    return token


def release_ai_lease(user_id, token):
    # A late response must never unlock a newer request.
    AIRequestLease.objects.filter(user_id=user_id, token=token).update(expires_at=timezone.now())

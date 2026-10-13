"""Plan access is server-controlled; paid checkout is implemented separately."""
from django.conf import settings
from django.utils import timezone


def is_pro(user):
    until = getattr(user, 'pro_until', None)
    return bool(getattr(user, 'is_authenticated', False) and until and until > timezone.now())


def quota(user, name):
    if name != 'lab_requests_per_day':
        raise ValueError('Unknown plan quota')
    free = settings.LAB_REQUESTS_PER_DAY
    # Preserve an explicit development switch, and the existing shared counter.
    if free <= 0 or not is_pro(user):
        return free
    return max(free, settings.LAB_PRO_REQUESTS_PER_DAY)

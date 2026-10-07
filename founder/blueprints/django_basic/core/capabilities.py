from functools import wraps
from django.conf import settings
from django.http import Http404
from module_catalog import normalize_modules
from .models import Membership, SiteControl


def enabled(key):
    return key in normalize_modules(settings.SITE.get('modules', []))


def module_required(key):
    def decorate(view):
        @wraps(view)
        def check(request, *args, **kwargs):
            if not enabled(key):
                raise Http404('Модуль не подключён')
            return view(request, *args, **kwargs)
        return check
    return decorate


def role(user):
    if not user.is_authenticated or not user.is_active:
        return ''
    if user.is_superuser:
        return Membership.Role.OWNER
    return Membership.objects.filter(user=user).values_list('role', flat=True).first() or Membership.Role.CLIENT


def editor(user):
    return role(user) in {'owner','editor'}


def manages(user, obj):
    return editor(user) or (user.is_authenticated and obj.owner_id == user.pk)


def can_create_item(user):
    return user.is_authenticated and (editor(user) or SiteControl.objects.filter(pk=1, allow_listings=True).exists())

"""Temporary pause without deleting survey questions, responses or linked tasks."""
import re
from django.conf import settings


def enabled():
    return getattr(settings, 'KANO_SURVEYS_ENABLED', False)


def visible_tasks(queryset):
    return queryset if enabled() else queryset.exclude(origin='kano')


def step_visible(step):
    return enabled() or not re.search(r'\b(?:кано|kano)\b',
        ' '.join(str(step.get(key, '')) for key in ('title', 'action', 'done_when')), re.IGNORECASE)

"""Лента «что нового в проекте». Тексты без глаголов прошедшего времени:
пол участника неизвестен, поэтому «Радар обновлён», а не «обновил(а)»."""
from founder.models import ProjectActivity

Kind = ProjectActivity.Kind


def log(startup, actor, kind, text):
    ProjectActivity.objects.create(startup=startup, actor=actor if actor and actor.is_authenticated else None,
                                   kind=kind, text=text[:240])


def quoted(value, limit=80):
    value = " ".join(str(value).split())
    return f"«{value[:limit - 1]}…»" if len(value) > limit else f"«{value}»"


def recent(startup, limit=8):
    return startup.activities.select_related("actor")[:limit]

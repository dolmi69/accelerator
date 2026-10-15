"""Чем сейчас занят Бруно у основателя.

Долгий AI-запрос держит блокировку «один запрос на аккаунт» (AIRequestLease). В ней записано, какое
действие идёт и в каком проекте, поэтому любая страница может показать «Бруно сейчас изучает рынок…»,
даже если основатель ушёл со страницы и вернулся: под ASGI запрос доводится до конца без него
(security_middleware.guard_lease), а результат появится, когда Бруно закончит.
"""
from django.utils import timezone

from founder.models import AIRequestLease

LABELS = {
    "chat_send": "отвечает вам в чате",
    "metrics_assess": "оценивает проект по радару",
    "pitch_finish": "разбирает тренировку питча",
    "panel_vote": "собирает голоса акул",
    "tasks_generate": "готовит задания",
    "card_generate": "готовит карточку проекта",
    "card_edit": "готовит карточку проекта",
    "review_generate": "готовит разбор и план",
    "market_generate": "изучает рынок и конкурентов",
    "ideas_generate": "развивает идею",
    "idea_new_generate": "развивает идею",
    "ideas_advice": "анализирует выбранную идею",
    "idea_new_advice": "анализирует выбранную идею",
    "idea_new_card": "составляет карточку проекта",
    "idea_new_card:create": "создаёт проект и оценивает его по радару",
}
DEFAULT_LABEL = "выполняет ваш запрос"


def job_key(route, request):
    """Название действия для блокировки: у карточки из идеи важно, что именно делает Бруно."""
    if route == "idea_new_card" and request.POST.get("action") == "create":
        return "idea_new_card:create"
    return route


def label(route):
    return LABELS.get(route or "", DEFAULT_LABEL)


def current_job(user):
    """Действие, которое Бруно сейчас выполняет для основателя, или None."""
    if not getattr(user, "is_authenticated", False):
        return None
    lease = AIRequestLease.objects.filter(user=user, expires_at__gt=timezone.now()).first()
    if lease is None:
        return None
    seconds = int((timezone.now() - lease.started_at).total_seconds()) if lease.started_at else 0
    return {"route": lease.route, "label": label(lease.route), "seconds": max(0, seconds),
            "startup_id": str(lease.startup_id) if lease.startup_id else ""}

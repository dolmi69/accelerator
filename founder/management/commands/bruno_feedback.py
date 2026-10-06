"""Собрать ответы Бруно, которые основатели отметили как неудачные.

Каждый случай — готовая заготовка сценария для `bruno_eval`: что написал
основатель, что ответил Бруно и что, по словам человека, было не так.

    python manage.py bruno_feedback              # за последние 30 дней
    python manage.py bruno_feedback --days 7
"""

import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from founder.models import ChatMessage, MessageFeedback


class Command(BaseCommand):
    help = "Выгружает ответы Бруно с оценкой 👎 в .runtime/bruno_feedback/ для новых проверочных сценариев."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options["days"])
        items = (MessageFeedback.objects.filter(rating=MessageFeedback.Rating.DOWN, updated_at__gte=since)
                 .select_related("message__session")[:options["limit"]])
        up = MessageFeedback.objects.filter(rating=MessageFeedback.Rating.UP, updated_at__gte=since).count()
        lines = [f"# Неудачные ответы Бруно за {options['days']} дн.\n",
                 f"👍 {up} · 👎 {len(items)}\n"]
        for item in items:
            reply = item.message
            asked = (ChatMessage.objects.filter(session=reply.session, role=ChatMessage.Role.USER,
                                                created_at__lte=reply.created_at)
                     .order_by("-created_at", "-id").first())
            lines += [
                f"\n## {reply.created_at:%d.%m.%Y %H:%M} · {reply.session.get_mode_display()} · {reply.model_name or '—'}\n",
                f"**Основатель:** {asked.content if asked else '—'}\n",
                f"**Бруно:** {reply.content}\n",
                f"**Что не так:** {item.comment or 'не указано'}\n",
            ]
        out = Path(settings.BASE_DIR) / ".runtime" / "bruno_feedback" / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"👍 {up}, 👎 {len(items)}. Отчёт: {out}"))

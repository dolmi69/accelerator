"""Представление проектов, источников и сохранённых версий радара."""

from uuid import UUID

from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from founder.models import StartupMetrics
from founder.services.metrics import AXES, radar_grid, radar_points


def grid_context():
    return {f"radar_grid_{size}": radar_grid(size) for size in (25, 50, 75, 100)}


def project_cards(user):
    projects = user.startups.prefetch_related(Prefetch(
        "metric_snapshots", queryset=StartupMetrics.objects.all()[:1], to_attr="latest_snapshots",
    ))
    cards = []
    for startup in projects:
        latest = startup.latest_snapshots[0] if startup.latest_snapshots else None
        cards.append({
            "startup": startup, "latest": latest, "points": radar_points(latest) if latest else "",
            "axes": [(label, getattr(latest, key, None)) for key, label in AXES],
        })
    return cards


def evidence_display(startup, snapshot, key):
    item = dict(snapshot.assessment_evidence.get(key, {})) if snapshot else {}
    status = item.get("status", "unlinked")
    if snapshot and snapshot.source == StartupMetrics.Source.MANUAL and not item:
        status = "manual"
    labels = {"stated": "Со слов основателя", "assumption": "Предположение",
              "missing": "Нет данных", "unlinked": "Источник не указан", "manual": "Поправлено вручную"}
    item.update({"status": status, "status_label": labels.get(status, "Источник не указан")})
    if item.get("quote"):
        if item.get("kind") == "profile":
            item["url"] = reverse("startup_edit", args=[startup.id])
        elif item.get("kind") == "diary" and item.get("entry_id"):
            item["url"] = reverse("evidence_edit", args=[startup.id, item["entry_id"]])
        elif item.get("kind") == "lab" and item.get("version_id"):
            item["url"] = reverse("lab", args=[startup.id]) + f'?version={item["version_id"]}'
            item["status_label"] = "Наблюдение в прототипе"
        elif item.get("session_id") and item.get("message_id"):
            item["url"] = reverse("chat_detail", args=[startup.id, item["session_id"]]) + f'?message={item["message_id"]}#message-{item["message_id"]}'
    return item


def history_context(startup, snapshot_id=None, page_number=1):
    snapshots = startup.metric_snapshots.all()
    page = Paginator(snapshots, 8).get_page(page_number)
    selected = snapshots.first()
    if snapshot_id:
        try:
            selected_id = UUID(snapshot_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise Http404("Оценка не найдена") from exc
        selected = get_object_or_404(snapshots, pk=selected_id)
    previous = None
    if selected:
        previous = snapshots.filter(
            Q(assessed_at__lt=selected.assessed_at)
            | Q(assessed_at=selected.assessed_at, id__lt=selected.id)
        ).first()
    rows = []
    if selected:
        for key, label in AXES:
            rows.append({
                "label": label, "score": getattr(selected, key),
                "before": getattr(previous, key) if previous else None,
                "delta": getattr(selected, key) - getattr(previous, key) if previous else None,
                "reason": selected.assessment_details.get(key, ""),
                "old_reason": previous.assessment_details.get(key, "") if previous else "",
                "evidence": evidence_display(startup, selected, key),
            })
    return {
        "history_page": page, "history_selected": selected, "history_previous": previous,
        "history_rows": rows,
        "history_points": radar_points(selected) if selected else "",
        "history_previous_points": radar_points(previous) if previous else "",
    }

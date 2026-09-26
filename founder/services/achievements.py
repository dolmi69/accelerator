"""Небольшие награды за заполнение и уточнение картины проекта."""

from founder.models import StartupAchievement, StartupMetrics
from founder.services.metrics import AXES


BADGES = (
    ("first_radar", "Первый радар", "Получить первую оценку Бруно.", "✦"),
    ("five_axes", "Картина сложилась", "Добавить сведения с источниками по всем пяти направлениям.", "◎"),
    ("refined", "Точнее с каждым шагом", "Доработать пояснение в сохранённой таблице.", "✎"),
    ("progress", "Новые результаты", "Поднять AI-оценку направления на 10 баллов с новыми сведениями.", "↗"),
)


def award_achievements(startup, snapshot):
    codes = []
    previous = startup.metric_snapshots.exclude(pk=snapshot.pk).first()
    if snapshot.source == StartupMetrics.Source.AI:
        codes.append("first_radar")
        if all(snapshot.assessment_evidence.get(key, {}).get("quote") for key, _ in AXES):
            codes.append("five_axes")
        previous_ai = startup.metric_snapshots.filter(source=StartupMetrics.Source.AI).exclude(pk=snapshot.pk).first()
        if previous_ai and any(
            getattr(snapshot, key) - getattr(previous_ai, key) >= 10
            and snapshot.assessment_evidence.get(key, {}).get("quote")
            and snapshot.assessment_evidence[key]["quote"] != previous_ai.assessment_evidence.get(key, {}).get("quote", "")
            for key, _ in AXES
        ):
            codes.append("progress")
    elif previous and any(
        snapshot.assessment_details.get(key, "").strip()
        and snapshot.assessment_details.get(key) != previous.assessment_details.get(key)
        for key, _ in AXES
    ):
        codes.append("refined")
    for code in codes:
        StartupAchievement.objects.get_or_create(startup=startup, code=code)


def achievement_cards(startup):
    earned = {item.code: item.earned_at for item in startup.achievements.all()}
    return [
        {"code": code, "title": title, "description": description, "icon": icon, "earned_at": earned.get(code)}
        for code, title, description, icon in BADGES
    ]

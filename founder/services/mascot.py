"""Единое место, где показатели бизнеса превращаются в образ Бруно."""

from founder.models import MascotState


METRIC_LABELS = {
    "product": "продукт",
    "market": "рынок",
    "finance": "финансы",
    "team": "команда",
    "pitch": "ясность идеи",
}


def update_mascot(startup, metrics=None):
    """Обновить постоянный образ Бруно после нового снимка радара."""
    state, _ = MascotState.objects.get_or_create(startup=startup)
    metrics = metrics or startup.metric_snapshots.first()

    if metrics is None:
        mood = MascotState.Mood.CURIOUS
        outfit = MascotState.Outfit.HOODIE
        reason = "Расскажите о своём сервисе — Бруно готов слушать."
    else:
        scores = {name: getattr(metrics, name) for name in METRIC_LABELS}
        weakest = min(scores, key=scores.get)
        weakest_label = METRIC_LABELS[weakest]

        if metrics.overall_score < 40:
            mood = MascotState.Mood.SLEEPY
            outfit = MascotState.Outfit.PAJAMAS
            reason = f"Пока рано расслабляться: слабее всего выглядит {weakest_label}."
        elif metrics.overall_score >= 75 and min(scores.values()) >= 60:
            mood = MascotState.Mood.CONFIDENT
            outfit = MascotState.Outfit.SUIT
            reason = "Все пять направлений выглядят уверенно. Теперь можно уточнять детали и план действий."
        else:
            mood = MascotState.Mood.FOCUSED
            outfit = MascotState.Outfit.JACKET
            reason = f"Есть прогресс. Следующий приоритет — {weakest_label}."

    state.mood = mood
    state.outfit = outfit
    state.reason = reason
    state.save(update_fields=["mood", "outfit", "reason", "updated_at"])
    return state

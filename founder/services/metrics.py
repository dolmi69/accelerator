"""Данные для пятиосевого радара стартапа."""

import math


AXES = (
    ("product", "Продукт"),
    ("market", "Рынок"),
    ("finance", "Финансы"),
    ("team", "Команда"),
    ("pitch", "Ясность идеи"),
)


def radar_points(metrics, radius=82, center=110):
    """Сформировать координаты SVG из пяти оценок 0–100."""
    values = [getattr(metrics, key, 0) if metrics else 0 for key, _ in AXES]
    points = []
    for index, value in enumerate(values):
        angle = math.radians(-90 + index * 72)
        distance = radius * value / 100
        x = center + math.cos(angle) * distance
        y = center + math.sin(angle) * distance
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def radar_grid(scale, radius=82, center=110):
    class GridValues:
        pass

    values = GridValues()
    for key, _ in AXES:
        setattr(values, key, scale)
    return radar_points(values, radius=radius, center=center)

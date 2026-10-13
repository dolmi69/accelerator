"""Собрать маску-силуэт Бруно для аксессуаров: белый — Бруно, чёрный — фон.

Шарф рисуется поверх картинки, и маска обрезает его ровно по контуру тела, поэтому
лента выглядит уходящей за спину. Перезапустите команду, если поменяется
static/founder/img/bruno-reference.png.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image, ImageChops, ImageDraw, ImageFilter

SOURCE = Path("static/founder/img/bruno-reference.png")
TARGET = Path("static/founder/img/bruno-silhouette.png")
BACKGROUND = 128


class Command(BaseCommand):
    help = "Пересобрать маску-силуэт Бруно из bruno-reference.png"

    def handle(self, *args, **options):
        image = Image.open(settings.BASE_DIR / SOURCE).convert("RGB")
        # Светлое (белый фон и серые тени) — кандидат в фон. Фоном считается только то,
        # что связано с краем картинки: белки глаз и страницы книги остаются внутри силуэта.
        light = [channel.point(lambda value: 255 if value > 215 else 0) for channel in image.split()]
        candidates = ImageChops.darker(ImageChops.darker(light[0], light[1]), light[2])
        for corner in ((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1)):
            ImageDraw.floodfill(candidates, corner, BACKGROUND)
        silhouette = candidates.point(lambda value: 0 if value == BACKGROUND else 255)
        silhouette.filter(ImageFilter.GaussianBlur(1)).save(settings.BASE_DIR / TARGET, optimize=True)
        self.stdout.write(self.style.SUCCESS(f"Силуэт сохранён: {TARGET}"))

"""Проверка входящих платежей USDT TRC-20 и зачисление на баланс.

Запускается Планировщиком задач раз в минуту через pythonw.exe — без окна
консоли. Поэтому строку прогона команда пишет в файл сама:

    .runtime/payments_poller.log
"""

from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from payments.services.checker import check_all


class Command(BaseCommand):
    help = "Проверяет входящие USDT TRC-20 и пополняет баланс пользователей."

    def handle(self, *args, **options):
        try:
            stats = check_all()
            line = (
                f"{datetime.now():%Y-%m-%d %H:%M:%S} "
                f"checked={stats['checked']} paid={stats['paid']} errors={stats['errors']}"
            )
        except Exception as exc:  # noqa: BLE001 — поллер не должен падать молча
            line = f"{datetime.now():%Y-%m-%d %H:%M:%S} ERROR {type(exc).__name__}: {exc}"

        log_path = Path(settings.BASE_DIR) / ".runtime" / "payments_poller.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

        self.stdout.write(line)

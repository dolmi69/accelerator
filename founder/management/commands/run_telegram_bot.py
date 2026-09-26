import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from founder.services.telegram import TelegramClient, TelegramError, handle_update, validate_app_url


class Command(BaseCommand):
    help = "Запустить Telegram-бота через long polling (один процесс на токен)."

    def handle(self, *args, **options):
        try:
            app_url = validate_app_url(settings.TELEGRAM_MINI_APP_URL)
            with TelegramClient() as client:
                if client.call("getWebhookInfo").get("url"):
                    raise CommandError("У бота уже настроен webhook. Сначала выберите способ его переноса.")
                bot = client.call("getMe")
                self.stdout.write(f"@{bot['username']} запущен. Для остановки нажмите Ctrl+C.")
                offset = 0
                while True:
                    try:
                        updates = client.call("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
                        for update in updates:
                            try:
                                handle_update(client, update, app_url)
                            except TelegramError as exc:
                                # A blocked recipient or bad message must not stop all other users.
                                if exc.code not in (400, 403):
                                    raise
                            offset = update["update_id"] + 1
                    except TelegramError as exc:
                        if exc.code in (401, 409):
                            raise
                        self.stderr.write(str(exc))
                        time.sleep(exc.retry_after)
        except KeyboardInterrupt:
            self.stdout.write("Бот остановлен.")
        except TelegramError as exc:
            raise CommandError(str(exc)) from None

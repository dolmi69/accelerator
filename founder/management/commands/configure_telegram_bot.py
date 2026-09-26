from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from founder.services.telegram import TelegramClient, TelegramError, configure_bot


class Command(BaseCommand):
    help = "Установить команды и кнопку Mini App в Telegram-боте."

    def handle(self, *args, **options):
        try:
            with TelegramClient() as client:
                bot = configure_bot(client, settings.TELEGRAM_MINI_APP_URL)
            self.stdout.write(self.style.SUCCESS(f"Бот @{bot['username']} настроен."))
        except TelegramError as exc:
            raise CommandError(str(exc)) from None

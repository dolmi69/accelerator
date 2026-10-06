"""Check the actual provider with a small, bounded paid request."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from founder.services.qwen import QwenError, generate_code


class Command(BaseCommand):
    help = "Проверить ключ и модель Qwen через Cloud.ru (один запрос, до 16 выходных токенов)."

    def handle(self, *args, **options):
        try:
            result = generate_code(
                "Reply with the single word OK.", [{"role": "user", "content": "Connection test."}],
                max_tokens=min(16, settings.QWEN_CODE_MAX_TOKENS),
            )
        except QwenError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(f"Cloud.ru отвечает. Модель: {result.model}"))
        self.stdout.write(f"Токены: вход {result.input_tokens}, выход {result.output_tokens}.")

"""Developer entry point for testing generation before adding the lab UI."""
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from founder.services.qwen import QwenError
from founder.services.site_generator import generate_site


class Command(BaseCommand):
    help = "Создать статический сайт через Qwen; сохранить код и расход в .runtime/qwen-sites/."

    def add_arguments(self, parser):
        parser.add_argument("--prompt", required=True, help="Задание на создание или изменение сайта.")
        parser.add_argument("--input", type=Path, help="Предыдущий index.html для доработки.")
        parser.add_argument("--max-tokens", type=int, help="Лимит ответа, не выше QWEN_CODE_MAX_TOKENS.")

    def handle(self, *args, **options):
        previous_html = ""
        if options["input"]:
            try:
                # Read at most the accepted source size, not an arbitrary local file.
                with options["input"].open(encoding="utf-8") as source:
                    previous_html = source.read(120_001)
            except (OSError, UnicodeError):
                raise CommandError("Не удалось прочитать исходный HTML в UTF-8.") from None
        try:
            result = generate_site(options["prompt"], previous_html=previous_html,
                                   max_tokens=options["max_tokens"])
        except QwenError as exc:
            raise CommandError(str(exc)) from None
        # The directory and filenames are chosen by us, never by the model.
        destination = settings.BASE_DIR / ".runtime" / "qwen-sites" / str(uuid.uuid4())
        metadata = asdict(result)
        metadata.pop("text")
        try:
            destination.mkdir(parents=True, mode=0o700)
            for name, text in {
                "index.html": result.text,
                "usage.json": json.dumps(metadata, ensure_ascii=False, indent=2),
            }.items():
                path = destination / name
                with path.open("x", encoding="utf-8") as output:
                    os.chmod(path, 0o600)
                    output.write(text)
        except OSError:
            raise CommandError("Cloud.ru ответил, но сохранить файлы не удалось. Проверьте место на диске.") from None
        self.stdout.write(self.style.SUCCESS(f"HTML сохранён: {destination / 'index.html'}"))
        self.stdout.write(f"Модель: {result.model}; токены: вход {result.input_tokens}, выход {result.output_tokens}.")
        self.stdout.write("Это код прототипа. Предпросмотр в приложении должен выполняться в изолированной среде.")

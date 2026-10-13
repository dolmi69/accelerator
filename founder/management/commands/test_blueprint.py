"""Exercise the exact Django blueprint shipped by the laboratory, without keys."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from founder.models import LabSiteVersion
from founder.services.django_builder import project_files
from founder.services.backend_modules import OPTIONAL_MODULES
from runtime_support import runtime_environment


class Command(BaseCommand):
    help = 'Run generated-site tests in a temporary project, including on Windows.'

    def handle(self, *args, **options):
        version = SimpleNamespace(kind=LabSiteVersion.Kind.DJANGO, pk=uuid4(), startup_id=uuid4(),
            startup=SimpleNamespace(name='Тестовый проект'), presentation={},
            backend_modules=['accounts', *OPTIONAL_MODULES],
            html='<!doctype html><html lang="ru"><head></head><body><h1>Тест</h1></body></html>')
        with TemporaryDirectory(prefix='cofounder-blueprint-') as directory:
            root = Path(directory)
            for name, content in project_files(version).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            env = runtime_environment()
            env.update(DJANGO_SETTINGS_MODULE='config.settings', DJANGO_DEBUG='1')
            try:
                subprocess.run([sys.executable, 'manage.py', 'test', 'core', '--noinput'],
                               cwd=root, env=env, check=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                raise CommandError('Generated-site tests failed.') from exc

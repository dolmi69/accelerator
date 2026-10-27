"""Start one selected service. Secrets are supplied by the hosting environment."""
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.amvera")
os.environ.setdefault("DJANGO_DEBUG", "0")
role = os.environ.get("BRUNOLAB_SERVICE", "web")
if role == "bot":
    os.execv(sys.executable, [sys.executable, "manage.py", "run_telegram_bot"])
if role != "web":
    raise SystemExit("BRUNOLAB_SERVICE must be web or bot")
Path(os.environ.get("AMVERA_DATA_DIR", "/data")).mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
os.execvp("daphne", ["daphne", "-b", "0.0.0.0", "-p", "8080", "config.asgi:application"])

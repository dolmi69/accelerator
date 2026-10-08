"""Run after installing requirements. One ASGI process supports live chats."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    root = Path(__file__).resolve().parent
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], cwd=root, check=True)
    subprocess.run([sys.executable, 'manage.py', 'prepare_site'], cwd=root, check=True)
    print(f"Open http://localhost:{args.port}/", flush=True)
    subprocess.run([sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(args.port), "config.asgi:application"], cwd=root, check=True)

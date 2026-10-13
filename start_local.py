"""Start the local platform on Windows, macOS and Linux with this Python."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

from start_shared_events import ensure_shared_events

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535')
    with socket.socket() as check:
        if sys.platform != 'win32':
            # Match runserver's reuse policy after a recent restart (TIME_WAIT).
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        check.bind(('127.0.0.1', args.port))  # do not migrate/open somebody else's server
    ensure_shared_events()
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], cwd=BASE, env=env, check=True)
    process = subprocess.Popen([sys.executable, 'manage.py', 'runserver',
                                f'127.0.0.1:{args.port}', '--noreload'], cwd=BASE, env=env)
    url = f'http://127.0.0.1:{args.port}/'
    try:
        if not args.no_browser:
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with opener.open(url + 'login/', timeout=1):
                        webbrowser.open(url)
                        break
                except (URLError, OSError):
                    time.sleep(0.2)
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Не удалось запустить сайт: {exc}', file=sys.stderr)
        raise SystemExit(1)

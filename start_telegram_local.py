"""Run a temporary public HTTPS bridge to this Mac and the launcher bot."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import time

import httpx
from dotenv import dotenv_values
from start_shared_events import ensure_shared_events


BASE = Path(__file__).resolve().parent
RUNTIME = BASE / ".runtime"


def check_fresh_dns(url):
    """macOS may cache NXDOMAIN for a just-created tunnel; verify fresh DNS.

    curl still verifies the TLS certificate against the original hostname.
    No system DNS settings or hosts files are changed.
    """
    host = url.split("//", 1)[1]
    try:
        result = subprocess.run(["dig", "+short", host, "A"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            try:
                address = ipaddress.IPv4Address(line)
            except ValueError:
                continue
            if not address.is_global:
                continue
            response = subprocess.run(
                ["curl", "--silent", "--fail", "--max-time", "10", "--resolve", f"{host}:443:{address}", url + "/login/"],
                capture_output=True, text=True, timeout=12,
            )
            if response.returncode == 0 and "Co-Founder" in response.stdout:
                return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def main():
    os.umask(0o077)
    RUNTIME.mkdir(exist_ok=True)
    lock = (RUNTIME / "telegram.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Telegram уже запущен. Повторный запуск не требуется.")
        return
    config = dotenv_values(BASE / ".env")
    if not config.get("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError("В .env отсутствует TELEGRAM_BOT_TOKEN.")
    ensure_shared_events()
    binary = BASE / ".tools" / "cloudflared"
    if not binary.exists():
        raise RuntimeError("Не найден .tools/cloudflared.")
    with socket.socket() as check:
        # Recently closed connections can keep the port in TIME_WAIT. Match the
        # server's reuse option while still rejecting an active listener.
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            check.bind(("127.0.0.1", 8001))
        except OSError:
            raise RuntimeError("Порт 8001 занят. Сначала остановите предыдущий запуск.") from None
    children, logs = [], []

    def start(args, name, env=None):
        log = (RUNTIME / f"{name}.log").open("w")
        logs.append(log)
        process = subprocess.Popen(args, cwd=BASE, env=env, stdout=log, stderr=subprocess.STDOUT)
        children.append(process)
        return process

    def stop_signal(*args):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    try:
        tunnel = start([str(binary), "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://127.0.0.1:8001"], "tunnel")
        print("Создаю временную HTTPS-ссылку…", flush=True)
        url = None
        for _ in range(60):
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", (RUNTIME / "tunnel.log").read_text())
            if match:
                url = match.group(0)
                break
            if tunnel.poll() is not None:
                raise RuntimeError("Не удалось открыть туннель. Подробности в .runtime/tunnel.log.")
            time.sleep(1)
        if not url:
            raise RuntimeError("Не дождались HTTPS-ссылки. Повторите запуск.")
        env = dict(os.environ)
        env.update({k: v for k, v in config.items() if v is not None})
        secret_file = RUNTIME / "web_secret"
        if not secret_file.exists():
            secret_file.write_text(secrets.token_urlsafe(48))
        env.update(TELEGRAM_MINI_APP_URL=url, DJANGO_SETTINGS_MODULE="config.telegram_local",
                   DJANGO_DEBUG="0", DJANGO_SECRET_KEY=secret_file.read_text().strip(), PYTHONUNBUFFERED="1")
        start([sys.executable, "manage.py", "runserver", "127.0.0.1:8001", "--insecure", "--noreload"], "website", env)
        # Verify the bridge before publishing its address in the bot menu.
        ready = False
        with httpx.Client(timeout=10, follow_redirects=True) as web:
            for _ in range(30):
                try:
                    response = web.get(url + "/login/")
                    if response.status_code == 200 and "Co-Founder" in response.text:
                        ready = True
                        break
                except httpx.HTTPError:
                    if check_fresh_dns(url):
                        ready = True
                        break
                time.sleep(2)
        if not ready:
            raise RuntimeError("Ссылка пока недоступна. Проверьте .runtime/tunnel.log и website.log.")
        result = subprocess.run([sys.executable, "manage.py", "configure_telegram_bot"], cwd=BASE, env=env, check=False)
        if result.returncode:
            raise RuntimeError("Telegram не принял настройки бота.")
        start([sys.executable, "manage.py", "run_telegram_bot"], "bot", env)
        (RUNTIME / "telegram.json").write_text(json.dumps({"url": url, "pid": os.getpid()}, indent=2))
        print(f"Сайт на Mac доступен через {url}\nБот запущен. Не закрывайте этот процесс. Ctrl+C — остановить.", flush=True)
        while all(process.poll() is None for process in children):
            time.sleep(2)
        raise RuntimeError("Один из процессов остановился. Подробности в .runtime/*.log.")
    except KeyboardInterrupt:
        print("Останавливаю Telegram и временную ссылку.")
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        for log in logs:
            log.close()
        (RUNTIME / "telegram.json").unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

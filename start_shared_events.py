"""Start the optional project-local Valkey for website/Telegram event delivery.

External Redis services are managed by their operator. We only start the bundled
binary for the explicitly configured loopback address on port 6387.
"""
import fcntl
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values
from redis import Redis
from redis.exceptions import RedisError

BASE = Path(__file__).resolve().parent


def ensure_shared_events():
    config = dotenv_values(BASE / '.env')
    url = os.environ.get('REDIS_URL', config.get('REDIS_URL', ''))
    if not url:
        return
    runtime = BASE / '.runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    with (runtime / 'events.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2) as client:
            try:
                if client.ping():
                    return
            except RedisError:
                pass
            parts = urlsplit(url)
            local = (parts.scheme == 'redis' and parts.hostname == '127.0.0.1'
                     and parts.port == 6387 and not parts.username and not parts.password)
            binary = BASE / '.tools' / 'valkey-server'
            if not local or not binary.is_file():
                raise RuntimeError('Общий канал сообщений недоступен. Запустите Redis/Valkey из REDIS_URL.')
            subprocess.run([
                str(binary), '--bind', '127.0.0.1', '--port', '6387', '--protected-mode', 'yes',
                '--daemonize', 'yes', '--save', '', '--appendonly', 'no',
                '--maxmemory', '128mb', '--maxmemory-policy', 'noeviction',
                '--dir', str(runtime), '--pidfile', str(runtime / 'valkey.pid'),
                '--logfile', str(runtime / 'valkey.log'),
            ], check=True, timeout=10, stdout=subprocess.DEVNULL)
            for _ in range(20):
                try:
                    if client.ping():
                        return
                except RedisError:
                    pass
                time.sleep(0.1)
            raise RuntimeError('Не удалось запустить общий канал сообщений. Проверьте .runtime/valkey.log.')


if __name__ == '__main__':
    ensure_shared_events()

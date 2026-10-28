"""Isolated hosted WSGI processes; never execute user code in Django.

macOS: deny-by-default sandbox. Linux: prebuilt container, no network, Unix
socket transport. No unconfined subprocess fallback on unsupported machines.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time

import httpx
from django.conf import settings
from runtime_support import file_lock
from founder.services.generated_backend import validate_files
from founder.services.generated_runtime import available, sandbox_profile
from founder.services.django_builder import BuilderError


def enabled():
    return ((settings.BRUNOHOST_ENGINE == 'sandbox' and available()) or
            (settings.BRUNOHOST_ENGINE == 'docker' and sys.platform == 'linux' and bool(shutil.which('docker'))))


def directory(site):
    return Path(settings.BRUNOHOST_ROOT) / site.slug


def _read(site):
    try:
        return json.loads((directory(site) / 'state.json').read_text())
    except (OSError, ValueError):
        return None


def client(state):
    transport = httpx.HTTPTransport(uds=state['socket']) if state.get('socket') else httpx.HTTPTransport()
    return httpx.Client(transport=transport, timeout=httpx.Timeout(8, connect=1), trust_env=False, follow_redirects=False)


def target(state):
    return 'http://worker:8099' if state.get('socket') else f"http://127.0.0.1:{state['port']}"


def healthy(state, site):
    if not state or state.get('slug') != site.slug:
        return False
    try:
        with client(state) as connection:
            reply = connection.get(target(state) + '/__lab_health', headers={'Host': state['host']}, timeout=.5)
        data = reply.json()
        return reply.status_code == 200 and data['version_id'] == state['version'] and data['project_id'] == site.slug
    except (httpx.HTTPError, ValueError, KeyError, OSError):
        return False


def _stop(site):
    state = _read(site)
    if state:
        if state.get('container') == 'brunohost-' + site.slug:
            if not shutil.which('docker'):
                raise BuilderError('Docker недоступен: не удалось остановить исполнитель сайта.')
            result = subprocess.run(['docker', 'rm', '-f', state['container']], capture_output=True, timeout=15)
            if result.returncode and b'No such container' not in result.stderr and b'No such object' not in result.stderr:
                raise BuilderError('Не удалось остановить исполнитель сайта. Проверьте соединение с Docker.')
        elif type(state.get('pid')) is int and state['pid'] > 1:
            details = subprocess.run(['ps', '-p', str(state['pid']), '-o', 'stat=', '-o', 'command='], capture_output=True, text=True).stdout.strip().split(None, 1)
            command = details[1] if len(details) > 1 and not details[0].startswith('Z') else ''
            # PID may have been reused after a restart.
            if str(directory(site) / 'app' / 'start.py') in command:
                try:
                    os.kill(state['pid'], signal.SIGTERM)
                    deadline = time.monotonic() + 1
                    while time.monotonic() < deadline:
                        os.kill(state['pid'], 0)
                        time.sleep(.05)
                    os.kill(state['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
                for _ in range(20):
                    process_status = subprocess.run(['ps', '-p', str(state['pid']), '-o', 'stat='], capture_output=True, text=True).stdout.strip()
                    if not process_status or process_status.startswith('Z'):
                        break
                    time.sleep(.05)
                else:
                    raise BuilderError('Исполнитель сайта ещё завершается. Повторите попытку.')
            elif command.strip():
                # A worker may exec() another program. Do not touch its data or
                # forget its PID while a process we cannot identify is alive.
                raise BuilderError('Не удалось подтвердить остановку исполнителя сайта.')
    (directory(site) / 'state.json').unlink(missing_ok=True)


def _checked_database(database):
    """Untrusted apps can create symlinks even when they cannot read targets.

    Never let the privileged SQLite backup follow a link to the platform DB or
    its WAL/SHM files. Caller must first stop the isolated worker.
    """
    for suffix in ('', '-wal', '-shm', '-journal'):
        file = Path(str(database) + suffix)
        try:
            info = file.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 128 * 1024 * 1024:
            raise BuilderError('База сайта имеет недопустимый формат или превышает лимит BrunoHost.')


def _restore_database(database, backup):
    for suffix in ('', '-wal', '-shm', '-journal'):
        Path(str(database) + suffix).unlink(missing_ok=True)
    if backup.exists():
        backup.replace(database)


def stop(site):
    Path(settings.BRUNOHOST_ROOT).mkdir(parents=True, exist_ok=True)
    with file_lock(Path(settings.BRUNOHOST_ROOT) / 'runtime.lock'):
        _stop(site)


def ensure(site):
    """Stable data directory across releases/restarts, never copy editor data."""
    if not enabled():
        raise BuilderError('Для серверного сайта нужен изолированный исполнитель BrunoHost. Сейчас он не подключён.')
    files = validate_files(site.version.backend_files)
    root = Path(settings.BRUNOHOST_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    with file_lock(root / 'runtime.lock'):
        state = _read(site)
        if healthy(state, site) and state['version'] == str(site.version_id):
            return state
        # Stop the old release before changing files; SQLite stays in app/data.
        _stop(site)
        running = 0
        from founder.models import HostedSite
        live = set(HostedSite.objects.filter(active=True, version__kind='generated',
                   startup__owner__is_active=True).values_list('slug', flat=True))
        for path in root.glob('h*/state.json'):
            try:
                other = json.loads(path.read_text())
                from types import SimpleNamespace
                if path.parent.name not in live:
                    _stop(SimpleNamespace(slug=path.parent.name))
                    continue
                running += bool(healthy(other, SimpleNamespace(slug=path.parent.name)))
            except (OSError, ValueError, TypeError):
                pass
        if running >= settings.BRUNOHOST_MAX_RUNNING:
            raise BuilderError('Все места BrunoHost заняты. Повторите позже.')
        folder = directory(site)
        app = folder / 'app'
        app.mkdir(parents=True, exist_ok=True)
        data = app / 'data'
        data.mkdir(exist_ok=True)
        database = data / 'data.sqlite3'
        backup = folder / 'start-backup.sqlite3'
        # A previous launcher may have died during startup/rollback. Do not
        # overwrite its recovery copy with the failed import's modified DB.
        if backup.exists():
            _restore_database(database, backup)
        # Remove obsolete sources, keeping only this site's persistent database.
        for child in app.iterdir():
            if child.name != 'data':
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        for name, content in files.items():
            file = app / name
            file.parent.mkdir(exist_ok=True)
            file.write_text(content, encoding='utf-8')
        shutil.copyfile(Path(__file__).with_name('generated_worker.py'), app / 'start.py')
        _checked_database(database)
        if database.exists():
            with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as source, sqlite3.connect(backup) as destination:
                source.backup(destination)
        state = {'slug': site.slug, 'version': str(site.version_id)}
        try:
            if settings.BRUNOHOST_ENGINE == 'sandbox':
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    port = probe.getsockname()[1]
                host = f'worker.localhost:{port}'
                interpreter = str(Path(sys.base_prefix) / 'bin' / f'python{sys.version_info.major}.{sys.version_info.minor}')
                profile = folder / 'sandbox.sb'
                profile.write_text(sandbox_profile(app, interpreter, port))
                command = ['/usr/bin/sandbox-exec', '-f', str(profile), interpreter, '-I', '-S', str(app / 'start.py'),
                           '--port', str(port), '--host', 'worker.localhost', '--version', str(site.version_id), '--project', site.slug]
                with (folder / 'worker.log').open('wb') as log:
                    process = subprocess.Popen(command, cwd=app,
                        env={'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                state.update(pid=process.pid, port=port, host=host)
            else:
                sockets = folder / 'socket'
                sockets.mkdir(exist_ok=True)
                (sockets / 'http.sock').unlink(missing_ok=True)
                name = 'brunohost-' + site.slug
                state.update(container=name, socket=str(sockets / 'http.sock'), host='worker:8099')
                command = ['docker', 'run', '--detach', '--rm', '--name', name, '--network', 'none',
                    '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                    '--memory=256m', '--memory-swap=256m', '--cpus=.5', '--pids-limit=32',
                    '--ulimit', 'nofile=64:64', '--ulimit', 'fsize=134217728:134217728',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--log-driver=none',
                    '--mount', f'type=bind,src={app.resolve()},dst=/app,readonly',
                    '--mount', f'type=bind,src={data.resolve()},dst=/app/data',
                    '--mount', f'type=bind,src={sockets.resolve()},dst=/socket',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                    settings.BRUNOHOST_DOCKER_IMAGE, 'python', '-I', '-S', '/app/start.py',
                    '--socket', '/socket/http.sock', '--host', 'worker', '--port', '8099',
                    '--version', str(site.version_id), '--project', site.slug]
                result = subprocess.run(command, capture_output=True, timeout=20)
                if result.returncode:
                    raise BuilderError('Не удалось запустить контейнер BrunoHost. Проверьте настройки исполнителя.')
            (folder / 'state.json').write_text(json.dumps(state))
            for _ in range(50):
                if healthy(state, site):
                    backup.unlink(missing_ok=True)
                    return state
                if settings.BRUNOHOST_ENGINE == 'sandbox' and process.poll() is not None:
                    break
                time.sleep(.1)
            raise BuilderError('Сайт не прошёл проверку запуска. Исправьте серверный код в лаборатории и повторите публикацию.')
        except Exception:
            # Persist state even if a container started but its health check failed.
            (folder / 'state.json').write_text(json.dumps(state))
            _stop(site)
            _restore_database(database, backup)
            raise

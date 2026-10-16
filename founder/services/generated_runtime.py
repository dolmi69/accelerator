"""Local model-code execution, confined by the macOS OS sandbox (fail closed)."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from founder.services.generated_backend import validate_files


def available():
    return sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file()


def _directory(startup_id):
    return settings.BASE_DIR / '.runtime' / 'lab-backends' / str(startup_id)


def _state(startup_id):
    try:
        value = json.loads((_directory(startup_id) / 'generated-state.json').read_text())
        if type(value.get('pid')) is int and value['pid'] > 1 and type(value.get('port')) is int and 1024 <= value['port'] <= 65535:
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _healthy(state, startup_id):
    if not state:
        return False
    try:
        response = httpx.get(f"http://127.0.0.1:{state['port']}/__lab_health",
            headers={'Host': f"{state['host']}:{state['port']}"}, timeout=.5, trust_env=False)
        return response.status_code == 200 and response.json() == {'pid': state['pid'], 'version_id': state['version_id'], 'project_id': str(startup_id)}
    except (httpx.HTTPError, ValueError, KeyError):
        return False


def status(startup_id):
    state = _state(startup_id)
    return state if _healthy(state, startup_id) else None


def stop_unlocked(startup_id):
    state = _state(startup_id)
    if state:
        # State is outside the child's sandbox. Also verify its command if the
        # app is stuck and cannot answer health requests; never kill reused PIDs.
        command = subprocess.run(['ps', '-p', str(state['pid']), '-o', 'command='], capture_output=True, text=True).stdout
        marker = str(_directory(startup_id) / 'generated-projects') + '/'
        if marker in command and 'start.py' in command:
            try:
                os.kill(state['pid'], signal.SIGTERM)
                for _ in range(20):
                    time.sleep(.05)
                    os.kill(state['pid'], 0)
                os.kill(state['pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass
    (_directory(startup_id) / 'generated-state.json').unlink(missing_ok=True)


def sandbox_profile(project, interpreter, port):
    """Allow only stdlib + this version's sources, writes to its data directory,
    and incoming loopback connections. Deny fork, signals, outbound networking,
    other projects, platform env/database, user's home and installed SDKs.
    """
    q = lambda path: json.dumps(str(path))
    project = project.resolve()
    prefix = Path(sys.base_prefix).resolve()
    return f'''(version 1)
(deny default)
(allow process-exec (subpath {q(prefix)}))
(allow file-read-metadata)
(allow file-read* (literal "/") (subpath {q(prefix)})
    (subpath "/System") (subpath "/usr/lib")
    (literal "/dev/urandom") (literal "/dev/null") (subpath {q(project)}))
(allow file-write* (subpath {q(project / 'data')}))
(allow sysctl-read)
(allow network-bind (local ip "localhost:{port}"))
(allow network-inbound (local ip "localhost:{port}"))
'''


def start(version, *, launcher_origin=None, return_path=None, selection_token=None, publication=False):
    from founder.services.django_builder import (BuilderError, RuntimeSelectionChanged,
        _runtime_lock, _active_project, _select_project, _stop, _directory as legacy_directory, _root)
    if not settings.LAB_BACKEND_RUNTIME_ENABLED or not available():
        raise BuilderError('Изолированный запуск бэкенда модели пока доступен на macOS. Исходники можно скачать для отдельного запуска.')
    files = validate_files(version.backend_files)
    origin = urlsplit(launcher_origin or '')
    if origin.scheme != 'http' or origin.hostname not in {'127.0.0.1', 'localhost'}:
        raise BuilderError('Бэкенд модели запускается только из локальной лаборатории.')
    # Different hosts, not just different ports: cookies of the platform must
    # never be sent to the generated app. The frontend also uses another origin.
    runtime_id = f'public-{version.startup_id}' if publication else version.startup_id
    host = f'lab-{version.pk}.localhost' if publication else ('localhost' if origin.hostname == '127.0.0.1' else '127.0.0.1')
    with _runtime_lock():
        if publication:
            pass  # Public viewing must never switch or stop the owner's editor.
        elif selection_token:
            selected = _active_project(version.startup.owner_id)
            if not selected or selected['project_id'] != str(version.startup_id) or selected['token'] != selection_token:
                raise RuntimeSelectionChanged('Предпросмотр закрыт: вы перешли в другой проект.')
        else:
            _select_project(version.startup)
        old = status(runtime_id)
        if old and old['version_id'] == str(version.pk) and old['host'] == host and old.get('parent') == launcher_origin:
            return f"http://{host}:{old['port']}/"
        stop_unlocked(runtime_id)
        if not publication:
            _stop(legacy_directory(runtime_id), version.startup_id)
        running = sum(bool(status(child.name)) for child in _root().iterdir() if child.is_dir())
        from founder.services.django_builder import runtime_status
        running += sum(bool(runtime_status(child.name)) for child in _root().iterdir() if child.is_dir())
        if running >= settings.LAB_BACKEND_MAX_RUNNING:
            raise BuilderError('Лаборатория занята другими сайтами. Остановите один и повторите запуск.')
        project = _directory(runtime_id) / 'generated-projects' / str(version.pk)
        project.mkdir(parents=True, exist_ok=True)
        data = project / 'data'
        data.mkdir(exist_ok=True)
        database = data / 'data.sqlite3'
        if not database.exists() and version.source_id and version.source.kind == 'generated':
            source_db = _directory(runtime_id) / 'generated-projects' / str(version.source_id) / 'data' / 'data.sqlite3'
            if source_db.is_file():
                with sqlite3.connect(f'{source_db.as_uri()}?mode=ro', uri=True) as source_connection, sqlite3.connect(database) as target:
                    source_connection.backup(target)
        for name, content in files.items():
            target = project / name
            target.parent.mkdir(exist_ok=True)
            target.write_text(content, encoding='utf-8')
        shutil.copyfile(Path(__file__).with_name('generated_worker.py'), project / 'start.py')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        interpreter = str(Path(sys.base_prefix) / 'bin' / f'python{sys.version_info.major}.{sys.version_info.minor}')
        profile = _directory(runtime_id) / 'generated.sb'
        profile.write_text(sandbox_profile(project, interpreter, port))
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        state = {'port': port, 'host': host, 'parent': launcher_origin, 'version_id': str(version.pk)}
        # A failed import may modify its database; preserve it for recovery.
        backup = _directory(runtime_id) / 'generated-start-backup.sqlite3'
        if database.exists():
            with sqlite3.connect(database) as source_connection, sqlite3.connect(backup) as target:
                source_connection.backup(target)
        with (_directory(runtime_id) / 'generated.log').open('wb') as log:
            process = subprocess.Popen(['/usr/bin/sandbox-exec', '-f', str(profile), interpreter, '-I', '-S',
                str(project / 'start.py'), '--port', str(port), '--host', host,
                '--version', str(version.pk), '--project', str(runtime_id), '--parent', launcher_origin],
                cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        state['pid'] = process.pid
        (_directory(runtime_id) / 'generated-state.json').write_text(json.dumps(state))
        for _ in range(50):
            if _healthy(state, runtime_id):
                backup.unlink(missing_ok=True)
                return f'http://{host}:{port}/'
            if process.poll() is not None:
                break
            time.sleep(.1)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        (_directory(runtime_id) / 'generated-state.json').unlink(missing_ok=True)
        if backup.exists():
            for suffix in ('', '-wal', '-shm', '-journal'):
                Path(str(database) + suffix).unlink(missing_ok=True)
            backup.replace(database)
        else:
            database.unlink(missing_ok=True)
        raise BuilderError('Серверный код сохранён, но не прошёл запуск. Проверьте generated.log или попросите Бруно исправить сервер. Автоматического платного повтора нет.')

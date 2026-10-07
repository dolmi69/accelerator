"""Export and run reviewed Django code; only prototype.html comes from the LLM.

This is a local development runtime, not an arbitrary-code execution service.
Child processes have a clean environment, their own DB/secret and unique cookies.
"""
from contextlib import contextmanager
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from django.conf import settings
from founder.models import LabSiteVersion
from founder.services.site_editor import theme_css
from founder.services.backend_modules import normalize_modules

BLUEPRINT = Path(__file__).resolve().parent.parent / "blueprints" / "django_basic"


class BuilderError(Exception):
    pass


def project_files(version):
    if version.kind != LabSiteVersion.Kind.DJANGO:
        raise BuilderError("Для этой версии ещё не подключена основа Django.")
    files = {}
    for path in sorted(BLUEPRINT.rglob("*")):
        relative = path.relative_to(BLUEPRINT)
        allowed = (relative.parts[0] in {'core','config','static','templates'} or relative.as_posix() in
            {'manage.py','start_local.py','requirements.txt','README.md','module_catalog.py','.env.example'})
        if allowed and path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            files[path.relative_to(BLUEPRINT).as_posix()] = path.read_bytes()
    files["prototype.html"] = version.html.encode("utf-8")
    presentation = getattr(version, "presentation", {})
    files["static/site-theme.css"] = theme_css(presentation).encode("utf-8")
    files["site.json"] = json.dumps({
        "name": (presentation.get("title") or version.startup.name) if isinstance(presentation, dict) else version.startup.name,
        "project_id": str(version.startup_id), "version_id": str(version.pk),
        'modules':normalize_modules(version.backend_modules),
    }, ensure_ascii=False, indent=2).encode("utf-8")
    return files


def export_project(version):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, content in project_files(version).items():
            archive.writestr("django_site/" + name, content)
    buffer.seek(0)
    return buffer


def _root():
    return settings.BASE_DIR / ".runtime" / "lab-backends"


def _directory(startup_id):
    return _root() / str(startup_id)


def _read_state(directory):
    try:
        data = json.loads((directory / "runtime.json").read_text())
        if (not isinstance(data, dict) or type(data.get("port")) is not int
                or not 1024 <= data["port"] <= 65535 or type(data.get("pid")) is not int
                or data["pid"] <= 1):
            return None
        return data
    except (OSError, ValueError):
        return None


def _healthy(state, project_id):
    if not state:
        return False
    try:
        response = httpx.get(f"http://127.0.0.1:{state['port']}/health/",
            headers={"Host": f"localhost:{state['port']}"}, timeout=0.5, trust_env=False)
        body = response.json()
        return (response.status_code == 200 and body.get("project_id") == str(project_id)
                and body.get("version_id") == state.get("version_id") and body.get("pid") == state.get("pid"))
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def runtime_status(startup_id):
    state = _read_state(_directory(startup_id))
    if _healthy(state, startup_id):
        return {**state, "url": f"http://localhost:{state['port']}/"}
    return None


@contextmanager
def _runtime_lock():
    # Both local and Telegram Django processes use the same lock and process cap.
    try:
        import fcntl
    except ImportError:
        raise BuilderError("Автозапуск доступен на Mac и Linux. Скачайте проект для запуска на Windows.") from None
    _root().mkdir(parents=True, exist_ok=True)
    with (_root() / "runtime.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BuilderError("Другое приложение сейчас запускается. Повторите через несколько секунд.") from None
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _stop(directory, startup_id):
    state = _read_state(directory)
    if _healthy(state, startup_id):
        # Never kill an unrelated process just because its PID was reused.
        try:
            os.kill(state["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                os.kill(state["pid"], 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
    (directory / "runtime.json").unlink(missing_ok=True)


def stop_runtime(startup_id):
    with _runtime_lock():
        _stop(_directory(startup_id), startup_id)


def _atomic_write(path, content):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def start_runtime(version, *, launcher_origin=None, return_path=None):
    if not settings.LAB_BACKEND_RUNTIME_ENABLED:
        raise BuilderError("Локальный автозапуск отключён. Скачайте Django-проект и запустите его отдельно.")
    files = project_files(version)
    fingerprint = hashlib.sha256()
    for name, content in sorted(files.items()):
        fingerprint.update(name.encode() + b"\0" + content + b"\0")
    build_hash = fingerprint.hexdigest()
    with _runtime_lock():
        directory = _directory(version.startup_id)
        project = directory / 'project'
        project.mkdir(parents=True, exist_ok=True)
        # Runtime-only navigation context is never included in downloaded ZIPs.
        # It may change without restarting the website or destroying sessions.
        origin = urlsplit(launcher_origin or '')
        if (origin.scheme in {'http', 'https'} and origin.hostname in {'localhost', '127.0.0.1'}
                and not origin.username and not origin.password and not origin.path and not origin.query
                and not origin.fragment and isinstance(return_path, str)
                and return_path.startswith(f'/startups/{version.startup_id}/lab/?version=')
                and not any(c in return_path for c in '\r\n\\')):
            _atomic_write(project / '.lab-viewer.json', json.dumps({
                'origin': launcher_origin, 'return_path': return_path,
            }).encode())
        state = _read_state(directory)
        if (_healthy(state, version.startup_id) and state["version_id"] == str(version.pk)
                and state.get("build_hash") == build_hash):
            return _open_url(state, directory / 'project')
        running = sum(bool(runtime_status(child.name)) for child in _root().iterdir()
                      if child.is_dir() and child != directory)
        if running >= settings.LAB_BACKEND_MAX_RUNNING:
            raise BuilderError("Одновременно можно запустить три сайта. Остановите один из них в его лаборатории.")
        _stop(directory, version.startup_id)
        for name, content in files.items():
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, content)
        # No inherited API keys, accelerator settings, PYTHONPATH or shared DB URL.
        env = {"PATH": os.defpath, "LANG": "en_US.UTF-8", "PYTHONNOUSERSITE": "1",
               "DJANGO_SETTINGS_MODULE": "config.settings", "DJANGO_DEBUG": "1", "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1"}
        try:
            with (directory / "server.log").open("ab") as log:
                subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], cwd=project,
                    env=env, stdout=log, stderr=log, check=True, timeout=30)
                subprocess.run([sys.executable,'manage.py','prepare_site'],cwd=project,
                    env=env,stdout=log,stderr=log,check=True,timeout=15)
                with socket.socket() as candidate:
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        candidate.bind(("127.0.0.1", state["port"] if state else 0))
                    except OSError:
                        candidate.bind(("127.0.0.1", 0))
                    port = candidate.getsockname()[1]
                process = subprocess.Popen([sys.executable, "-m", "daphne", "-b", "127.0.0.1",
                    "-p", str(port), "config.asgi:application"], cwd=project, env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            state = {"pid": process.pid, "port": port, "version_id": str(version.pk), "build_hash": build_hash}
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and process.poll() is None:
                if _healthy(state, version.startup_id):
                    _atomic_write(directory / "runtime.json", json.dumps(state).encode())
                    return _open_url(state,project)
                time.sleep(0.2)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            raise BuilderError("Сайт не успел запуститься. Подробности сохранены в локальном журнале лаборатории.")
        except (OSError, subprocess.SubprocessError):
            raise BuilderError("Не удалось запустить основу Django. Проверьте установку зависимостей проекта.") from None


def _open_url(state, project):
    # Only the authorized launcher receives this local one-time owner URL.
    path = project / '.owner-setup'
    suffix = 'setup/' + path.read_text().strip() + '/' if path.exists() else ''
    hostname = 'localhost'
    try:
        context = json.loads((project / '.lab-viewer.json').read_text())
        candidate = urlsplit(context.get('origin', '')).hostname
        if candidate in {'localhost', '127.0.0.1'}:
            # Same hostname as the laboratory keeps SameSite cookies working
            # inside its iframe. Cookie names are already isolated per project.
            hostname = candidate
    except (OSError, ValueError, AttributeError):
        pass
    return f"http://{hostname}:{state['port']}/" + suffix

"""Prepare the local BrunoHost database without overwriting existing SQLite data."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time

from dotenv import dotenv_values

ZERO_FRACTION = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.000Z")


class SetupInProgress(RuntimeError):
    pass


@contextmanager
def setup_lock(directory):
    lock = directory / "preparing"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise SetupInProgress("Другой запуск готовит локальную PostgreSQL.") from exc
    try:
        yield
    finally:
        lock.rmdir()


def docker(*args, env=None, sql=None):
    return subprocess.run(
        ["docker", *args], env=env, input=sql, text=True, capture_output=True, check=True, timeout=180,
    ).stdout.strip()


def database_url(base, directory, env):
    name = "brunolab-local-" + hashlib.sha256(str(base).encode()).hexdigest()[:16]
    password_file = directory / "runtime-password"
    container = docker("container", "ls", "-aq", "--filter", f"name=^/{name}$")
    volume = docker("volume", "ls", "-q", "--filter", f"name=^{name}$").splitlines()
    if (container or name in volume) and not password_file.exists():
        raise RuntimeError("Найдена существующая база без её локальных настроек.")
    if not password_file.exists():
        with os.fdopen(os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as handle:
            handle.write(secrets.token_hex(24))
    password = password_file.read_text(encoding="ascii").strip()

    if not container:
        if (directory / "ready").exists():
            raise RuntimeError("Контейнер локальной базы отсутствует. Восстановите его данные.")
        print("BrunoLab подготавливает локальную PostgreSQL…", flush=True)
        docker(
            "run", "-d", "--name", name, "-p", "127.0.0.1::5432",
            "--mount", f"type=volume,src={name},dst=/var/lib/postgresql/data",
            "-e", "POSTGRES_DB=brunolab", "-e", "POSTGRES_PASSWORD", "postgres:16",
            env={**env, "POSTGRES_PASSWORD": secrets.token_hex(24)},
        )
    elif docker("inspect", "-f", "{{.State.Running}}", name) != "true":
        docker("start", name)

    deadline = time.monotonic() + 45
    while True:
        try:
            # TCP becomes available after the image's temporary initialization server stops.
            docker("exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-d", "brunolab")
            break
        except subprocess.CalledProcessError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Локальная PostgreSQL не запустилась.") from None
            time.sleep(1)

    if not (directory / "ready").exists():
        docker(
            "exec", "-i", name, "psql", "-U", "postgres", "-d", "brunolab", "-v", "ON_ERROR_STOP=1",
            sql=(
                "SELECT 'CREATE ROLE brunolab LOGIN NOSUPERUSER NOBYPASSRLS' "
                "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='brunolab')\n"
                "\\gexec\n"
                f"ALTER ROLE brunolab PASSWORD '{password}';\n"
                "ALTER DATABASE brunolab OWNER TO brunolab;\n"
            ),
        )
    port = docker("port", name, "5432/tcp").splitlines()[0].rsplit(":", 1)[1]
    return f"postgresql://brunolab:{password}@127.0.0.1:{int(port)}/brunolab"


def manage(base, env, *args):
    subprocess.run([sys.executable, "manage.py", *args], cwd=base, env=env, check=True)


def prepare_copy(base, directory, env):
    source = base / "db.sqlite3"
    if not source.exists() or not source.stat().st_size:
        return
    snapshot = directory / "source.sqlite3"
    fixture = directory / "platform.json"
    imported = directory / "imported.json"
    with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as origin:
        with sqlite3.connect(snapshot) as backup:
            origin.backup(backup)
    source_env = {**env, "DATABASE_URL": f"sqlite:///{snapshot.as_posix()}"}
    manage(base, source_env, "migrate", "--noinput")
    args = ("dumpdata", "--all", "--natural-foreign", "--exclude", "contenttypes", "--exclude", "auth.permission")
    manage(base, source_env, *args, "--output", str(fixture))
    manage(base, env, "loaddata", str(fixture))
    manage(base, env, *args, "--output", str(imported))
    key = lambda row: (row["model"], str(row["pk"]))
    # Django can serialize the same timestamp as either 00Z or 00.000Z.
    before = sorted(json.loads(ZERO_FRACTION.sub(r"\1Z", fixture.read_text(encoding="utf-8"))), key=key)
    after = sorted(json.loads(ZERO_FRACTION.sub(r"\1Z", imported.read_text(encoding="utf-8"))), key=key)
    if before != after:
        raise RuntimeError("Проверка данных после переноса в PostgreSQL не прошла.")
    fixture.unlink()
    imported.unlink()


def local_database_env(base, env, port):
    config = {**dotenv_values(base / ".env"), **env}
    directory = base / ".runtime" / "local-postgres"
    ready = directory / "ready"
    configured_url = config.get("DATABASE_URL")
    if configured_url or config.get("BRUNO_LOCAL_SQLITE") == "1":
        if configured_url and configured_url.startswith(("postgres://", "postgresql://")):
            env = {"BRUNOHOST_SHARED_ENABLED": "1", **env}
        return env
    try:
        if not shutil.which("docker"):
            raise RuntimeError("Docker не найден.")
        docker("info", "--format", "{{.ServerVersion}}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with setup_lock(directory):
            target = {
                **env, "DATABASE_URL": database_url(base, directory, env),
                "BRUNOHOST_SHARED_ENABLED": "1",
                "BRUNOHOST_BASE_URL": config.get("BRUNOHOST_BASE_URL") or f"http://localhost:{port}",
            }
            manage(base, target, "migrate", "--noinput")
            manage(base, target, "check_shared_host")
            if not ready.exists():
                prepare_copy(base, directory, target)
                ready.touch()
            print("Общий BrunoHost включён. Локальная база PostgreSQL готова.", flush=True)
            return target
    except SetupInProgress:
        raise
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, RuntimeError) else "сбой запуска или переноса данных"
        if ready.exists():
            raise RuntimeError(f"Локальная PostgreSQL недоступна: {reason} Восстановите её и повторите запуск.") from None
        print(f"Общий BrunoHost не включён: {reason} BrunoLab запускается на прежней SQLite.", flush=True)
        return env
